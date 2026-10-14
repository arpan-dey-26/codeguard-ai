"""One test per defect the Phase 5 audit found.

Each of these fails against the code as it stood at the end of Phase 4. That is
the bar: a regression test that would have passed before the fix is not
evidence of anything. Where a defect cannot be reproduced without a database or
an event loop, the test asserts the *structural* property that the fix
introduced and says so explicitly, rather than pretending to more than it has.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"


def _source(relative: str) -> str:
    return (APP_DIR / relative).read_text()


def _function_source(relative: str, name: str) -> str:
    """The source of one function, by name, without importing its module.

    Several of these modules import SQLAlchemy, which is not installable in
    this environment. The properties being asserted are properties of the
    statement text, so parsing is not a weaker check here — it is the same
    check without the dependency.
    """
    tree = ast.parse(_source(relative))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found in {relative}")


def _code_only(relative: str) -> str:
    """Source with docstrings removed.

    These modules discuss their own defects at length. Matching on prose would
    make a test assert something about comments, and the Phase 4 experience was
    that such a test gets "fixed" by rewording the comment.
    """
    source = _source(relative)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                source = source.replace(doc, "")
    return source


# -- A4: the job release fence -----------------------------------------------


@pytest.mark.parametrize("method", ["mark_completed", "mark_retry", "mark_failed"])
def test_every_job_release_is_fenced_by_the_worker_that_holds_it(method):
    """Defect: releases keyed on ``id`` alone.

    The sweeper reclaims a job whose worker exceeded the visibility timeout, a
    second worker claims it, and the first — alive, just slow — then marks it
    completed or requeues it out from under the second. A release must be
    conditional on still holding the lock.

    Read from source rather than by importing: this module must stay runnable
    where SQLAlchemy is not installed, and the property is a property of the
    statement text either way.
    """
    body = _function_source("db/repositories/jobs.py", method)
    assert "_FENCE" in body, f"{method} releases a job without the worker fence"
    assert "worker_id" in body, f"{method} takes no worker id to fence on"


def test_the_fence_compares_the_lock_holder():
    source = _code_only("db/repositories/jobs.py")
    fence = re.search(r'_FENCE = "([^"]+)"', source)
    assert fence, "the fence constant has been renamed or removed"
    assert "locked_by = :worker_id" in fence.group(1)
    # A NULL worker id disables the fence — for the sweeper and for tests. That
    # is deliberate, so the SQL has to express it rather than relying on
    # PostgreSQL's NULL comparison semantics doing the right thing by accident.
    assert "IS NULL" in fence.group(1)


def test_the_runner_passes_its_own_worker_id_to_every_release():
    """The fence is inert if the call sites do not use it.

    Parsed rather than pattern-matched: these are multi-line calls, and a
    regex over them silently matches the wrong span — which is how a test like
    this ends up asserting nothing.
    """
    tree = ast.parse(_source("jobs/runner.py"))
    releases = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"mark_completed", "mark_retry", "mark_failed"}
    ]
    assert releases, "no release call sites found — has the runner been renamed?"
    for call in releases:
        keywords = {kw.arg for kw in call.keywords}
        assert "worker_id" in keywords, f"unfenced release at line {call.lineno}: {call.func.attr}"


# -- A2: enqueue must not lose the audit row ---------------------------------


def test_enqueue_handles_the_dedupe_conflict_instead_of_raising():
    """Defect: a second delivery mapping to a live ``dedupe_key`` raised
    ``IntegrityError``, which surfaced as a 500 and rolled back the webhook
    audit row with it. GitHub does not redeliver, so the event was lost with no
    trace that it had arrived."""
    source = _code_only("db/repositories/jobs.py")
    assert "IntegrityError" in source
    assert "begin_nested" in source, (
        "a constraint violation poisons the enclosing transaction; the insert "
        "needs a savepoint or the caller cannot commit the audit row either"
    )


def test_enqueue_can_report_that_it_did_nothing():
    tree = ast.parse(_source("db/repositories/jobs.py"))
    enqueue = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "enqueue"
    )
    returns = ast.unparse(enqueue.returns) if enqueue.returns else ""
    assert "None" in returns, "enqueue must be able to say 'an equivalent job is already active'"


def test_a_coalesced_delivery_is_still_recorded():
    source = _code_only("services/webhook_service.py")
    assert "COALESCED" in source
    assert "mark_coalesced" in source, "a coalesced delivery must leave an audit trail, not vanish"


def test_the_coalesced_status_is_permitted_by_the_check_constraint():
    model = _source("db/models/webhook_event.py")
    assert '"coalesced"' in model, "PROCESSING_STATUSES must list the new value"
    assert "'coalesced'" in model, "the CHECK constraint must allow the new value"


# -- A7: every routed job type has a handler ---------------------------------


def _registered_handlers() -> set[str]:
    """The keys of ``HANDLERS``, read from source.

    Importing the package would pull in the whole worker dependency tree; the
    registry is a literal dict, so parsing it is exact rather than approximate.
    """
    tree = ast.parse(_source("jobs/handlers/__init__.py"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "HANDLERS" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            return {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
    raise AssertionError("HANDLERS is no longer a literal dict")


def test_every_job_type_the_router_can_produce_has_a_handler():
    """Defect: ``installation`` and ``installation_repositories`` deliveries
    routed to job types with no handler, so every App install, uninstall,
    suspend and repository change failed permanently with "No handler
    registered" — and the installation tables were never updated."""
    routed = set(re.findall(r'job_type="([a-z_]+)"', _source("github/events.py")))
    assert routed, "no routed job types found — has events.py been restructured?"
    missing = routed - _registered_handlers()
    assert missing == set(), f"routed job types with no handler: {sorted(missing)}"


