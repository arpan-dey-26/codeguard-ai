"""Deterministic, budgeted review context.

Turns the Phase 2 ``PullRequestAnalysis`` into the single text document the
model is allowed to see. Three properties matter, in this order:

**1. It is bounded.** ``ai_max_files_per_review``,
``ai_max_changed_lines_per_review``, ``ai_max_context_bytes`` and
``ai_max_estimated_input_tokens`` are all enforced here, before any provider is
touched. The failure this prevents is concrete: a 900-file dependency bump
becoming one enormous request that exhausts a free-tier quota, or is rejected
whole, and produces nothing. Bounded context degrades instead — fewer files,
honestly recorded.

**2. It is deterministic.** Same analysis and same configuration produce a
byte-identical document. Selection is by an explicit sort key, never by dict or
set iteration order, so a regression test on the rendered document is
meaningful and a re-review of an unchanged commit cannot silently drift.

**3. Every line number in it is real.** The document presents head-file content
with its **actual 1-based line numbers**, taken from ``SourceFile``. It never
renumbers, never presents a fragment starting at 1, and never shows a line the
file does not contain. This is what makes "the model invented a line number" a
detectable event rather than an indistinguishable one: ``presented_lines``
records exactly which lines were shown, and the validator rejects a citation
outside that set.

**What is deliberately excluded.** Whole files are never sent — only the
touched declarations and a small window around changed lines outside any
declaration (architecture.md §11.3). Non-authoritative files (truncated reads,
failed parses) are excluded entirely: their line numbers describe a file that
does not exist, so anchoring against them is unsound, not merely imprecise.

The rendered document is **untrusted data**. It is fenced by
``app.ai.prompt.wrap_untrusted_context`` before it reaches a provider, and
nothing in this module may be read as an instruction to the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.artifact import FileAnalysis, PullRequestAnalysis
from app.analysis.languages.base import Declaration, DeclarationKind
from app.analysis.mapping.mapper import touched_declarations

# Bytes per token, used only to *reject early*. Deliberately pessimistic: the
# real tokenizer is the provider's and we do not have it. Under-estimating the
# token count is the expensive mistake (a rejected request, a wasted quota
# unit); over-estimating merely trims a file we could have sent.
_BYTES_PER_TOKEN = 3.5

_MAX_EXCLUDED_LISTED = 50
"""How many excluded paths the manifest names before summarising the rest.

Bounded because the excluded list grows as the budget shrinks — see the comment
at the manifest. 50 is enough that an ordinary over-budget pull request is
listed in full, and small enough that the pathological one cannot double the
document."""

_MARKER_CHANGED = "+"
_MARKER_CONTEXT = " "

_CONTAINER_KINDS = frozenset(
    {
        DeclarationKind.CLASS,
        DeclarationKind.INTERFACE,
        DeclarationKind.ENUM,
        DeclarationKind.STRUCT,
    }
)
"""Kinds whose *body* is other declarations rather than the changed code.

Shown as a header only. Including a whole class because one of its twelve
methods changed would put the other eleven in front of the model — more tokens,
more surface for a finding about code the pull request did not touch.
"""

_HEADER_LINES = 2
"""How much of a container declaration to show: its own line, plus one.

