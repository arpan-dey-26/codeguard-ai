"""Python adapter: native ``ast`` first, Tree-sitter as the fallback.

Python is the one language where the standard library gives us a *semantically
canonical* tree, so the preference order is the reverse of every other adapter:

1.  ``ast.parse`` — exact, no dependency, and it knows about decorators,
    ``async def``, comprehension scopes and type parameters without any
    grammar-node guesswork.
2.  Tree-sitter — used when ``ast.parse`` raises, because it is error-tolerant
    and still recovers declarations from a file that is mid-edit. Pull-request
    code is broken more often than not.

If neither is available the result is a structured failure, never an exception.

**``ast.parse`` compiles to a syntax tree; it does not execute the module.**
No import, no ``exec``, no ``compile(..., "exec")`` followed by a call. That
distinction is the entire security argument for using it, and it is precise:
parsing a file that contains ``os.system("rm -rf /")`` yields a Call node and
nothing happens.
"""

from __future__ import annotations

import ast
from typing import ClassVar

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


class PythonAdapter(BaseAdapter):
    language = "python"
    extensions = frozenset({".py", ".pyi"})

    @property
    def is_available(self) -> bool:
        # Always true: ``ast`` is in the standard library, so Python analysis
        # works in every deployment regardless of what else is installed.
        return True

    def parse(self, source: SourceFile) -> ParseResult:
        try:
            tree = ast.parse(source.text, filename=source.path)
        except SyntaxError as exc:
            return self._parse_with_treesitter(source, native_error=exc)
        except (ValueError, RecursionError, MemoryError) as exc:
            # ValueError covers source containing NUL bytes; RecursionError a
            # pathologically nested expression. Both are file problems, not
            # run problems.
            return self._parse_with_treesitter(source, native_error=exc)

        declarations = self._walk(tree, source)
        return ParseResult(
            language=self.language,
            status=ParseStatus.OK,
            declarations=tuple(declarations),
            parser_backend="python-ast",
        )

    # -- native ast --------------------------------------------------------
    def _walk(self, tree: ast.Module, source: SourceFile) -> list[Declaration]:
        module = self._module_declaration(source)
        declarations: list[Declaration] = [module]

        def visit(node: ast.AST, parent: Declaration, prefix: str) -> None:
            if len(declarations) >= _MAX_DECLARATIONS:
                return
            for child in ast.iter_child_nodes(node):
                decl = self._declaration_for(child, source, parent, prefix)
                if decl is None:
                    # Not a declaration itself, but may contain one — a
                    # function inside an `if TYPE_CHECKING:` block, say.
                    visit(child, parent, prefix)
                    continue
                declarations.append(decl)
                inner_prefix = f"{prefix}{decl.name}." if prefix else f"{decl.name}."
                visit(child, decl, inner_prefix)

        visit(tree, module, "")
        return declarations

    def _declaration_for(
        self, node: ast.AST, source: SourceFile, parent: Declaration, prefix: str
    ) -> Declaration | None:
        if isinstance(node, ast.ClassDef):
            kind = DeclarationKind.CLASS
        elif isinstance(node, ast.AsyncFunctionDef):
            kind = (
                DeclarationKind.METHOD
                if parent.kind is DeclarationKind.CLASS
                else DeclarationKind.ASYNC_FUNCTION
            )
        elif isinstance(node, ast.FunctionDef):
            kind = (
                DeclarationKind.METHOD
                if parent.kind is DeclarationKind.CLASS
                else DeclarationKind.FUNCTION
            )
        else:
            return None

        name = node.name
        start = node.lineno
        end = getattr(node, "end_lineno", None) or start

        # Decorators sit above `def` but belong to the declaration: a change to
        # `@app.route("/x")` is a change to the function it decorates, and
        # excluding them would map that line to the enclosing class instead.
        decorators = getattr(node, "decorator_list", [])
        if decorators:
            start = min(start, min(d.lineno for d in decorators))

        modifiers: list[str] = []
        if isinstance(node, ast.AsyncFunctionDef):
            modifiers.append("async")
        for dec in decorators:
            label = _decorator_name(dec)
            if label:
                modifiers.append(f"@{label}")
                if label in ("property", "cached_property"):
                    kind = DeclarationKind.PROPERTY
                elif label == "staticmethod":
                    modifiers.append("static")

        return self._declaration(
            kind=kind,
            name=name,
            qualified_name=f"{prefix}{name}" if prefix else name,
            file_path=source.path,
            start_line=start,
            end_line=end,
            start_column=node.col_offset,
            end_column=getattr(node, "end_col_offset", None),
            parent_id=parent.declaration_id,
            modifiers=tuple(modifiers),
            signature=_signature(node),
        )

    # -- tree-sitter fallback ---------------------------------------------
    def _parse_with_treesitter(self, source: SourceFile, *, native_error: Exception) -> ParseResult:
        detail = _error_detail(native_error)
        parser = treesitter.get_parser("python")
        if parser is None:
            # Native parsing failed and there is no fallback. A structured
            # failure for this file only; the run continues.
            return ParseResult(
                language=self.language,
                status=ParseStatus.FAILED,
                warnings=(f"python-ast: {detail}", "tree-sitter unavailable"),
                error_count=1,
                parser_backend="python-ast",
            )
        result = _TreeSitterPythonWalker(self).parse(source, parser)
        return ParseResult(
            language=result.language,
            status=ParseStatus.OK_WITH_ERRORS,
            declarations=result.declarations,
            error_count=max(1, result.error_count),
            warnings=(f"python-ast: {detail}", *result.warnings),
            parser_backend="tree-sitter",
        )


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(_expr_name(b) for b in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        args = [a.arg for a in node.args.posonlyargs + node.args.args]
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        args += [a.arg for a in node.args.kwonlyargs]
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        returns = f" -> {_expr_name(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({', '.join(args)}){returns}"
    return ""


def _expr_name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_expr_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Subscript):
        return f"{_expr_name(node.value)}[...]"
    return type(node).__name__


