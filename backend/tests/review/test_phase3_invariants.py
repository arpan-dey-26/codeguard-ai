"""The twelve Phase 3 security invariants, asserted against the source tree.

Some of these are behavioural and are covered by the other suites. The ones
here are *structural*: they are properties of the codebase that no single
function test can guarantee, because the failure mode is a future edit adding
a second path that bypasses the first.

Source-level assertions are blunt instruments and are used only where the
invariant is genuinely about code shape. Where a behavioural test is possible,
it lives in the suite for that component and is cross-referenced here.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest
from app.core.errors import ProviderBudgetExceededError

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"
BACKEND_ROOT = APP_DIR.parent


def _sources() -> list[tuple[pathlib.Path, str]]:
    return [(path, path.read_text()) for path in APP_DIR.rglob("*.py")]


def _code_only(source: str) -> str:
    """The module with its docstrings removed.

    Blunt text searches over a codebase that documents its own rules will
    always match the documentation. A module that *explains* why it never
    issues REQUEST_CHANGES must not be flagged for containing the words. The
    docstrings are stripped so the assertion is about behaviour, not prose.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


# -- 1. The model's output is never trusted ---------------------------------


def test_only_validated_findings_can_reach_github():
    """Invariant 1. The publisher accepts exactly one finding type."""
    import app.review.publisher as publisher

    tree = ast.parse(pathlib.Path(publisher.__file__).read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "ValidatedFinding" in imported
    assert "RawFinding" not in imported


def test_the_validator_is_the_only_producer_of_validated_findings():
    """A second construction site would be a second, unreviewed trust boundary."""
    offenders = []
    for path, source in _sources():
        if path.name in ("validator.py",):
            continue
        if "ValidatedFinding(" in source:
            offenders.append(str(path.relative_to(APP_DIR)))
    assert offenders == [], f"ValidatedFinding constructed outside the validator: {offenders}"


# -- 2-3. Secrets never leave the process -----------------------------------


@pytest.mark.parametrize(
    "secret_attr",
    ["gemini_api_key", "claude_api_key", "github_private_key", "github_webhook_secret"],
)
def test_secret_settings_are_secretstr(secret_attr):
    """Invariant 2. A plain ``str`` would print in any f-string or repr."""
    from app.core.config import Settings
    from pydantic import SecretStr

    annotation = Settings.model_fields[secret_attr].annotation
    assert SecretStr in getattr(annotation, "__args__", (annotation,))


def test_no_api_key_is_ever_placed_in_a_url():
    """Invariant 3. A key in a query string reaches logs and proxy records."""
    for path, source in _sources():
        assert "?key=" not in source, path
        assert "&key=" not in source, path
    gemini = (APP_DIR / "ai" / "providers" / "gemini.py").read_text()
    assert "x-goog-api-key" in gemini
    assert "?key=" not in gemini


def test_the_safe_summary_exposes_booleans_about_secrets_never_secrets():
    from app.core.config import Settings

    settings = Settings(
        app_env="test",
        gemini_api_key="leak-me-if-you-can",
        github_webhook_secret="also-leak-me",
    )
    rendered = str(settings.safe_summary())
    assert "leak-me-if-you-can" not in rendered
    assert "also-leak-me" not in rendered


# -- 4-5. Repository content is never stored or executed --------------------


def test_no_repository_is_ever_cloned_and_no_git_is_run():
    """Invariant 4."""
    for path, source in _sources():
        code = _code_only(source)
        assert "git clone" not in code, path
        assert "GitPython" not in code, path
        assert "subprocess" not in _imported_modules(source), path
        assert "subprocess" not in code, path


def test_no_repository_code_is_ever_executed():
    """Invariant 5. Parsing is not executing, and the difference is the
    difference between a code reviewer and a remote shell."""
    banned = {"eval", "exec", "compile"}
    offenders = []
    for path, source in _sources():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in banned
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.func.id}")
    assert offenders == [], offenders


def test_the_source_text_of_a_file_is_never_written_to_the_database():
    """Invariant 6. ``findings`` stores one quoted line, by design, and nothing
    larger; the patch and the file body stay out of PostgreSQL entirely."""
    reviews = (APP_DIR / "db" / "repositories" / "reviews.py").read_text()
    assert "context_document" not in reviews
    assert ".text" not in reviews.replace("sqlalchemy import text", "")
    changed_files = (APP_DIR / "db" / "models" / "changed_file.py").read_text()
    assert "patch_text" not in changed_files
    assert "has_patch" in changed_files


# -- 7-8. The AI is fenced --------------------------------------------------