Two lines catches a signature that wraps, and a docstring's opening line, which
together are enough to identify what the changed method belongs to.
"""


class ExclusionReason:
    """Why a file the pull request changed is not in the document.

    Strings rather than an enum because they are recorded on the review run and
    rendered in the UI; a closed enum would have to be migrated every time a
    new reason appears, and the value is descriptive, not dispatched on.
    """

    NOT_AUTHORITATIVE = "not_authoritative"
    NO_COMMENTABLE_LINES = "no_commentable_lines"
    FILE_BUDGET = "file_budget"
    BYTE_BUDGET = "byte_budget"
    CHANGED_LINE_BUDGET = "changed_line_budget"
    NO_SOURCE = "no_source"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """The ceilings, lifted out of ``Settings`` so this module is testable
    without an environment."""

    max_files: int = 40
    max_changed_lines: int = 3_000
    max_bytes: int = 180_000
    max_estimated_tokens: int = 60_000
    lines_around_declaration: int = 8
    max_lines_per_file: int = 600
    """Ceiling on presented lines for one file.

    A single 4,000-line generated file would otherwise consume the whole byte
    budget and crowd out every other file in the pull request. Reviewing four
    files partially is more useful than reviewing one file completely.
    """

    @staticmethod
    def from_settings(settings: Any) -> ContextBudget:
        return ContextBudget(
            max_files=settings.ai_max_files_per_review,
            max_changed_lines=settings.ai_max_changed_lines_per_review,
            max_bytes=settings.ai_max_context_bytes,
            max_estimated_tokens=settings.ai_max_estimated_input_tokens,
            lines_around_declaration=settings.ai_context_lines_around_declaration,
        )


@dataclass(frozen=True, slots=True)
class ExcludedFile:
    file_path: str
    reason: str
    changed_line_count: int = 0


@dataclass(frozen=True, slots=True)
class IncludedFile:
    file_path: str
    language: str | None
    change_type: str
    changed_line_count: int
    presented_line_count: int
    declaration_count: int
    partial: bool
    """True when the file's own line ceiling truncated what was shown.

    Recorded per file and surfaced on the review run, because "we reviewed this
    file" and "we reviewed part of this file" are different claims and only one
    of them is true here.
    """


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """The rendered document plus everything needed to audit it afterwards."""

    repository_full_name: str
    pull_request_number: int
    head_sha: str
    document: str
    included: tuple[IncludedFile, ...] = ()
    excluded: tuple[ExcludedFile, ...] = ()
    presented_lines: dict[str, frozenset[int]] = field(default_factory=dict)
    """path → the exact head-file line numbers shown to the model.

    The validator's "line was actually presented" gate reads this. It is a
    stricter test than "line exists in the file": a model citing a line it was
    never shown is guessing, and a guess that happens to land on a real line is
    still a guess.
    """

    estimated_tokens: int = 0
    byte_size: int = 0
    truncated: bool = False
    """True when any budget stopped the build before all files were included."""

    @property
    def is_empty(self) -> bool:
        return not self.included

    @property
    def coverage_ratio(self) -> float:
        total = len(self.included) + len(self.excluded)
        return len(self.included) / total if total else 0.0

    def summary(self) -> dict[str, Any]:
        """Non-secret, source-free summary. Safe to log and to persist."""
        return {
            "files_included": len(self.included),
            "files_excluded": len(self.excluded),
            "bytes": self.byte_size,
            "estimated_tokens": self.estimated_tokens,
            "truncated": self.truncated,
            "partial_files": sorted(f.file_path for f in self.included if f.partial),
            "exclusions": sorted({f"{f.file_path}:{f.reason}" for f in self.excluded}),
        }


# -- region selection --------------------------------------------------------


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent 1-based inclusive ranges.

    Adjacent ranges are merged too (``(1, 4)`` and ``(5, 9)`` become
    ``(1, 9)``): emitting them separately would put a gap marker between two
    consecutive lines, which reads as missing code that is not missing.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def select_regions(
    analysis: FileAnalysis, *, budget: ContextBudget
) -> tuple[list[tuple[int, int]], bool]:
    """Which head-file line ranges to present for one file.

    Returns ``(regions, truncated)``.

    The rule (architecture.md §11.3): show the **whole** of every declaration
    the change touched, plus a small window around changed lines that sit
    outside any declaration. A diff fragment alone is not reviewable — whether
    an early ``return`` leaks a file handle is not decidable from the changed
    line, only from the function around it.

    Falls back to windows around changed lines when the file has no parse
    (a language with no adapter installed). That is a weaker context, honestly
    weaker, rather than no review of the file at all.
    """
    source = analysis.source
    line_map = analysis.line_map
    if source is None or line_map is None:
        return [], False

    pad = budget.lines_around_declaration
    changed = sorted(line_map.commentable_new_lines)
    if not changed:
        return [], False

    intervals: list[tuple[int, int]] = []
    covered: set[int] = set()

    if analysis.mapping is not None and analysis.declarations:
        for declaration in touched_declarations(analysis.declarations, analysis.mapping):
            if declaration.kind is DeclarationKind.MODULE:
                # The module declaration spans the whole file. Including it
                # would send the entire file, which is exactly the thing this
                # module exists to avoid.
                continue

            start = max(1, declaration.start_line)
            if declaration.kind in _CONTAINER_KINDS:
                # A class or interface is in the touched set whenever any
                # method inside it changed, and its span is the whole body.
                # Including that body would send every sibling method too — the
                # opposite of scoping to the change. Only the declaration's own
                # header is shown, which is what the model needs to know what
                # the changed method belongs to.
                end = min(source.line_count, declaration.start_line + _HEADER_LINES - 1)
            else:
                # A function or method is shown in full. A reviewer cannot judge
                # whether an early return leaks a file handle without seeing the
                # whole function (architecture.md §11.3).
                end = min(source.line_count, declaration.end_line)

            if start <= end:
                intervals.append((start, end))
                covered.update(range(start, end + 1))

    # Changed lines no declaration covers — imports, module constants, a file
    # with no parser — get a window instead.
    for line in changed:
        if line in covered:
            continue
        start = max(1, line - pad)
        end = min(source.line_count, line + pad)
        if start <= end:
            intervals.append((start, end))

    merged = _merge_intervals(intervals)

    total = sum(end - start + 1 for start, end in merged)
    if total <= budget.max_lines_per_file:
        return merged, False

    # Over the per-file ceiling. Keep whole regions in file order until the
    # ceiling is reached rather than slicing one in half: half a function is
    # the misleading context this whole module is trying not to produce.
    kept: list[tuple[int, int]] = []
    used = 0
    for start, end in merged:
        span = end - start + 1
        if used + span > budget.max_lines_per_file:
            continue
        kept.append((start, end))
        used += span
    return kept, True


def _render_declaration_index(declarations: tuple[Declaration, ...]) -> list[str]:
    lines = []
    for declaration in sorted(declarations, key=Declaration.sort_key):
        if declaration.kind is DeclarationKind.MODULE:
            continue
        lines.append(
            f"  {declaration.kind.value} {declaration.qualified_name} "
            f"(lines {declaration.start_line}-{declaration.end_line})"
        )
    return lines


def render_file_block(
    analysis: FileAnalysis,
    regions: list[tuple[int, int]],
    *,
    partial: bool,
) -> tuple[str, frozenset[int]]:
    """Render one file, returning ``(text, presented_line_numbers)``.

    Line format is fixed and documented to the model in the system prompt::

        <number> <marker> | <verbatim file content>

    ``marker`` is ``+`` when the pull request added or modified that line and a
    space otherwise. The marker sits **before** the pipe so that everything
    after ``"| "`` is byte-exact file content — which is what makes
    ``anchor_snippet`` verifiable by a literal comparison against
    ``SourceFile.line()``.
    """
    source = analysis.source
    line_map = analysis.line_map
    assert source is not None and line_map is not None  # guarded by caller

    out: list[str] = []
    presented: set[int] = set()

    out.append(f"FILE: {analysis.file_path}")
    out.append(
        f"  change: {analysis.change_type.value}"
        f" | language: {analysis.language or 'unknown'}"
        f" | lines: {source.line_count}"
        f" | changed: {analysis.changed_line_count}"
    )
    if analysis.old_path and analysis.old_path != analysis.file_path:
        out.append(f"  renamed_from: {analysis.old_path}")
    if partial:
        out.append("  note: only part of this file is shown")

    index = _render_declaration_index(analysis.declarations)
    if index:
        out.append("  declarations:")
        out.extend(index)

    out.append("  content:")
    previous_end: int | None = None
    for start, end in regions:
        if previous_end is not None and start > previous_end + 1:
            out.append(f"    ... lines {previous_end + 1}-{start - 1} omitted ...")
        for number in range(start, end + 1):
            text = source.line(number)
            if text is None:
                continue
            marker = _MARKER_CHANGED if line_map.is_changed(number) else _MARKER_CONTEXT
            out.append(f"{number:>6} {marker} | {text}")
            presented.add(number)
        previous_end = end

    return "\n".join(out), frozenset(presented)


# -- the builder -------------------------------------------------------------


def _selection_key(analysis: FileAnalysis) -> tuple[int, str]:
    """Rank files for inclusion when the budget cannot hold them all.

    Most changed lines first, path ascending as the tie-break. Both components
    are properties of the analysis, so the ranking is total and stable — the
    same pull request never produces two different documents.

    The justification for "most changed lines first" is blunt: when something
    must be dropped, drop the file the pull request barely touched.
    """
    return (-analysis.changed_line_count, analysis.file_path)


def build_review_context(
    analysis: PullRequestAnalysis,
    *,
    budget: ContextBudget,
) -> ReviewContext:
    """Assemble the bounded review document."""
    header = [
        "PULL REQUEST UNDER REVIEW",
        f"repository: {analysis.repository_full_name}",
        f"pull_request: #{analysis.pull_request_number}",
        f"head_commit: {analysis.head_sha}",
        f"changed_files_total: {analysis.total_files}",
    ]
    if analysis.files_truncated_by_github:
        header.append("note: GitHub truncated the changed-file list for this PR")

    included: list[IncludedFile] = []
    excluded: list[ExcludedFile] = []
    presented_lines: dict[str, frozenset[int]] = {}
    blocks: list[str] = []

    byte_total = sum(len(line) + 1 for line in header)
    changed_line_total = 0
    truncated = False

    # -- the manifest, reserved before anything is admitted ---------------
    #
    # **Phase 6.** ``byte_total`` used to count the header and the file blocks
    # and nothing else, so the manifest — two headings plus one line for every
    # file, included *and* excluded — was spent outside the budget it was meant
    # to be inside. The excluded half grows as the *inverse* of the budget: the
    # tighter the ceiling, the more files it excludes and the longer the list
    # naming them. A pull request near ``GITHUB_MAX_FILES_PER_PR`` (3000) would
    # have added roughly a hundred kilobytes of paths to a document the byte
    # ceiling had already declared full, and the provider would have rejected a
    # request that still cost a quota unit — the exact outcome
    # ``AI_MAX_CONTEXT_BYTES`` exists to prevent.
    #
    # Reserved up front rather than charged as files are decided, because by
    # the time a file is excluded the budget is already spent and there is no
    # way to un-admit the block that spent it. Every file costs exactly one
    # manifest line, so the reserve is exact up to the reason string, for which
    # the longest is assumed — erring toward a smaller document, which is the
    # safe direction for a ceiling.
    _LONGEST_REASON = max(
        value
        for key, value in vars(ExclusionReason).items()
        if not key.startswith("_") and isinstance(value, str)
    )

    def manifest_cost(path: str, reason: str = "") -> int:
        suffix = f" ({reason})" if reason else ""
        return len(f"  {path}{suffix}".encode()) + 1

    byte_total += 120  # the two headings and the blank lines around them
    _listable = budget.max_files + _MAX_EXCLUDED_LISTED
    for index, candidate in enumerate(sorted(analysis.files, key=lambda f: f.file_path)):
        if index >= _listable:
            byte_total += 80  # the "... and N more" line, charged once
            break
        byte_total += manifest_cost(candidate.file_path, _LONGEST_REASON)

    def _record_exclusion(file_analysis: FileAnalysis, reason: str) -> None:
        excluded.append(
            ExcludedFile(file_analysis.file_path, reason, file_analysis.changed_line_count)
        )

    candidates: list[FileAnalysis] = []
    for file_analysis in sorted(analysis.files, key=lambda f: f.file_path):
        if not file_analysis.is_authoritative:
            _record_exclusion(
                file_analysis,
                ExclusionReason.NOT_AUTHORITATIVE
                if file_analysis.source is not None
                else ExclusionReason.NO_SOURCE,
            )
            continue
        if file_analysis.line_map is None or not file_analysis.line_map.commentable_new_lines:
            _record_exclusion(file_analysis, ExclusionReason.NO_COMMENTABLE_LINES)
            continue
        candidates.append(file_analysis)

    ranked = sorted(candidates, key=_selection_key)
    chosen: list[tuple[FileAnalysis, list[tuple[int, int]], bool, str]] = []

    for file_analysis in ranked:
        if len(chosen) >= budget.max_files:
            _record_exclusion(file_analysis, ExclusionReason.FILE_BUDGET)
            truncated = True
            continue

        if changed_line_total + file_analysis.changed_line_count > budget.max_changed_lines:
            _record_exclusion(file_analysis, ExclusionReason.CHANGED_LINE_BUDGET)
            truncated = True
            continue

        regions, region_truncated = select_regions(file_analysis, budget=budget)
        if not regions:
            _record_exclusion(file_analysis, ExclusionReason.NO_COMMENTABLE_LINES)
            continue

        block, lines_shown = render_file_block(file_analysis, regions, partial=region_truncated)
        block_bytes = len(block.encode("utf-8")) + 2
        if byte_total + block_bytes > budget.max_bytes:
            _record_exclusion(file_analysis, ExclusionReason.BYTE_BUDGET)
            truncated = True
            continue

        byte_total += block_bytes
        changed_line_total += file_analysis.changed_line_count
        presented_lines[file_analysis.file_path] = lines_shown
        chosen.append((file_analysis, regions, region_truncated, block))
        included.append(
            IncludedFile(
                file_path=file_analysis.file_path,
                language=file_analysis.language,
                change_type=file_analysis.change_type.value,
                changed_line_count=file_analysis.changed_line_count,
                presented_line_count=len(lines_shown),
                declaration_count=len(file_analysis.declarations),
                partial=region_truncated,
            )
        )

    # Emit in path order regardless of the order files were *selected* in:
    # selection order is a budgeting artefact, and letting it reach the
    # document would make the output depend on changed-line counts.
    chosen.sort(key=lambda entry: entry[0].file_path)
    included.sort(key=lambda entry: entry.file_path)
    excluded.sort(key=lambda entry: (entry.file_path, entry.reason))

    manifest = ["", "FILES IN THIS REVIEW (use these paths exactly):"]
    manifest.extend(f"  {entry.file_path}" for entry in included)
    if excluded:
        manifest.append("")
        manifest.append("FILES CHANGED BUT NOT SHOWN (do not report findings on these):")
        # **Bounded in Phase 6.** The included list is capped by
        # ``max_files``; this one was not, and it grows as the *inverse* of the
        # budget — the more files the byte ceiling excludes, the longer the
        # manifest listing them. None of it was counted against
        # ``byte_total``, which accumulates file blocks only, so a pull request
        # near ``GITHUB_MAX_FILES_PER_PR`` (3000) would have added roughly a
        # hundred kilobytes of paths to a document the byte ceiling had already
        # declared full. The provider would then reject a request that cost a
        # quota unit — the exact outcome ``AI_MAX_CONTEXT_BYTES`` exists to
        # prevent.
        #
        # Truncating this list costs nothing in safety: it is advisory to the
        # model, and a finding on a path we did not show is refused by gate 4
        # of the validator whether or not the path was named here.
        shown = excluded[:_MAX_EXCLUDED_LISTED]
        manifest.extend(f"  {entry.file_path} ({entry.reason})" for entry in shown)
        if len(excluded) > len(shown):
            manifest.append(
                f"  ... and {len(excluded) - len(shown)} further changed files "
                "not shown in this review"
            )

    blocks = [entry[3] for entry in chosen]
    document = (
        "\n".join(header + manifest + [""] + blocks) if blocks else "\n".join(header + manifest)
    )

    byte_size = len(document.encode("utf-8"))
    estimated_tokens = int(byte_size / _BYTES_PER_TOKEN) + 1

    if estimated_tokens > budget.max_estimated_tokens:
        # Reached only when a single file's block is itself over the token
        # ceiling while under the byte ceiling — possible with dense non-ASCII
        # source. Recorded rather than silently sent: the provider would
        # reject it anyway, and a rejected request costs a quota unit.
        truncated = True

    return ReviewContext(
        repository_full_name=analysis.repository_full_name,
        pull_request_number=analysis.pull_request_number,
        head_sha=analysis.head_sha,
        document=document,
        included=tuple(included),
        excluded=tuple(excluded),
        presented_lines=presented_lines,
        estimated_tokens=estimated_tokens,
        byte_size=byte_size,
        truncated=truncated,
    )


__all__ = [
    "ContextBudget",
    "ExcludedFile",
    "ExclusionReason",
    "IncludedFile",
    "ReviewContext",
    "build_review_context",
    "render_file_block",
    "select_regions",
]
