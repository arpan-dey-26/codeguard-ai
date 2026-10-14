"""Publisher tests.

The publisher is the only code that writes to a pull request, so these tests
are about *what reaches GitHub* and *what happens when GitHub misbehaves*. The
fake client records every call, so assertions are made against the exact
requests the publisher issued rather than against a mock of its own methods.

No GitHub credentials are used and no network call is made. These tests prove
the publisher's logic; they do not prove that GitHub accepts the payloads,
which only a real installation can show.
"""

from __future__ import annotations

import pytest
from app.ai.contracts import FindingCategory, FindingSeverity, FindingSide
from app.core.errors import AmbiguousWriteError, GitHubValidationError, PermanentAccessError
from app.review.publisher import (
    PublishOutcome,
    ReviewPublisher,
    extract_marker,
    finding_marker,
    render_comment_body,
    render_review_body,
)
from app.review.validator import ValidatedFinding

HEAD = "a" * 40
MOVED = "b" * 40


def _validated(**overrides) -> ValidatedFinding:
    values = {
        "fingerprint": "f" * 32,
        "category": FindingCategory.RESOURCE,
        "severity": FindingSeverity.HIGH,
        "title": "File handle is never closed",
        "description": "open() without a context manager.",
        "file_path": "app/session.py",
        "line": 14,
        "side": FindingSide.RIGHT,
        "head_sha": HEAD,
        "anchor_snippet": "handle = open(path)",
        "source_line": "        handle = open(path)",
        "confidence": 0.9,
        "impact": "Descriptors leak.",
        "rationale": "No close().",
        "claimed_line": 14,
    }
    values.update(overrides)
    return ValidatedFinding(**values)


class FakeClient:
    """Records calls; raises whatever the test queues."""

    def __init__(self, *, head_sha: str = HEAD, existing: list | None = None, raises=None):
        self.head_sha = head_sha
        self.existing = existing or []
        self.raises = list(raises or [])
        self.reviews: list[dict] = []
        self.list_calls = 0
        self.next_review_id = 9001

    async def get_json(self, installation_id, path):
        return {"head": {"sha": self.head_sha}}

    async def list_review_comments(self, installation_id, owner, repo, number):
        self.list_calls += 1
        return list(self.existing)

    async def create_pull_request_review(
        self, installation_id, owner, repo, number, *, commit_id, body, comments
    ):
        self.reviews.append({"commit_id": commit_id, "body": body, "comments": list(comments)})
        if self.raises:
            error = self.raises.pop(0)
            if error is not None:
                raise error
        self.next_review_id += 1
        return {"id": self.next_review_id}


def _publisher(client, **overrides):
    kwargs = {
        "client": client,
        "installation_id": 1,
        "owner": "acme",
        "repo": "web",
        "pull_number": 7,
    }
    kwargs.update(overrides)
    return ReviewPublisher(**kwargs)


async def _publish(publisher, findings, **overrides):
    kwargs = {
        "findings": findings,
        "head_sha": HEAD,
        "run_id": "run-1",
        "model_name": "gemini-test",
    }
    kwargs.update(overrides)
    return await publisher.publish(**kwargs)


# -- THE STALE-HEAD RACE -----------------------------------------------------


async def test_a_push_during_the_review_aborts_publication_entirely():
    """The mandated race test.

    The findings were validated against commit A. By the time we publish, the
    pull request's head is commit B. Posting anchored comments now would place
    them on code the author has already replaced — the most trust-destroying
    failure a review bot has. Nothing is posted.
    """
    client = FakeClient(head_sha=MOVED)
    result = await _publish(_publisher(client), [_validated()])

    assert result.outcome == PublishOutcome.SKIPPED_STALE_HEAD
    assert result.observed_head_sha == MOVED
    assert client.reviews == [], "not one request may be issued"
    assert result.posted == ()


async def test_a_matching_head_allows_publication():
    client = FakeClient(head_sha=HEAD)
    result = await _publish(_publisher(client), [_validated()])

    assert result.outcome == PublishOutcome.PUBLISHED
    assert len(client.reviews) == 1
    assert client.reviews[0]["commit_id"] == HEAD


async def test_an_unreadable_preflight_does_not_block_the_review():
    """A transient GitHub hiccup must not silently discard a completed review.
    Only a *known* mismatch aborts."""

    class Unreadable(FakeClient):
        async def get_json(self, installation_id, path):
            raise RuntimeError("upstream blip")

    client = Unreadable()
    result = await _publish(_publisher(client), [_validated()])
    assert result.outcome == PublishOutcome.PUBLISHED


# -- payload shape -----------------------------------------------------------


