"""Every declared limit is enforced by something. Phase 6 §21.

A configured ceiling that no code reads is the same failure as a flag nothing
checks: it reads as a guarantee, an operator sets it believing they are
protected, and it does nothing. Phase 6 found three of them —
``AI_MAX_REQUESTS_PER_REVIEW`` (documented to operators in
``docs/deployment.md`` as a free-tier guard), ``AI_MAX_RETRIES`` and
``WORKER_BATCH_SIZE``. The first is now enforced; the other two were removed,
because the honest fix for a knob that turns nothing is to take it off the
panel, not to leave it there looking functional.

The first test below is the one that found them and is the reason this file
exists: it is a standing check, not a record of a past cleanup. The rest
exercise the limits whose enforcement can be executed here. Limits enforced
over HTTP (request body size) live in ``test_middleware_over_http.py``; limits
enforced in SQL (page size) are covered against a real PostgreSQL by the
Phase 6 dashboard harness.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
from app.ai.budget import ProviderCallBudget
from app.ai.contracts import FindingCategory, FindingSeverity, RawFinding
from app.core.errors import ProviderBudgetExceededError
from app.review.context import ContextBudget, build_review_context
from app.review.validator import MAX_FINDING_SPAN, MIN_ANCHOR_CHARS, FindingValidator

from tests.review.fixtures import FILE_PATH, HEAD_SHA, build_analysis, build_file_analysis

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"

# Names that look like a ceiling. Deliberately broad: a false positive costs one
# line in the exemption list below and a false negative is an unenforced limit.
_LIMIT_NAME = re.compile(
    r"(max|min|limit|timeout|budget|size|bytes|attempts|retries|per_minute|multiplier|radius)"
)


def _declared_limits() -> list[str]:
    tree = ast.parse((APP_DIR / "core" / "config.py").read_text())
    names = {
        node.target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and _LIMIT_NAME.search(node.target.id)
    }
    return sorted(names)


def _application_source_excluding_config() -> str:
    return "\n".join(path.read_text() for path in APP_DIR.rglob("*.py") if path.name != "config.py")


def test_every_configured_limit_is_read_by_something():
    """The check that found the three inert settings.

    Reading a setting is a weaker property than enforcing it — a name could be
    logged and nothing more — so this is a floor, not a ceiling. It is still the
    check worth having, because a name that appears *nowhere* cannot possibly
    be enforced, and that is a defect no amount of reading the docs reveals.

    The behavioural counterpart for the specific limits that can be exercised
    here is the rest of this file.
    """
    source = _application_source_excluding_config()
    unread = [name for name in _declared_limits() if not re.search(rf"\b{name}\b", source)]
    assert unread == [], (
        "these limits are declared and documented but read by nothing, so setting "
        f"them changes nothing: {unread}"
    )


def test_the_free_tier_ceiling_refuses_the_call_past_it():
    """``AI_MAX_REQUESTS_PER_REVIEW``. Until Phase 6 this held only because the
    orchestrator happened to make exactly one call."""
    reached = 0

    class _Provider:
        async def review(self, request):  # type: ignore[no-untyped-def]
            nonlocal reached
            reached += 1
            return "result"

    budget = ProviderCallBudget(_Provider(), limit=2)

    async def _exercise() -> None:
        assert await budget.review(object()) == "result"  # type: ignore[arg-type]
        assert await budget.review(object()) == "result"  # type: ignore[arg-type]
        with pytest.raises(ProviderBudgetExceededError):
            await budget.review(object())  # type: ignore[arg-type]

    import asyncio

    asyncio.run(_exercise())
    assert reached == 2, "the refused call still reached the provider"
    assert budget.remaining == 0


def test_a_failing_provider_call_still_spends_its_budget():
    """Counting only successes would let a provider that fails repeatedly make
    unbounded requests — which is precisely the case a free-tier ceiling is
    for. The bill does not care whether the response parsed."""

    class _Broken:
        async def review(self, request):  # type: ignore[no-untyped-def]
            raise RuntimeError("upstream exploded")

    budget = ProviderCallBudget(_Broken(), limit=1)

    async def _exercise() -> None:
        with pytest.raises(RuntimeError):
            await budget.review(object())  # type: ignore[arg-type]
        with pytest.raises(ProviderBudgetExceededError):
            await budget.review(object())  # type: ignore[arg-type]

    import asyncio

    asyncio.run(_exercise())


# -- the context budget ------------------------------------------------------


def _finding(**overrides) -> RawFinding:
    values = {
        "finding_id": "F001",
        "category": FindingCategory.RESOURCE,
        "severity": FindingSeverity.HIGH,
        "title": "File handle is never closed",
        "description": "open() is called without a context manager.",
        "file_path": FILE_PATH,
        "line_start": 14,
        "anchor_snippet": "handle = open(path)",
        "confidence": 0.9,
        "impact": "File descriptors leak.",
        "rationale": "No close() and no with-statement.",
    }
    values.update(overrides)
    return RawFinding(**values)


def test_the_file_ceiling_truncates_the_context_and_says_so():
    """A budget that silently drops files is worse than one that refuses: the
    review then reports on part of a pull request while looking complete."""
    many = [build_file_analysis(path=f"app/mod_{index:03d}.py") for index in range(12)]
    context = build_review_context(build_analysis(files=many), budget=ContextBudget(max_files=4))

    assert len(context.included) == 4
    assert context.truncated is True
    assert len(context.excluded) == 8, "excluded files must be named, not just counted"


def test_the_byte_ceiling_bounds_the_document_actually_sent():
    """The token estimate is an estimate; the byte count is not. Both are
    checked, because the thing that arrives at the provider is bytes."""
    many = [build_file_analysis(path=f"app/mod_{index:03d}.py") for index in range(40)]
    budget = ContextBudget(max_files=40, max_bytes=8_000)
    context = build_review_context(build_analysis(files=many), budget=budget)

    assert len(context.document.encode("utf-8")) <= budget.max_bytes
    assert context.truncated is True


@pytest.mark.parametrize(
    ("file_count", "max_bytes"),
    [(40, 4_000), (40, 8_000), (200, 20_000), (3000, 20_000), (3000, 180_000)],
)
def test_the_byte_ceiling_holds_at_every_scale(file_count, max_bytes):
    """**Phase 6 defect, and the reason the case above is parametrized.**

    The manifest — two headings plus one line per file, included *and* excluded
    — was never charged against ``byte_total``, which counted the header and the
    file blocks only. The excluded half grows as the inverse of the budget: the
    tighter the ceiling, the more files it excludes and the longer the list
    naming them.

    Measured against the Phase 5 tree: a 3000-file pull request at the default
    180,000-byte budget produced a **221,017-byte** document, 1.2x the ceiling,
    with an estimated 63,148 tokens against a 60,000-token limit. The provider
    would have rejected it — after charging for it. At a 20,000-byte budget with
    200 files the overshoot was 1.6x.

    Parametrized because a single small case passed by luck for months: with few
    enough files the manifest is small enough to hide inside the slack.
    """
    many = [
        build_file_analysis(path=f"app/pkg{index // 50:02d}/module_name_{index:04d}.py")
        for index in range(file_count)
    ]
    budget = ContextBudget(max_files=40, max_bytes=max_bytes)
    context = build_review_context(build_analysis(files=many), budget=budget)

    actual = len(context.document.encode("utf-8"))
    assert actual <= max_bytes, (
        f"{file_count} files at a {max_bytes}-byte ceiling produced {actual} bytes "
        f"({actual / max_bytes:.1f}x) — the manifest is outside the budget again"
    )
    assert context.byte_size == actual, "the recorded size disagrees with the document"


def test_every_changed_file_is_still_accounted_for_when_the_manifest_is_capped():
    """Capping the *rendered* list must not lose the record. ``excluded`` is
    what the dashboard reports and what proves no file was silently dropped;
    only the text shown to the model is abbreviated, and a finding on a path we
    did not show is refused by the validator whether or not it was named."""
    many = [build_file_analysis(path=f"app/module_{index:04d}.py") for index in range(300)]
    context = build_review_context(build_analysis(files=many), budget=ContextBudget(max_files=5))

    assert len(context.included) + len(context.excluded) == 300
    assert "... and" in context.document, "the abbreviated tail is not disclosed to the model"


def test_a_context_that_fits_is_not_marked_truncated():
    """The complement: a budget that always reports truncation tells an
    operator nothing, and the review body shows this flag to users."""
    context = build_review_context(build_analysis(), budget=ContextBudget())
    assert context.truncated is False
    assert context.excluded == ()


# -- the validator's own ceilings --------------------------------------------


def test_the_findings_cap_rejects_the_surplus_rather_than_dropping_it():
    """Silent truncation would make the dashboard funnel stop adding up, and a
    funnel whose stages do not sum is worse than no funnel."""
    analysis = build_analysis()
    validator = FindingValidator(max_findings=1)
    report = validator.validate(
        [
            _finding(finding_id="F001"),
            _finding(finding_id="F002", line_start=15, anchor_snippet="data = handle.read()"),
        ],
        context=build_review_context(analysis, budget=ContextBudget()),
        analyses={f.file_path: f for f in analysis.files},
        head_sha=HEAD_SHA,
    )
    assert len(report.validated) == 1
    assert len(report.rejected) == 1
    assert report.total_claims == 2, "a capped claim vanished from the funnel"


@pytest.mark.parametrize(
    ("constant", "floor"),
    [(MAX_FINDING_SPAN, 1), (MIN_ANCHOR_CHARS, 1)],
)
def test_the_validator_ceilings_are_real_numbers_not_placeholders(constant, floor):
    assert isinstance(constant, int) and constant >= floor


# -- v0.6.1: the same check, widened from limits to every setting ------------


def _settings_fields() -> list[str]:
    tree = ast.parse((APP_DIR / "core" / "config.py").read_text())
    return [
        item.target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
        for item in node.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
        and not item.target.id.startswith("_")
    ]


def _config_members() -> dict[str, str]:
    """Every method and property of ``Settings``, by name, as source."""
    tree = ast.parse((APP_DIR / "core" / "config.py").read_text())
    return {
        item.name: ast.unparse(item)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _reaches_the_application(name: str, source: str, members: dict[str, str]) -> bool:
    """Is ``name`` read by the application, directly or through any chain?

    **Transitive on purpose.** The first version of this check followed one hop
    and reported `session_cookie_secure` as dead. It is not: it feeds the
    private `_secure_cookies`, which feeds the public `cookie_secure`, which the
    cookie writer and the HSTS header both read. A check that cannot follow two
    hops does not measure reachability — it measures how the code happens to be
    factored, and it would be "fixed" by adding an exemption, which is how a
    dead-configuration audit stops finding dead configuration.
    """
    if re.search(rf"\b{name}\b", source):
        return True

    seen: set[str] = set()
    frontier = [member for member, body in members.items() if re.search(rf"\b{name}\b", body)]
    while frontier:
        member = frontier.pop()
        if member in seen:
            continue
        seen.add(member)
        if not member.startswith("_") and re.search(rf"\b{member}\b", source):
            return True
        frontier.extend(
            other
            for other, body in members.items()
            if other not in seen and re.search(rf"\b{member}\b", body)
        )
    return False


def test_every_setting_reaches_something_that_uses_it():
    """The limit check above, widened to all 68 settings (v0.6.1).

    Phase 6 found three limit-shaped settings that nothing read. Restricting the
    check to names that *look* like limits was a convenience, and it left the
    rest of the configuration unchecked — a dead `SESSION_COOKIE_SAMESITE` would
    have been just as misleading as a dead `AI_MAX_RETRIES`, and just as
    invisible.

    A setting passes if the application reads it directly, or if it reaches — at
    any depth — a public property or method of `Settings` that the application
    reads. Three take the indirect route today and all three genuinely arrive:
    `trusted_hosts` → `trusted_host_list` → `TrustedHostMiddleware`;
    `session_cookie_secure` → `_secure_cookies` → `cookie_secure` → the cookie
    writer and HSTS; `public_base_url` → `oauth_redirect_uri` → the OAuth
    client.
    """
    source = _application_source_excluding_config()
    members = _config_members()
    dead = [
        name for name in _settings_fields() if not _reaches_the_application(name, source, members)
    ]

    assert dead == [], (
        "these settings are read by nothing, directly or through any property "
        f"the application uses — setting them changes nothing: {dead}"
    )
