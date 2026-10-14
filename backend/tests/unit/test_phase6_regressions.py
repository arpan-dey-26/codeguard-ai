"""One test per defect the Phase 6 verification found.

Same bar as ``test_phase5_regressions.py``: each of these fails against the
code as it stood at the end of Phase 5. A regression test that would have
passed before the fix is not evidence of anything.

Several fixes live in SQL, and SQLAlchemy is not installable in this
environment, so those are asserted against the statement text parsed out of the
module. That is not a weaker check — the property *is* a property of the
statement — but it is a different one from executing it, and the executing
version lives in the Phase 6 verification harness that runs against a real
PostgreSQL. Both are recorded in ``docs/final-verification-matrix.md``.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
APP_DIR = BACKEND / "app"


def _source(relative: str) -> str:
    return (APP_DIR / relative).read_text()


def _code_without_docstrings(node: ast.AST) -> str:
    """Unparse a node with every docstring removed.

    These modules discuss their own defects at length, and the first version of
    the rate-limit test below matched ``raise RateLimitedError`` inside the very
    docstring explaining that it no longer raises. A test that reads prose
    asserts nothing about behaviour and gets "fixed" by rewording a comment.
    """
    stripped = ast.parse(ast.unparse(node))
    for item in ast.walk(stripped):
        if isinstance(item, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(item, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
                if not body:
                    body.append(ast.Pass())
    return ast.unparse(stripped)


def _function_source(relative: str, name: str) -> str:
    """One function's code, with docstrings removed.

    ``ast.unparse`` already drops ``#`` comments; docstrings survive it, and
    these modules explain their own defects at length — the P6-1 assertion below
    would otherwise be satisfied by the docstring that *describes* the guard
    rather than by the guard.
    """
    tree = ast.parse(_source(relative))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return _code_without_docstrings(node)
    raise AssertionError(f"{name} not found in {relative}")


def _normalise(sql: str) -> str:
    return " ".join(sql.split()).lower()


# -- P6-1: a superseded review run must not be resurrected -------------------


def test_finishing_a_review_run_cannot_reopen_a_closed_one():
    """Defect: ``finish`` updated ``WHERE id = :id`` with no status guard.

    The §14 interleaving — push A starts a run, push B supersedes it, worker 1
    finishes it anyway — moved the run back out of ``superseded`` and recorded
    it as a success. Two succeeded runs for one pull request at two commits, a
    duration measured across the supersession, and a success rate counting work
    whose output was discarded.

    Confirmed against a real PostgreSQL before the fix, and again after.
    """
    sql = _normalise(_function_source("db/repositories/reviews.py", "finish"))
    assert "status in ('queued', 'running')" in sql, (
        "finish() lost its open-run guard: a superseded run can be resurrected"
    )
    assert "returning status" in sql, "finish() must report the status the row actually settled on"


def test_the_service_reports_the_status_the_database_settled_on():
    """The guard alone is not enough: if the worker still returns the status it
    *asked* for, the API answers 'succeeded' for a run the database records as
    superseded, and the lie simply moves one layer up."""
    source = _function_source("review/service.py", "_settle")
    assert "settled != status" in source
    assert "return settled" in source

    for method in ("review_pull_request", "_record_publication", "_fail"):
        body = _function_source("review/service.py", method)
        assert "self._runs.finish(" not in body, (
            f"{method} calls finish() directly and would report a stale status; "
            "it must go through _settle"
        )


# -- P6-2: model prose must not forge a comment's identity -------------------


def test_model_authored_text_is_sanitised_before_it_reaches_a_comment_body():
    """Defect: ``render_comment_body`` interpolated the model's description,
    impact, suggested fix and title into the same string as the finding marker.

    A model emitting ``<!-- codeguard-finding:<other fingerprint> -->`` forged
    the comment's identity. Republication reads that marker back from GitHub to
    decide what has already been said, so the genuine finding owning the forged
    fingerprint was treated as already posted and silently suppressed on every
    later run — model output deciding which real findings a reviewer sees.

    The diff is attacker-controlled on a public pull request and the diff is
    what the model reads, so this was reachable, not hypothetical.
    """
    body = _function_source("review/publisher.py", "render_comment_body")
    for field in ("title", "description", "impact", "suggested_fix", "cwe_id"):
        assert re.search(rf"_sanitise_model_(text|line)\(finding\.{field}\)", body), (
            f"finding.{field} reaches the comment body unsanitised"
        )

    summary = _function_source("review/publisher.py", "render_review_body")
    assert "_sanitise_model_line(finding.title)" in summary, (
        "the review summary renders the model's title unsanitised"
    )


def test_the_marker_reader_prefers_our_own_marker():
    """Defence in depth, and the only thing that fixes comments already posted
    before the sanitiser shipped. Ours is always appended last."""
    source = _function_source("review/publisher.py", "extract_marker")
    assert "findall" in source and "[-1]" in source, (
        "extract_marker reads the first marker in the body, which the model can write"
    )


# -- P6-3: the redirect allowlist rejected control characters by accident ----


def test_the_login_redirect_rejects_every_control_character():
    """Defect: the check caught ``\\r\\n`` only because the injected header
    happened to contain a colon. A payload with a newline and no colon went
    into a ``Location`` header intact."""
    source = _function_source("api/v1/auth.py", "_safe_redirect_path")
    assert "ch <" in source and "x7f" in source.lower(), (
        "the redirect check no longer rejects control characters as a class"
    )


# -- P6-4: repository paths with backslash separators ------------------------


def test_the_repository_path_check_understands_both_separators():
    """Defect: ``..\\..\\win`` passed. The function already rejected a leading
    backslash and a ``C:\\`` prefix, so it clearly intended to understand
    Windows separators — and understanding them in two places out of three is
    the kind of half-applied rule that reads as a guarantee and is not one."""
    from_source = _function_source("review/validator.py", "is_safe_repository_path")
    assert "re.split" in from_source, "the path check splits on '/' only again"


# -- P6-5: the rate limiter answered 500 instead of 429 ----------------------


def test_the_rate_limiter_returns_a_response_rather_than_raising():
    """Defect: raising in middleware. Starlette's per-exception handlers live in
    ``ExceptionMiddleware``, which is *inside* the user middleware stack, so a
    throttled request reached ``ServerErrorMiddleware`` and was answered 500
    with no ``Retry-After``. Found by running the stack over real HTTP."""
    source = _function_source("core/middleware.py", "_too_many_requests")
    assert "JSONResponse" in source
    assert "Retry-After" in source

    # Scoped to RateLimitMiddleware, and read without docstrings: there are
    # three `dispatch` methods in this module, so matching the first one found
    # would assert nothing about this one — and the docstring above *quotes*
    # the old `raise` while explaining why it is gone.
    tree = ast.parse(_source("core/middleware.py"))
    classes = {
        node.name: _code_without_docstrings(node)
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "raise RateLimitedError" not in classes["RateLimitMiddleware"], (
        "the rate limiter raises again: the 429 would be rendered as a 500"
    )

    # The behavioural version of this assertion runs the real stack over HTTP.
    assert (BACKEND / "tests" / "security" / "test_middleware_over_http.py").exists()


# -- P6-6: health status literals were silently widened to str ---------------


@pytest.mark.parametrize(
    ("function", "annotation"),
    [
        ("health", "overall: HealthStatus"),
        ("_integration_states", "github_state: IntegrationState"),
    ],
)
def test_health_states_are_annotated_with_their_closed_vocabulary(function, annotation):
    """Defect: a ternary over two string literals infers ``str``, so the Literal
    the response model declares was enforced only by Pydantic at runtime. A typo
    would have surfaced as a 500 in production rather than a type error in CI."""
    assert annotation in _function_source("api/health.py", function)


# -- P6-7: async tests were recorded as passing without running --------------


def test_the_suite_refuses_to_report_an_unrun_async_test_as_passed():
    """Defect (in the test infrastructure): ``pytest-asyncio`` cannot be
    installed here, and 96 of this suite's tests outside ``tests/api`` and
    ``tests/integration`` are coroutines.

    **What actually happens, measured rather than assumed.** pytest 9.0.3 fails
    such a test with "You need to install a suitable plugin for your async
    framework" — it does *not* report it as passed. An earlier note in this
    phase claimed the opposite; running the current file against the Phase 5
    tree disproved it, and the claim is corrected here rather than carried
    forward. The consequence was 96 tests that could not run at all, not 96
    tests silently lying — bad enough, and a different thing.

    The fallback hook runs them on a fresh event loop when the real plugin is
    absent. It must not activate when the plugin *is* installed, or it would
    silently replace it.
    """
    conftest = (BACKEND / "tests" / "conftest.py").read_text()
    assert "pytest_pyfunc_call" in conftest
    assert "PYTEST_ASYNCIO_AVAILABLE" in conftest
    assert "if not PYTEST_ASYNCIO_AVAILABLE:" in conftest


async def test_an_async_test_in_this_suite_really_runs():
    """The direct proof of the above: if coroutine tests were still being
    skipped silently, this assertion would never execute and the test would
    still be reported as passed. The marker file makes the difference
    observable."""
    marker = []

    async def work():
        marker.append("ran")

    await work()
    assert marker == ["ran"]


# -- P6-8: the finding-status workflow is intentionally absent ---------------


def test_no_code_writes_a_finding_status_transition():
    """Not a defect — a **scope decision**, pinned so it cannot be misread.

    ``findings.status`` has four values (open, resolved, dismissed,
    false_positive), ``finding_events`` is a full audit trail with an
    ``actor_user_id`` and a ``source IN ('user','system')``, and the findings
    API accepts all four as a filter. Nothing writes any of it. Every finding is
    created ``open`` and stays there; ``finding_events`` is read by the detail
    endpoint and written by nobody.

    Phase 6 deliberately did **not** add the endpoints that would populate it.
    Triage is a product workflow — who may dismiss a finding, what a dismissal
    means on re-review, whether it is per-repository or per-fingerprint — and
    none of that is decided anywhere in this project's documentation. Building
    the mutation because the schema anticipates it would be adding a feature to
    make a table look used.

    What that costs, stated rather than hidden: any statistic derived from
    finding status is structurally zero, and this project must not present a
    false-positive rate. ``final_audit.py`` enforces the second half; this test
    enforces the first, so the claim in ``docs/phase-6-final-report.md`` stays
    true rather than becoming stale the moment someone adds a writer without
    updating the report.
    """
    writers = []
    for path in APP_DIR.rglob("*.py"):
        if path.name == "finding_event.py":
            continue  # the model definition itself
        text = path.read_text()
        if re.search(r"INSERT\s+INTO\s+finding_events", text, re.I):
            writers.append(str(path.relative_to(APP_DIR)))
        if re.search(r"UPDATE\s+findings\s+SET[^;]*\bstatus\b", text, re.I | re.S):
            writers.append(f"{path.relative_to(APP_DIR)} (status update)")

    assert writers == [], (
        "the finding-status workflow now has a writer, so the scope note in "
        f"docs/phase-6-final-report.md is out of date: {writers}"
    )


# -- P6-10: every setting is documented somewhere ---------------------------


def test_every_setting_is_documented_and_no_documented_key_is_stale():
    """Defect: 16 settings were documented nowhere.

    Among them `REVIEW_PUBLISH_ENABLED` — **the switch that stops CodeGuard
    writing to GitHub**. An operator who needed to pause publication during an
    incident had no documented way to find it, and a control nobody can find is
    not a control.

    Both directions are checked. A setting absent from both files is
    undiscoverable; a key in `.env.example` that is no longer a setting is worse
    than undiscoverable, because an operator sets it and nothing happens.
    """
    repo = BACKEND.parent
    env_example = (repo / ".env.example").read_text()
    deployment = (repo / "docs" / "deployment.md").read_text()

    env_keys = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_example, re.M))

    settings_tree = ast.parse(_source("core/config.py"))
    fields = {
        item.target.id.upper()
        for node in ast.walk(settings_tree)
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
        for item in node.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
        and not item.target.id.startswith("_")
    }
    assert fields, "no settings were found — the parser, not the config, is wrong"

    undocumented = sorted(f for f in fields if f not in env_keys and f not in deployment)
    assert undocumented == [], (
        "settings an operator cannot discover from .env.example or "
        f"docs/deployment.md: {undocumented}"
    )

    stale = sorted(env_keys - fields)
    assert stale == [], f".env.example offers keys the application does not read: {stale}"


# -- v0.6.1: the session probe reports the deployment's own open-reads mode --


def test_the_session_probe_reports_unauthenticated_reads_mode():
    """The one mode built for running this dashboard without OAuth was
    unreachable from its own frontend.

    `DASHBOARD_ALLOW_UNAUTHENTICATED_READS` makes `resolve_scope` return an
    unrestricted scope, so every dashboard endpoint serves data with no
    session. `/auth/session` did not mention it, so the SPA saw
    `authenticated: false`, showed a sign-in page, and never rendered a single
    dashboard page against a backend that was serving them.

    The field discloses nothing new — `/health` and `/api/v1/system/settings`
    already publish it — and grants nothing: the server reads the same setting
    itself when deciding what any query may return.
    """
    body = _function_source("api/v1/auth.py", "session_state")
    assert "unauthenticated_reads_enabled" in body
    assert "dashboard_allow_unauthenticated_reads" in body


def test_enabling_unauthenticated_reads_still_refuses_to_start_in_production():
    """The frontend now renders in this mode, which makes it more important,
    not less, that production cannot be in it."""
    validation = _function_source("core/config.py", "_fail_fast_on_production_gaps")
    assert "dashboard_allow_unauthenticated_reads" in validation, (
        "production no longer refuses to start with unauthenticated reads enabled"
    )