def test_every_handler_corresponds_to_a_valid_job_type():
    """The reverse direction: a handler for a job type the schema rejects would
    be dead code that looks like coverage."""
    job_types = set(
        re.findall(
            r'"([a-z_]+)"',
            re.search(r"JOB_TYPES[^=]*=\s*\(([^)]*)\)", _source("db/models/job.py"), re.S).group(1),
        )
    )
    assert job_types, "JOB_TYPES could not be read from the model"
    unknown = _registered_handlers() - job_types
    assert unknown == set(), f"handlers for job types the schema forbids: {unknown}"


# -- A3: no external call inside an open transaction -------------------------


def test_the_sync_service_commits_before_running_analysis():
    """Defect: four write statements opened a transaction, and the Phase 2
    analysis — up to 300 sequential GitHub fetches — then ran inside it,
    holding a pooled connection and the changed-files row locks for the whole
    duration."""
    source = _code_only("services/github_sync_service.py")
    commit_at = source.find("await self._session.commit()")
    analysis_at = source.find("await self._run_analysis(")
    assert commit_at != -1, "the sync service no longer commits"
    assert analysis_at != -1, "the analysis call has moved"
    assert commit_at < analysis_at, (
        "the transaction must close before the analysis makes HTTP calls"
    )


# -- A5: a publication transient must not re-run the review ------------------


def test_a_transient_publication_failure_is_caught_rather_than_raised():
    """Defect: a 429 or 5xx from GitHub at publication time escaped
    ``publish()``. The runner classified it retryable and rescheduled the job —
    but the findings were already committed, so the retry created a new run and
    inserted every finding again under a new ``review_run_id``. The dashboard
    counted each finding twice per retry."""
    source = _code_only("review/publisher.py")
    assert "except (TransientError, ThrottledError)" in source, (
        "publish() must absorb transients rather than letting the job retry"
    )


def test_the_publisher_returns_a_result_instead_of_raising_for_transients():
    from app.review.publisher import PublishOutcome, PublishResult

    # FAILED is a *reported* outcome, not an exception: the review ran and its
    # findings are stored, which is `partial_success` at the run level.
    assert not PublishResult(outcome=PublishOutcome.FAILED).succeeded


# -- A6: review runs must not leak in 'running' ------------------------------


def test_abandoned_review_runs_are_swept():
    """Defect: a retryable AI failure re-raised, leaving the run ``running``
    for ever. The comment claimed the stale-job sweeper reclaimed it; that
    sweeper touches ``jobs`` and nothing else."""
    body = _function_source("db/repositories/reviews.py", "abandon_stale")
    assert "status = 'running'" in body
    assert "started_at <" in body


def test_the_worker_sweep_closes_abandoned_runs():
    source = _code_only("jobs/runner.py")
    assert "abandon_stale" in source, (
        "the maintenance sweep must close abandoned review runs, not only jobs"
    )


# -- A8: redaction must see the traceback ------------------------------------


