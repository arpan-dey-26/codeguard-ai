"""The declaration model and the language adapter contract.

                      LanguageAdapter            (Protocol)
                             │
      ┌──────────────┬───────┴───────┬──────────────┐
  Python         JavaScript     TypeScript        Java
  (native ast +   (Tree-sitter)  (Tree-sitter)  (Tree-sitter)
   Tree-sitter)

Adding a language means writing one adapter and adding one registry entry.
Nothing in the pipeline, the mapper or the diff layer changes — which is the
whole reason the seam exists (architecture.md NFR-M3).

Two properties the whole package depends on:

*   **Declaration identity is deterministic.** ``declaration_id`` is a hash of
    the file path, kind, qualified name and span — never a UUID, never a
    counter that depends on iteration order. The same snapshot always produces
    the same ids, so an artifact can be compared across runs and a later phase
    can reference a declaration by id without storing it.
*   **Parsing never executes anything.** Adapters build syntax trees from
    bytes. No import, no exec, no eval, no compile-and-run, no subprocess.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.analysis.source.models import SourceFile


class DeclarationKind(StrEnum):
    """Structural kinds, normalised across languages.

    A Java method and a Python method are the same *kind* even though the
    grammars name them differently. Normalising here is what lets the mapper,
    and every later consumer, stay language-agnostic.
    """

    MODULE = "module"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    STRUCT = "struct"
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    ARROW_FUNCTION = "arrow_function"
    PROPERTY = "property"
    TYPE_ALIAS = "type_alias"
    FIELD = "field"


class ParseStatus(StrEnum):
    """How a parse went. Every value is reportable and none is a silent skip."""

    OK = "ok"
    OK_WITH_ERRORS = "ok_with_errors"
    """A tree was produced but contains error nodes. Real pull requests
    contain half-written code constantly, so this is a normal outcome, not a
    failure — the declarations that *were* recovered remain usable."""

    FAILED = "failed"
    PARSER_UNAVAILABLE = "parser_unavailable"
    """No parser backend is installed for this language in this deployment.
    Distinct from FAILED: the code is fine, the environment is not, and
    conflating them would hide a deployment problem as a source problem."""

    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Declaration:
    """One structural element of a source file.

    Line numbers are 1-based and inclusive on both ends, matching every other
    line number in the system. Columns are 0-based because every parser emits
    them that way; mixing the two conventions is a reliable source of
    off-by-one bugs, so the difference is stated here rather than discovered.
    """

    declaration_id: str
    kind: DeclarationKind
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None
    parent_id: str | None = None
    modifiers: tuple[str, ...] = ()
    signature: str = ""

    @property
    def line_span(self) -> int:
        return self.end_line - self.start_line + 1

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    def sort_key(self) -> tuple[int, int, str]:
        """Total order for deterministic serialisation."""
        return (self.start_line, -self.end_line, self.qualified_name)


def make_declaration_id(
    *, file_path: str, kind: DeclarationKind, qualified_name: str, start_line: int, end_line: int
) -> str:
    """Stable identity for a declaration within one source snapshot.

    Content-addressed rather than generated: two runs over the same file must
    produce the same id, and a caller must be able to recompute one without
    having seen the original parse. The span is included because two
    same-named overloads in one file are genuinely different declarations.
    """
    material = f"{file_path}\x00{kind.value}\x00{qualified_name}\x00{start_line}\x00{end_line}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ParseResult:
    """What an adapter returns for one file.

    A result is always returned — never an exception for bad source. Malformed
    code is the normal case in a pull request, and one unparseable file must
    not prevent analysis of the others.
    """

    language: str
    status: ParseStatus
    declarations: tuple[Declaration, ...] = ()
    error_count: int = 0
    warnings: tuple[str, ...] = ()
    parser_backend: str = ""
    """Which backend produced this, e.g. ``python-ast`` or ``tree-sitter``.

    Recorded because the two are not equivalent, and an artifact that does not
    say which one ran cannot be compared honestly against another.
    """

    @property
    def ok(self) -> bool:
        return self.status in (ParseStatus.OK, ParseStatus.OK_WITH_ERRORS)

    def sorted_declarations(self) -> tuple[Declaration, ...]:
        return tuple(sorted(self.declarations, key=Declaration.sort_key))


@runtime_checkable
class LanguageAdapter(Protocol):
    """What the pipeline needs from a language.

    Structurally typed, so a test double satisfies it by shape — no
    inheritance, no import of production parsing code.
    """

    @property
    def language(self) -> str: ...

    @property
    def extensions(self) -> frozenset[str]: ...

    @property
    def is_available(self) -> bool:
        """Whether a parser backend is installed for this language here."""
        ...

    def parse(self, source: SourceFile) -> ParseResult:
        """Build declarations from source. Never raises on malformed input."""
        ...


class BaseAdapter:
    """Shared helpers. Subclasses provide only what genuinely differs."""

    language: str = ""
    extensions: frozenset[str] = frozenset()

    def _declaration(
        self,
        *,
        kind: DeclarationKind,
        name: str,
        qualified_name: str,
        file_path: str,
        start_line: int,
        end_line: int,
        start_column: int | None = None,
        end_column: int | None = None,
        parent_id: str | None = None,
        modifiers: tuple[str, ...] = (),
        signature: str = "",
    ) -> Declaration:
        return Declaration(
            declaration_id=make_declaration_id(
                file_path=file_path,
                kind=kind,
                qualified_name=qualified_name,
                start_line=start_line,
                end_line=end_line,
            ),
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            start_column=start_column,
            end_column=end_column,
            parent_id=parent_id,
            modifiers=modifiers,
            signature=signature,
        )

    def _module_declaration(self, source: SourceFile) -> Declaration:
        """Every file has an implicit module spanning the whole of it.

        It gives lines outside any function or class a real structural home,
        so the mapper never has to invent a fake enclosing function for a
        changed import or a module-level constant.
        """
        return self._declaration(
            kind=DeclarationKind.MODULE,
            name=source.path.rsplit("/", 1)[-1],
            qualified_name="<module>",
            file_path=source.path,
            start_line=1,
            end_line=max(1, source.line_count),
        )


@dataclass
class AdapterRegistry:
    """Maps a language name to its adapter."""

    _adapters: dict[str, LanguageAdapter] = field(default_factory=dict)

    def register(self, adapter: LanguageAdapter) -> None:
        self._adapters[adapter.language] = adapter

    def for_language(self, language: str) -> LanguageAdapter | None:
        return self._adapters.get(language)

    def available_languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def availability(self) -> dict[str, bool]:
        """Which languages have a working parser backend right now."""
        return {name: a.is_available for name, a in sorted(self._adapters.items())}


__all__ = [
    "AdapterRegistry",
    "BaseAdapter",
    "Declaration",
    "DeclarationKind",
    "LanguageAdapter",
    "ParseResult",
    "ParseStatus",
    "make_declaration_id",
]