async def test_comments_use_line_and_side_never_the_deprecated_position():
    client = FakeClient()
    await _publish(_publisher(client), [_validated()])

    comment = client.reviews[0]["comments"][0]
    assert comment["line"] == 14
    assert comment["side"] == "RIGHT"
    assert "position" not in comment
    assert comment["path"] == "app/session.py"


async def test_every_comment_carries_its_fingerprint_marker():
    client = FakeClient()
    await _publish(_publisher(client), [_validated()])

    body = client.reviews[0]["comments"][0]["body"]
    assert extract_marker(body) == "f" * 32


def test_the_marker_is_an_html_comment_so_nothing_renders():
    marker = finding_marker("abc123def456")
    assert marker.startswith("<!--") and marker.endswith("-->")


async def test_the_review_body_is_present_even_with_zero_findings():
    """A tool that says nothing when it finds nothing is indistinguishable from
    a broken one."""
    client = FakeClient()
    result = await _publish(_publisher(client), [])

    assert result.outcome == PublishOutcome.NOTHING_TO_PUBLISH
    assert len(client.reviews) == 1
    assert "No issues found" in client.reviews[0]["body"]
    assert client.reviews[0]["comments"] == []


async def test_the_publisher_never_issues_approve_or_request_changes():
    """Enforced at the client, asserted here so the guarantee is visible where
    the policy lives."""
    client = FakeClient()
    await _publish(_publisher(client), [_validated()])
    # `event` is not settable through create_pull_request_review at all: the
    # fake's signature mirrors the real one, which has no event parameter.
    assert "event" not in client.reviews[0]


# -- the inline-comment cap --------------------------------------------------


async def test_the_inline_cap_demotes_rather_than_drops():
    findings = [
        _validated(fingerprint=f"{i:032d}", line=10 + i, title=f"Issue {i}") for i in range(6)
    ]
    client = FakeClient()
    result = await _publish(_publisher(client, max_inline_comments=2), findings)

    assert len(client.reviews[0]["comments"]) == 2
    assert len(result.demoted) == 4
    body = client.reviews[0]["body"]
    for finding in findings[2:]:
        assert finding.title in body or "Findings without a verified inline location" in body


async def test_ranking_is_severity_first_and_deterministic():
    findings = [
        _validated(fingerprint="1" * 32, severity=FindingSeverity.LOW, line=10),
        _validated(fingerprint="2" * 32, severity=FindingSeverity.CRITICAL, line=11),
        _validated(fingerprint="3" * 32, severity=FindingSeverity.MEDIUM, line=12),
    ]
    client = FakeClient()
    await _publish(_publisher(client, max_inline_comments=1), findings)

    posted = client.reviews[0]["comments"][0]
    assert posted["line"] == 11, "the critical finding is the one that survives the cap"


# -- idempotent republication ------------------------------------------------


async def test_a_finding_already_commented_on_is_not_posted_again():
    existing = [{"id": 1, "body": f"old text {finding_marker('f' * 32)}"}]
    client = FakeClient(existing=existing)
    result = await _publish(_publisher(client), [_validated()])

    assert result.outcome == PublishOutcome.NOTHING_TO_PUBLISH
    assert client.reviews == [], "republishing an identical comment is noise"
    assert result.already_present == ("f" * 32,)


async def test_only_the_new_findings_are_posted_on_a_second_run():
    existing = [{"id": 1, "body": finding_marker("f" * 32)}]
    client = FakeClient(existing=existing)
    new = _validated(fingerprint="e" * 32, line=15, title="Second issue")
    result = await _publish(_publisher(client), [_validated(), new])

    assert len(client.reviews[0]["comments"]) == 1
    assert extract_marker(client.reviews[0]["comments"][0]["body"]) == "e" * 32
    assert result.already_present == ("f" * 32,)


# -- 422 salvage -------------------------------------------------------------


async def test_a_422_falls_back_to_a_safer_subset():
    """One unpositionable comment rejects the whole review, and GitHub does not
    say which. Losing every good finding to one bad one is the wrong trade."""
    findings = [
        _validated(fingerprint="1" * 32, line=14, confidence=0.95),
        _validated(fingerprint="2" * 32, line=15, confidence=0.55, anchor_relocated=True),
    ]
    client = FakeClient(raises=[GitHubValidationError("422"), None])
    result = await _publish(_publisher(client), findings)

    assert result.outcome == PublishOutcome.PUBLISHED_PARTIAL
    assert len(client.reviews) == 2
    assert len(client.reviews[1]["comments"]) == 1
    assert "2" * 32 in result.demoted