def _error_detail(exc: Exception) -> str:
    """A short, non-sensitive description.

    Deliberately excludes ``exc.text`` — that attribute is the offending
    *source line*, and putting third-party source into a warning that reaches
    logs is the leak this phase must not introduce.
    """
    if isinstance(exc, SyntaxError):
        return f"{exc.msg} at line {exc.lineno}"
    return type(exc).__name__


class _TreeSitterPythonWalker:
    """Recovers Python declarations from a Tree-sitter tree.

    Only reached when ``ast.parse`` failed, so it is walking a broken file by
    definition and must tolerate missing names and truncated spans.
    """

    _KINDS: ClassVar[dict[str, DeclarationKind | None]] = {
        "function_definition": DeclarationKind.FUNCTION,
        "class_definition": DeclarationKind.CLASS,
        "decorated_definition": None,
    }

    def __init__(self, adapter: PythonAdapter) -> None:
        self._adapter = adapter

    def parse(self, source: SourceFile, parser: object) -> ParseResult:
        data = source.text.encode("utf-8")
        tree = parser.parse(data)  # type: ignore[attr-defined]
        module = self._adapter._module_declaration(source)
        declarations: list[Declaration] = [module]
        self._visit(tree.root_node, source, data, module, "", declarations)
        return ParseResult(
            language="python",
            status=ParseStatus.OK_WITH_ERRORS,
            declarations=tuple(declarations),
            error_count=treesitter.count_error_nodes(tree.root_node),
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
    ) -> None:
        for child in getattr(node, "children", ()):
            node_type = getattr(child, "type", "")
            if node_type == "decorated_definition":
                self._visit(child, source, data, parent, prefix, out)
                continue
            kind = self._KINDS.get(node_type)
            if kind is None:
                self._visit(child, source, data, parent, prefix, out)
                continue
            if kind is DeclarationKind.FUNCTION and parent.kind is DeclarationKind.CLASS:
                kind = DeclarationKind.METHOD
            name_node = child.child_by_field_name("name")
            name = treesitter.node_text(name_node, data) if name_node else "<anonymous>"
            decl = self._adapter._declaration(
                kind=kind,
                name=name,
                qualified_name=f"{prefix}{name}" if prefix else name,
                file_path=source.path,
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                start_column=child.start_point[1],
                end_column=child.end_point[1],
                parent_id=parent.declaration_id,
            )
            out.append(decl)
            if len(out) >= _MAX_DECLARATIONS:
                return
            self._visit(
                child, source, data, decl, f"{prefix}{name}." if prefix else f"{name}.", out
            )


__all__ = ["PythonAdapter"]
