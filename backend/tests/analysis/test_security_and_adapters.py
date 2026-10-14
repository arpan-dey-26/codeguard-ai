"""Phase 2 security invariants and language-adapter availability.

The security tests assert properties rather than assuming them, so they fail
loudly if someone later removes the control they protect.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from app.analysis.languages import build_registry
from app.analysis.languages.base import LanguageAdapter, ParseStatus
from app.analysis.source.models import SourceFile
from app.analysis.source.provider import _safe_repo_path
from app.core.errors import PermanentInputError

ANALYSIS_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "analysis"
HEAD = "f" * 40


# -- no execution of repository content -----------------------------------


def test_the_analysis_package_contains_no_execution_primitives() -> None:
    """The absolute rule: repository content is parsed, never run."""
    offenders: list[str] = []
    banned_calls = {"exec", "eval", "compile", "__import__"}
    banned_imports = {"subprocess", "pickle", "marshal", "shelve", "os.system", "pty"}
    for path in ANALYSIS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in banned_calls
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.func.id}()")
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}:{node.lineno} import {a.name}"
                    for a in node.names
                    if a.name.split(".")[0] in banned_imports
                ]
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in banned_imports
            ):
                offenders.append(f"{path.name}:{node.lineno} from {node.module}")
    assert offenders == [], f"Execution primitives in the analysis layer: {offenders}"


def test_no_repository_cloning_anywhere_in_the_analysis_layer() -> None:
    for path in ANALYSIS_DIR.rglob("*.py"):
        text = path.read_text()
        assert "git clone" not in text
        assert "GitPython" not in text


def test_dynamic_import_is_confined_to_a_fixed_grammar_allow_list() -> None:
    """`importlib` appears exactly once, and never with a computed name.

    Loading a Tree-sitter grammar package is a legitimate dynamic import.
    Importing anything derived from repository content is not, so the module
    names are asserted to come from the module-level constant.
    """
    from app.analysis.languages.treesitter import _GRAMMAR_MODULES

    users = [p for p in ANALYSIS_DIR.rglob("*.py") if "importlib" in p.read_text()]
    assert [p.name for p in users] == ["treesitter.py"]

    allowed = {name for names in _GRAMMAR_MODULES.values() for name in names}
    assert allowed == {
        "tree_sitter_python",
        "tree_sitter_javascript",
        "tree_sitter_typescript",
        "tree_sitter_java",
    }

    # The argument to import_module must be the loop variable over that
    # constant, never a value that could originate outside this module.
    tree = ast.parse((ANALYSIS_DIR / "languages" / "treesitter.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            argument = node.args[0]
            if isinstance(argument, ast.Constant):
                assert argument.value == "tree_sitter"
            else:
                assert isinstance(argument, ast.Name)
                assert argument.id == "module_name"


def test_no_ai_provider_is_reachable_from_the_analysis_layer() -> None:
    """Phase 2 makes zero AI calls. Asserted structurally, not promised."""
    ai_roots = {"google", "anthropic", "openai"}
    for path in ANALYSIS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] in ai_roots for a in node.names), path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in ai_roots, path
        assert "app.ai" not in path.read_text(), f"{path} reaches into the AI layer"


def test_no_github_comment_is_created_from_the_analysis_layer() -> None:
    for path in ANALYSIS_DIR.rglob("*.py"):
        text = path.read_text()
        assert "/reviews" not in text
        assert "create_review" not in text


# -- path handling --------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["../../etc/passwd", "/etc/passwd", "src/../../../secret", "a/./b", "a//b"]
)
def test_traversal_paths_are_refused_before_a_url_is_built(path: str) -> None:
    """Repository file names are attacker-controlled."""
    with pytest.raises(PermanentInputError):
        _safe_repo_path(path)


def test_ordinary_and_unicode_paths_are_percent_encoded() -> None:
    assert _safe_repo_path("src/app.py") == "src/app.py"
    assert _safe_repo_path("src/a b.py") == "src/a%20b.py"
    assert "%" in _safe_repo_path("src/计算.py")


# -- source text never leaks ----------------------------------------------


def test_parse_warnings_never_contain_source_text() -> None:
    from app.analysis.languages.python_adapter import PythonAdapter

    result = PythonAdapter().parse(
        SourceFile.from_text(
            path="a.py",
            head_sha=HEAD,
            text="def broken(:\n    API_KEY = 'sk-live-SECRET'\n",
        )
    )
    for warning in result.warnings:
        assert "sk-live-SECRET" not in warning


# -- adapters -------------------------------------------------------------


def test_all_four_languages_are_registered() -> None:
    registry = build_registry()
    assert set(registry.available_languages()) == {"python", "javascript", "typescript", "java"}


@pytest.mark.parametrize("language", ["python", "javascript", "typescript", "java"])
def test_every_adapter_satisfies_the_protocol(language: str) -> None:
    adapter = build_registry().for_language(language)
    assert adapter is not None
    assert isinstance(adapter, LanguageAdapter)


def test_python_is_always_available_because_ast_is_stdlib() -> None:
    assert build_registry().for_language("python").is_available is True


@pytest.mark.parametrize("language", ["javascript", "typescript", "java"])
def test_tree_sitter_adapters_report_availability_honestly(language: str) -> None:
    """When a grammar is missing the adapter must say so.

    Returning an empty declaration list instead would be indistinguishable
    from "this file has no functions" and would make every downstream mapping
    silently wrong.
    """
    adapter = build_registry().for_language(language)
    assert adapter is not None
    source = SourceFile.from_text(path=f"a.{language[:2]}", head_sha=HEAD, text="function f() {}\n")
    result = adapter.parse(source)
    if adapter.is_available:
        assert result.status in (ParseStatus.OK, ParseStatus.OK_WITH_ERRORS)
        assert result.parser_backend == "tree-sitter"
    else:
        assert result.status is ParseStatus.PARSER_UNAVAILABLE
        assert result.warnings
        assert result.declarations == ()


def test_adapter_availability_map_covers_every_language() -> None:
    availability = build_registry().availability()
    assert set(availability) == {"python", "javascript", "typescript", "java"}
    assert all(isinstance(v, bool) for v in availability.values())