def test_the_model_is_never_given_a_tool_or_a_credential():
    """Invariant 7. It returns data; it cannot act."""
    gemini = (APP_DIR / "ai" / "providers" / "gemini.py").read_text()
    assert '"tools"' not in gemini
    assert '"toolConfig"' not in gemini
    assert '"functionDeclarations"' not in gemini


def test_instructions_and_repository_content_are_structurally_separated():
    """Invariant 8. Asserted behaviourally in
    tests/review/test_gemini_provider.py; here only that the separation exists
    in the request builder at all."""
    gemini = (APP_DIR / "ai" / "providers" / "gemini.py").read_text()
    assert '"system_instruction"' in gemini
    assert "wrap_untrusted_context" in gemini


def test_the_prompt_never_promises_the_model_that_its_output_is_published():
    """A model told its findings go straight to a pull request behaves
    differently — and worse — than one told every claim is verified."""
    from app.ai.prompt import SYSTEM_PROMPT

    assert "verified" in SYSTEM_PROMPT.lower()
    assert "CLAIMS" in SYSTEM_PROMPT


# -- 9-10. Nothing unverified is published ----------------------------------


def test_the_database_forbids_an_inline_finding_without_a_verified_anchor():
    """Invariant 9. Enforced in the migration, not only in Python, so a
    forgotten branch in application code cannot bypass it."""
    migration = next(
        (BACKEND_ROOT / "alembic" / "versions").glob("*0003_phase3_review_entities.py")
    ).read_text()
    assert "inline_requires_verified_anchor" in migration
    assert "resolved_line IS NOT NULL AND anchor_verified" in migration


def test_the_app_never_issues_approve_or_request_changes():
    """Invariant 10. The event is a constant in the client, not a parameter."""
    client = (APP_DIR / "github" / "client.py").read_text()
    assert '"event": "COMMENT"' in client
    for path, source in _sources():
        code = _code_only(source)
        assert "REQUEST_CHANGES" not in code, path
        assert '"APPROVE"' not in code, path


# -- 11-12. Failure modes are bounded ---------------------------------------


def test_a_write_is_never_blindly_retried():
    """Invariant 11. A timed-out POST may already have taken effect."""
    client = (APP_DIR / "github" / "client.py").read_text()
    assert "AmbiguousWriteError" in client
    assert "_is_safe_to_retry" in client

    from app.core.errors import AmbiguousWriteError

    assert AmbiguousWriteError.retryable is False


def test_the_provider_call_budget_is_bounded_by_configuration():
    """Invariant 12. An unbounded retry loop against a paid API is an
    unbounded bill."""
    from app.core.config import Settings

    settings = Settings(app_env="test")
    assert settings.ai_max_requests_per_review >= 1
    assert settings.ai_max_context_bytes > 0
    assert settings.ai_max_findings <= 100

    # Phase 6: asserting the *value* of a setting proves only that Pydantic
    # parsed it. This invariant is about a bill, so what has to be true is that
    # something refuses the request that would exceed the budget. It does now;
    # it did not when this test was written, and the test passed anyway.
    # ``ai_max_retries`` used to be asserted here and was read by nothing at
    # all, so it was removed rather than left as a knob that does nothing.
    from app.ai.budget import ProviderCallBudget

    calls = 0

    class _Counting:
        async def review(self, request):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return "result"

    budget = ProviderCallBudget(_Counting(), limit=settings.ai_max_requests_per_review)

    async def _drain() -> None:
        for _ in range(settings.ai_max_requests_per_review):
            await budget.review(object())  # type: ignore[arg-type]
        with pytest.raises(ProviderBudgetExceededError):
            await budget.review(object())  # type: ignore[arg-type]

    asyncio.run(_drain())
    assert calls == settings.ai_max_requests_per_review, (
        "the call past the ceiling still reached the provider"
    )


def test_a_kill_switch_exists_that_stops_publication_without_stopping_analysis():
    from app.core.config import Settings

    settings = Settings(app_env="test", review_publish_enabled=False)
    assert settings.review_publish_enabled is False
    publisher = (APP_DIR / "review" / "publisher.py").read_text()
    assert "publish_enabled" in publisher


# -- provenance --------------------------------------------------------------


def test_every_review_run_records_which_prompt_produced_it():
    """Without it, comparing this month's false-positive rate against last
    month's compares two different experiments."""
    model = (APP_DIR / "db" / "models" / "review_run.py").read_text()
    assert "prompt_version" in model
    service = (APP_DIR / "review" / "service.py").read_text()
    assert "PROMPT_VERSION" in service
