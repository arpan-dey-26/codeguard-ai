"""Tree-sitter adapters for JavaScript, TypeScript and Java.

One generic walker driven by a per-language node-type table, rather than three
near-identical walkers. The grammars genuinely differ only in which node types
name which construct; the traversal, the naming and the parent-chain logic are
the same, and duplicating them three times would mean fixing every bug three
times.

Each adapter therefore contributes a table and nothing else. Adding Go or C#
means adding a table and a registry entry — no new traversal code.

**Availability is reported, never faked.** When a grammar is not installed the
adapter returns ``PARSER_UNAVAILABLE`` with the reason. It does not return an
empty declaration list, which would be indistinguishable from "this file has no
functions" and would quietly make every downstream mapping wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.languages import treesitter
from app.analysis.languages.base import (
    BaseAdapter,
    Declaration,
    DeclarationKind,
    ParseResult,
    ParseStatus,
)
from app.analysis.source.models import SourceFile

_MAX_DECLARATIONS = 5000

# Node types that introduce a naming scope but are not declarations
# themselves — a variable declarator wrapping an arrow function, for example.
_TRANSPARENT = frozenset(
    {
        "program",
        "statement_block",
        "class_body",
        "declaration_list",
        "block",
        "export_statement",
        "expression_statement",
        "lexical_declaration",
        "variable_declaration",
        "variable_declarator",
        "public_field_definition",
        "field_declaration",
        "interface_body",
        "enum_body",
        "object",
        "pair",
        "assignment_expression",
        "arguments",
        "call_expression",
        "return_statement",
        "if_statement",
        "for_statement",
        "while_statement",
        "try_statement",
        "catch_clause",
        "switch_statement",
        "switch_body",
        "switch_case",
        "parenthesized_expression",
        "array",
        "module",
        "internal_module",
        "namespace_declaration",
        "labeled_statement",
        "type_annotation",
        "constructor_body",
        "enum_declaration_list",
    }
)


@dataclass(frozen=True, slots=True)
class NodeKindTable:
    """Maps a grammar's node types onto normalised declaration kinds."""

    language: str
    extensions: frozenset[str]
    kinds: dict[str, DeclarationKind]
    class_like: frozenset[str]
    """Kinds whose direct function children are methods rather than functions."""


JAVASCRIPT = NodeKindTable(
    language="javascript",
    extensions=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    kinds={
        "function_declaration": DeclarationKind.FUNCTION,
        "function_expression": DeclarationKind.FUNCTION,
        "generator_function_declaration": DeclarationKind.FUNCTION,
        "arrow_function": DeclarationKind.ARROW_FUNCTION,
        "class_declaration": DeclarationKind.CLASS,
        "class": DeclarationKind.CLASS,
        "method_definition": DeclarationKind.METHOD,
    },
    class_like=frozenset({"class_declaration", "class"}),
)

TYPESCRIPT = NodeKindTable(
    language="typescript",
    extensions=frozenset({".ts", ".tsx", ".mts", ".cts"}),
    kinds={
        **JAVASCRIPT.kinds,
        "interface_declaration": DeclarationKind.INTERFACE,
        "type_alias_declaration": DeclarationKind.TYPE_ALIAS,
        "enum_declaration": DeclarationKind.ENUM,
        "abstract_class_declaration": DeclarationKind.CLASS,
        "method_signature": DeclarationKind.METHOD,
        "abstract_method_signature": DeclarationKind.METHOD,
        "function_signature": DeclarationKind.FUNCTION,
    },
    class_like=frozenset(
        {"class_declaration", "class", "abstract_class_declaration", "interface_declaration"}
    ),
)

JAVA = NodeKindTable(
    language="java",
    extensions=frozenset({".java"}),
    kinds={
        "class_declaration": DeclarationKind.CLASS,
        "interface_declaration": DeclarationKind.INTERFACE,
        "enum_declaration": DeclarationKind.ENUM,
        "record_declaration": DeclarationKind.STRUCT,
        "annotation_type_declaration": DeclarationKind.INTERFACE,
        "method_declaration": DeclarationKind.METHOD,
        "constructor_declaration": DeclarationKind.CONSTRUCTOR,
        "compact_constructor_declaration": DeclarationKind.CONSTRUCTOR,
    },
    class_like=frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
            "annotation_type_declaration",
        }
    ),
)


