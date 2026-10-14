"""Changed lines → AST declarations.

The algorithm that turns a diff into something a reviewer — human or model —
can reason about:

    changed line 47
        ↓  innermost declaration containing it
    method calculate_total()
        ↓  parent chain
    class Invoice
        ↓
    <module>

**Innermost wins, and ties are broken deterministically.** Two declarations can
both contain a line; the correct answer is the tightest one, because a nested
helper is a more useful context than the 400-line class around it. When spans
are genuinely identical — which a broken parse can produce — the tie is broken
by a fixed rule (later start, then earlier end, then qualified name) rather
than by whichever the parser happened to emit first. An arbitrary choice would
make the artifact non-deterministic, which §18 forbids.

**Lines outside every declaration stay outside.** A changed import or a
module-level constant maps to the module declaration, which is real, rather
than being forced into the nearest function, which would be a lie.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.diff.models import ChangedLine, LineMap
from app.analysis.languages.base import Declaration, DeclarationKind


@dataclass(frozen=True, slots=True)
class LineMapping:
    """Where one changed line sits in the file's structure.

    ``declaration_chain`` runs outermost → innermost, so
    ``chain[-1] is innermost`` and ``chain[0]`` is the module. Both ends are
    useful: the innermost is the review context, the chain is how a finding
    gets described as "``Invoice.calculate_total``".
    """

    line: int
    side: str
    change_type: str
    innermost_declaration_id: str | None
    declaration_chain: tuple[str, ...] = ()
    qualified_name: str = ""
    kind: str = ""

    @property
    def is_inside_declaration(self) -> bool:
        """True when the line sits in something narrower than the module."""
        return len(self.declaration_chain) > 1


@dataclass(frozen=True, slots=True)
class FileMapping:
    """Every changed line in one file, mapped."""

    file_path: str
    head_sha: str
    mappings: tuple[LineMapping, ...] = ()
    unmapped_lines: tuple[int, ...] = ()
    """Changed lines that fall outside even the module declaration.

    Normally empty. Non-empty means the parse produced a module span shorter
    than the file — a truncated read, or a parser that stopped early — and that
    is worth surfacing rather than hiding, because it means the structural
    picture is incomplete.
    """

    touched_declaration_ids: tuple[str, ...] = ()

    def for_line(self, line: int) -> LineMapping | None:
        for mapping in self.mappings:
            if mapping.line == line:
                return mapping
        return None


def _containment_key(declaration: Declaration) -> tuple[int, int, str]:
    """Order candidates innermost-first, deterministically.

    Later start beats earlier start; on a tie, earlier end (the tighter span)
    wins; on a further tie, qualified name. Every component is a property of
    the declaration itself, so the result never depends on parse order.
    """
    return (-declaration.start_line, declaration.end_line, declaration.qualified_name)


def find_containing_declarations(
    declarations: tuple[Declaration, ...], line: int
) -> tuple[Declaration, ...]:
    """Every declaration containing ``line``, outermost → innermost."""
    containing = [d for d in declarations if d.contains_line(line)]
    containing.sort(key=_containment_key, reverse=True)
    return tuple(containing)


def map_line(
    declarations: tuple[Declaration, ...], line: int
) -> tuple[Declaration | None, tuple[Declaration, ...]]:
    """Return ``(innermost, chain)`` for one line."""
    chain = find_containing_declarations(declarations, line)
    return (chain[-1] if chain else None, chain)


def map_changed_lines(
    *,
    line_map: LineMap,
    declarations: tuple[Declaration, ...],
    include_deletions: bool = False,
) -> FileMapping:
    """Map a file's changed lines onto its declarations.

    Only post-image lines are mapped by default. A deleted line's number refers
    to the *pre-image*, and the declarations here were parsed from the head
    version — so looking a deleted line's old number up against head
    declarations would return a confidently wrong answer. Deletions are
    reachable through ``LineMap.deleted_old_lines`` when a caller genuinely
    wants them, and ``include_deletions`` exists only for a future phase that
    parses the base revision too.
    """
    mappings: list[LineMapping] = []
    unmapped: list[int] = []
    touched: set[str] = set()

    candidates: list[ChangedLine] = [
        entry
        for entry in line_map.changed_lines
        if entry.new_line is not None and entry.is_commentable
    ]
    if include_deletions:  # pragma: no cover — reserved for base-revision analysis
        candidates.extend(entry for entry in line_map.changed_lines if entry.new_line is None)

    seen: set[int] = set()
    for entry in candidates:
        line = entry.new_line
        if line is None or line in seen:
            continue
        seen.add(line)

        innermost, chain = map_line(declarations, line)
        if innermost is None:
            unmapped.append(line)
            continue

        touched.update(d.declaration_id for d in chain)
        mappings.append(
            LineMapping(
                line=line,
                side=entry.side.value,
                change_type=entry.change_type.value,
                innermost_declaration_id=innermost.declaration_id,
                declaration_chain=tuple(d.declaration_id for d in chain),
                qualified_name=innermost.qualified_name,
                kind=innermost.kind.value,
            )
        )

    mappings.sort(key=lambda m: m.line)
    return FileMapping(
        file_path=line_map.file_path,
        head_sha=line_map.head_sha,
        mappings=tuple(mappings),
        unmapped_lines=tuple(sorted(unmapped)),
        touched_declaration_ids=tuple(sorted(touched)),
    )


def touched_declarations(
    declarations: tuple[Declaration, ...], mapping: FileMapping
) -> tuple[Declaration, ...]:
    """The declarations a change actually reaches, in file order.

    This is what a later phase sends to a model as context: the *whole* of each
    touched function, rather than an orphaned diff fragment. A reviewer cannot
    judge whether an early return leaks a file handle without seeing the
    function around it.
    """
    wanted = set(mapping.touched_declaration_ids)
    found = [d for d in declarations if d.declaration_id in wanted]
    found.sort(key=Declaration.sort_key)
    return tuple(found)


def innermost_non_module(declarations: tuple[Declaration, ...], line: int) -> Declaration | None:
    """The tightest declaration that is not the whole module.

    ``None`` for a line that genuinely sits at module level — an import, a
    constant — which is a real answer and must not be replaced with a fake
    enclosing function.
    """
    _, chain = map_line(declarations, line)
    for declaration in reversed(chain):
        if declaration.kind is not DeclarationKind.MODULE:
            return declaration
    return None


__all__ = [
    "FileMapping",
    "LineMapping",
    "find_containing_declarations",
    "innermost_non_module",
    "map_changed_lines",
    "map_line",
    "touched_declarations",
]