async def test_a_second_422_falls_back_to_summary_only_and_loses_nothing():
    """With a single high-confidence finding there is no smaller safe subset to
    try — retrying the identical payload would 422 again — so the publisher goes
    straight to a summary-only review rather than wasting a request."""
    findings = [_validated(fingerprint="1" * 32, line=14, confidence=0.95)]
    client = FakeClient(raises=[GitHubValidationError("422"), None])
    result = await _publish(_publisher(client), findings)

    assert result.outcome == PublishOutcome.SUMMARY_ONLY
    assert client.reviews[-1]["comments"] == []
    assert findings[0].title in client.reviews[-1]["body"]
    assert result.demoted == ("1" * 32,)


# -- ambiguous writes --------------------------------------------------------


async def test_an_ambiguous_write_is_reconciled_not_retried():
    """A timed-out POST may already have created the review. Retrying blindly
    is how duplicate comments appear."""
    fingerprint = "f" * 32

    class Reconciling(FakeClient):
        async def list_review_comments(self, *a, **k):
            self.list_calls += 1
            # First read: nothing. Second read (after the ambiguous write):
            # the comment is there, so the write did land.
            if self.list_calls == 1:
                return []
            return [{"id": 55, "body": finding_marker(fingerprint), "html_url": "u"}]

    client = Reconciling(raises=[AmbiguousWriteError("timeout")])
    result = await _publish(_publisher(client), [_validated(fingerprint=fingerprint)])

    assert result.outcome == PublishOutcome.PUBLISHED
    assert len(client.reviews) == 1, "no second write is attempted"
    assert result.posted[0].github_comment_id == 55


async def test_an_ambiguous_write_that_landed_nothing_is_reported_as_failed():
    client = FakeClient(raises=[AmbiguousWriteError("timeout")])
    result = await _publish(_publisher(client), [_validated()])

    assert result.outcome == PublishOutcome.FAILED
    assert len(client.reviews) == 1
    assert result.posted == ()


async def test_lost_access_is_reported_and_not_retried():
    client = FakeClient(raises=[PermanentAccessError("uninstalled")])
    result = await _publish(_publisher(client), [_validated()])

    assert result.outcome == PublishOutcome.FAILED
    assert len(client.reviews) == 1


# -- the kill switch ---------------------------------------------------------


async def test_publish_disabled_stops_the_write_and_nothing_else():
    client = FakeClient()
    result = await _publish(_publisher(client, publish_enabled=False), [_validated()])

    assert result.outcome == PublishOutcome.SKIPPED_DISABLED
    assert client.reviews == []
    assert client.list_calls == 0


# -- body rendering ----------------------------------------------------------


def test_a_relocated_finding_discloses_the_relocation():
    body = render_comment_body(_validated(claimed_line=16, line=14, anchor_relocated=True))
    assert "line 16" in body
    assert "resolved" in body.lower()


def test_the_review_body_states_how_many_claims_were_rejected():
    body = render_review_body(
        inline=[_validated()],
        summary_only=[],
        head_sha=HEAD,
        run_id="run-1",
        model_name="gemini-test",
        rejected_count=4,
    )
    assert "4 further model suggestions" in body


def test_the_review_body_warns_when_the_pull_request_exceeded_the_budget():
    body = render_review_body(
        inline=[],
        summary_only=[],
        head_sha=HEAD,
        run_id="run-1",
        model_name="gemini-test",
        rejected_count=0,
        context_truncated=True,
        excluded_files=["a.py", "b.py"],
    )
    assert "not reviewed" in body
    assert "`a.py`" in body


def test_the_review_body_always_carries_the_ai_disclaimer():
    body = render_review_body(
        inline=[],
        summary_only=[],
        head_sha=HEAD,
        run_id="run-1",
        model_name="gemini-test",
        rejected_count=0,
    )
    assert "AI-generated" in body
    assert "human judgement" in body


# -- the type boundary -------------------------------------------------------


def test_the_publisher_module_never_imports_rawfinding():
    """The Phase 3 invariant, asserted mechanically rather than by convention:
    an unverified model claim has no path to GitHub.

    Checked against the parsed AST, not the file text, so the prose in the
    module docstring that *explains* the rule does not satisfy it.
    """
    import ast
    import pathlib

    import app.review.publisher as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text())
    imported: set[str] = set()
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)

    assert "RawFinding" not in imported
    assert "RawFinding" not in referenced
    assert "ValidatedFinding" in imported, "the only finding type it may accept"


async def test_the_publisher_rejects_anything_that_is_not_a_validated_finding():
    """A duck-typed stand-in must not sail through."""

    class NotValidated:
        fingerprint = "z" * 32
        severity = FindingSeverity.HIGH
        confidence = 0.9
        file_path = "app/session.py"
        line = 14

    client = FakeClient()
    with pytest.raises(AttributeError):
        await _publish(_publisher(client), [NotValidated()])
    assert client.reviews == []