def test_the_redactor_runs_after_the_traceback_is_rendered():
    """Defect: ``redaction_processor`` was placed before ``format_exc_info``,
    so it inspected a record that did not yet contain the traceback. Every
    rendered SQLAlchemy exception — statement and bound parameters included —
    reached the log unread."""
    source = _source("core/logging.py")
    exc_at = source.find("structlog.processors.format_exc_info")
    redact_at = source.find("redaction_processor,  #")
    assert exc_at != -1 and redact_at != -1
    assert exc_at < redact_at, "the redactor must run after the traceback exists"


def test_the_redactor_recurses_into_lists():
    """Defect: ``_redact`` recursed into dicts only, so a list of dicts — which
    the validation error handler actually produces — passed through untouched
    at any depth."""
    from app.core.logging import REDACTED, _redact

    result = _redact({"errors": [{"api_key": "sk-live-abc"}, {"ok": 1}]})
    assert result["errors"][0]["api_key"] == REDACTED
    assert result["errors"][1]["ok"] == 1


def test_a_rendered_sql_exception_loses_its_bound_parameters():
    from app.core.logging import _redact

    rendered = (
        "IntegrityError: duplicate key\n"
        "[SQL: INSERT INTO jobs (payload) VALUES (%(payload)s)]\n"
        "[parameters: {'payload': {'secret_token': 'ghp_aaaaaaaaaaaaaaaaaaaaaaaa'}}]"
    )
    cleaned = _redact({"exception": rendered})["exception"]
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaa" not in cleaned
    assert "redacted-sql" in cleaned


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_0123456789abcdefghijklmnopqrstuvwxyz",
        "AIzaSyDaGmWKa4JsXZ-HjGw1234567890abcdefg",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "Authorization: Bearer v1.1234567890abcdef",
        "postgresql://user:hunter2@db.internal:5432/app",
    ],
)
def test_credential_shaped_values_are_scrubbed_from_free_text(secret):
    """Key-based redaction cannot see inside a string. This is the second
    layer, and it is explicitly the weaker one — it catches only what it
    recognises, which is why it is not the only one."""
    from app.core.logging import _redact

    cleaned = _redact({"detail": f"failed while handling {secret} here"})["detail"]
    assert secret not in cleaned


# -- A9 / A11: the GitHub client ---------------------------------------------


def test_responses_are_read_with_a_ceiling():
    """Defect: ``AsyncClient.request`` buffers the whole body with no limit.
    The blob endpoint returns inline base64 up to 100 MB, and the size guard
    downstream ran after all of it was already resident."""
    source = _code_only("github/client.py")
    assert "_send_bounded" in source
    assert "aiter_bytes" in source, "a ceiling that only reads Content-Length is not one"
    assert "github_max_response_bytes" in source


def test_an_installation_token_is_only_sent_to_the_configured_host():
    """Defect: pagination followed the upstream ``Link`` header to any absolute
    URL, with the installation token attached and no host check."""
    source = _code_only("github/client.py")
    assert "_require_trusted_host" in source
    request_body = source[source.find("async def _request") :]
    assert "_require_trusted_host" in request_body[:2000], (
        "the host check must run in the request path, not somewhere optional"
    )


# -- A10: graceful shutdown ---------------------------------------------------


def test_the_worker_drains_its_in_flight_job_on_sigterm():
    """Defect: SIGTERM was observed only between jobs, and a review routinely
    outlives Docker's 10-second grace — so the process was killed mid-handler
    and its job sat ``running`` until the 15-minute sweep."""
    assert "async def run_until_drained" in _source("jobs/runner.py")
    source = _code_only("worker.py")
    assert "run_until_drained" in source
    assert "worker_shutdown_drain_seconds" in source


# -- configuration fail-closed ------------------------------------------------


def test_there_is_no_fallback_session_secret_constant():
    """A development fallback secret is a secret the attacker also has, and the
    one time it matters is the one time somebody forgot to set the real one."""
    source = _code_only("core/config.py")
    # An ephemeral per-process key is fine; a literal in the source is not.
    assert "secrets.token_bytes" in source
    literals = re.findall(r'session_secret\s*[:=]\s*["\'][^"\']{8,}["\']', source)
    assert literals == [], f"hard-coded session secret: {literals}"


def test_the_proxy_middleware_counts_from_the_right():
    """``X-Forwarded-For`` is client-appendable. Reading ``entries[0]`` — the
    intuitive "original client" — reads whatever the client wrote there."""
    source = _code_only("core/proxy.py")
    assert "len(entries) - self._hops" in source
    assert "entries[0]" not in source, "the leftmost entry is client-supplied"
