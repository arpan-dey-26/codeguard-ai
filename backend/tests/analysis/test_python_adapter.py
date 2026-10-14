"""Python declaration extraction via the native `ast` backend.

`ast` is in the standard library, so this suite runs in every environment —
including ones where no Tree-sitter grammar is installed.

`ast.parse` compiles to a syntax tree and does **not** execute the module.
`test_parsing_does_not_execute_code` asserts that rather than assuming it.
"""

from __future__ import annotations

import pytest
from app.analysis.languages.base import DeclarationKind, ParseStatus
from app.analysis.languages.python_adapter import PythonAdapter
from app.analysis.source.models import SourceFile

HEAD = "c" * 40
ADAPTER = PythonAdapter()


def parse(text: str, path: str = "m.py"):  # type: ignore[no-untyped-def]
    return ADAPTER.parse(SourceFile.from_text(path=path, head_sha=HEAD, text=text))


def names(result) -> dict[str, DeclarationKind]:  # type: ignore[no-untyped-def]
    return {d.qualified_name: d.kind for d in result.declarations}


def test_module_declaration_always_exists() -> None:
    """Module-level lines need a real home, not an invented function."""
    result = parse("import os\nX = 1\n")
    assert names(result)["<module>"] is DeclarationKind.MODULE


def test_function_and_signature() -> None:
    result = parse("def add(a, b=1, *args, **kw) -> int:\n    return a + b\n")
    decl = next(d for d in result.declarations if d.name == "add")
    assert decl.kind is DeclarationKind.FUNCTION
    assert decl.start_line == 1
    assert decl.end_line == 2
    assert "a" in decl.signature and "-> int" in decl.signature


def test_async_function_is_distinguished() -> None:
    result = parse("async def fetch(url):\n    return url\n")
    decl = next(d for d in result.declarations if d.name == "fetch")
    assert decl.kind is DeclarationKind.ASYNC_FUNCTION
    assert "async" in decl.modifiers


def test_class_methods_and_async_methods() -> None:
    result = parse(
        "class A:\n    def m(self):\n        return 1\n    async def am(self):\n        return 2\n"
    )
    found = names(result)
    assert found["A"] is DeclarationKind.CLASS
    assert found["A.m"] is DeclarationKind.METHOD
    assert found["A.am"] is DeclarationKind.METHOD


def test_nested_functions_are_extracted_with_qualified_names() -> None:
    result = parse(
        "def outer():\n"
        "    def inner():\n"
        "        def deepest():\n            return 3\n"
        "        return deepest\n"
        "    return inner\n"
    )
    found = names(result)
    assert "outer" in found
    assert "outer.inner" in found
    assert "outer.inner.deepest" in found


def test_decorators_are_part_of_the_declaration_span() -> None:
    """A change to `@app.route(...)` is a change to the function it decorates."""
    result = parse("@app.route('/x')\n@auth\ndef view():\n    return 1\n")
    decl = next(d for d in result.declarations if d.name == "view")
    assert decl.start_line == 1, "decorator lines must be inside the span"
    assert "@route" in decl.modifiers or "@auth" in decl.modifiers


def test_property_decorator_changes_the_kind() -> None:
    result = parse("class A:\n    @property\n    def v(self):\n        return 1\n")
    assert names(result)["A.v"] is DeclarationKind.PROPERTY


def test_unicode_identifiers_and_comments() -> None:
    result = parse("def 计算(值):\n    # café — naïve\n    return 值\n")
    assert "计算" in names(result)


def test_syntax_error_does_not_raise() -> None:
    """Pull requests contain half-written code constantly."""
    result = parse("def broken(:\n    pass\n")
    assert result.status in (ParseStatus.FAILED, ParseStatus.OK_WITH_ERRORS)
    assert result.warnings


def test_syntax_error_warning_excludes_the_offending_source_line() -> None:
    """Third-party source must not reach a log through an error message."""
    result = parse("def broken(:\n    SECRET = 'hunter2'\n")
    assert not any("hunter2" in w for w in result.warnings)


def test_empty_and_comment_only_files() -> None:
    assert parse("").ok
    assert parse("# just a comment\n").ok


def test_declaration_ids_are_deterministic() -> None:
    """Same snapshot, same ids — no UUIDs, no counters."""
    source = "class A:\n    def m(self):\n        return 1\n"
    first = {d.qualified_name: d.declaration_id for d in parse(source).declarations}
    second = {d.qualified_name: d.declaration_id for d in parse(source).declarations}
    assert first == second


def test_declaration_ids_differ_between_files() -> None:
    """Identity includes the path, so two files' declarations never collide."""
    a = {d.declaration_id for d in parse("def f():\n    pass\n", "a.py").declarations}
    b = {d.declaration_id for d in parse("def f():\n    pass\n", "b.py").declarations}
    assert not (a & b)


def test_parent_ids_form_a_chain() -> None:
    result = parse("class A:\n    def m(self):\n        pass\n")
    by_name = {d.qualified_name: d for d in result.declarations}
    assert by_name["A.m"].parent_id == by_name["A"].declaration_id
    assert by_name["A"].parent_id == by_name["<module>"].declaration_id
    assert by_name["<module>"].parent_id is None


def test_parsing_does_not_execute_code() -> None:
    """The security claim, asserted rather than assumed.

    If `ast.parse` executed the module, the sentinel file would be written and
    the exception would be raised.
    """
    import pathlib
    import tempfile

    sentinel = pathlib.Path(tempfile.gettempdir()) / "codeguard_must_not_exist.txt"
    sentinel.unlink(missing_ok=True)
    result = parse(
        "import pathlib, tempfile\n"
        "pathlib.Path(tempfile.gettempdir(), 'codeguard_must_not_exist.txt').write_text('x')\n"
        "raise RuntimeError('this must never run')\n"
    )
    assert result.ok
    assert not sentinel.exists(), "repository code was executed"


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    " + "x = 1\n    " * 500,
        "class A:\n" + "".join(f"    def m{i}(self): pass\n" for i in range(200)),
    ],
)
def test_large_files_parse_without_error(source: str) -> None:
    assert parse(source).ok