class TreeSitterAdapter(BaseAdapter):
    """A language adapter driven entirely by a ``NodeKindTable``."""

    def __init__(self, table: NodeKindTable) -> None:
        self._table = table
        self.language = table.language
        self.extensions = table.extensions

    @property
    def is_available(self) -> bool:
        return treesitter.is_available(self._table.language)

    def parse(self, source: SourceFile) -> ParseResult:
        parser = treesitter.get_parser(self._table.language)
        if parser is None:
            reason = (
                treesitter.unavailable_reason(self._table.language)
                or "tree-sitter grammar not installed"
            )
            # Not an empty result: "no parser" and "no declarations" are
            # different facts and must never look alike downstream.
            return ParseResult(
                language=self._table.language,
                status=ParseStatus.PARSER_UNAVAILABLE,
                warnings=(reason,),
                parser_backend="tree-sitter",
            )

        data = source.text.encode("utf-8")
        try:
            tree = parser.parse(data)
        except Exception as exc:
            return ParseResult(
                language=self._table.language,
                status=ParseStatus.FAILED,
                warnings=(f"tree-sitter parse failed: {type(exc).__name__}",),
                error_count=1,
                parser_backend="tree-sitter",
            )

        module = self._module_declaration(source)
        declarations: list[Declaration] = [module]
        self._visit(tree.root_node, source, data, module, "", declarations, False)

        error_count = treesitter.count_error_nodes(tree.root_node)
        return ParseResult(
            language=self._table.language,
            status=ParseStatus.OK_WITH_ERRORS if error_count else ParseStatus.OK,
            declarations=tuple(declarations),
            error_count=error_count,
            parser_backend="tree-sitter",
        )

    def _visit(
        self,
        node: object,
        source: SourceFile,
        data: bytes,
        parent: Declaration,
        prefix: str,
        out: list[Declaration],
        in_class: bool,
    ) -> None:
        for child in getattr(node, "children", ()):
            if len(out) >= _MAX_DECLARATIONS:
                return
            node_type = getattr(child, "type", "")
            kind = self._table.kinds.get(node_type)

            if kind is None:
                # Descend through wrappers; ignore anything else entirely.
                if node_type in _TRANSPARENT or not node_type.endswith("_declaration"):
                    self._visit(child, source, data, parent, prefix, out, in_class)
                continue

            if kind is DeclarationKind.FUNCTION and in_class:
                kind = DeclarationKind.METHOD

            name = self._name_of(child, data)
            if name == "constructor" and kind is DeclarationKind.METHOD:
                kind = DeclarationKind.CONSTRUCTOR

            decl = self._declaration(
                kind=kind,
                name=name,
                qualified_name=f"{prefix}{name}" if prefix else name,
                file_path=source.path,
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                start_column=child.start_point[1],
                end_column=child.end_point[1],
                parent_id=parent.declaration_id,
                modifiers=self._modifiers(child, data),
            )
            out.append(decl)
            self._visit(
                child,
                source,
                data,
                decl,
                f"{prefix}{name}." if prefix else f"{name}.",
                out,
                node_type in self._table.class_like,
            )

    def _name_of(self, node: object, data: bytes) -> str:
        """Best available name, falling back rather than failing.

        An arrow function assigned to a const has no name of its own; the
        grammar puts the name on the enclosing declarator. Walking up one level
        recovers it, and ``<anonymous>`` is an honest last resort.
        """
        for field in ("name", "declarator"):
            found = node.child_by_field_name(field)  # type: ignore[attr-defined]
            if found is not None:
                text = treesitter.node_text(found, data).strip()
                if text:
                    return text.split("(")[0].split("=")[0].split(":")[0].strip() or "<anonymous>"
        parent = getattr(node, "parent", None)
        if parent is not None and getattr(parent, "type", "") == "variable_declarator":
            found = parent.child_by_field_name("name")
            if found is not None:
                text = treesitter.node_text(found, data).strip()
                if text:
                    return text
        return "<anonymous>"

    def _modifiers(self, node: object, data: bytes) -> tuple[str, ...]:
        found: list[str] = []
        for child in getattr(node, "children", ()):
            node_type = getattr(child, "type", "")
            if node_type in ("modifiers", "accessibility_modifier"):
                text = treesitter.node_text(child, data).split()
                found.extend(t for t in text if t.isalpha())
            elif node_type in ("async", "static", "abstract", "export_statement"):
                found.append(node_type)
        return tuple(sorted(set(found)))


def javascript_adapter() -> TreeSitterAdapter:
    return TreeSitterAdapter(JAVASCRIPT)


def typescript_adapter() -> TreeSitterAdapter:
    return TreeSitterAdapter(TYPESCRIPT)


def java_adapter() -> TreeSitterAdapter:
    return TreeSitterAdapter(JAVA)


__all__ = [
    "JAVA",
    "JAVASCRIPT",
    "TYPESCRIPT",
    "NodeKindTable",
    "TreeSitterAdapter",
    "java_adapter",
    "javascript_adapter",
    "typescript_adapter",
]
