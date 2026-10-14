"""Offline end-to-end: analysis → model → validation → publication.

No network, no credentials, no database. A fake provider stands in for Gemini
and a fake client for GitHub, so the whole Phase 3 path runs deterministically
and for free. Persistence is exercised separately against a real PostgreSQL
(see ``docs/phase-3.md``); this suite covers everything between the analysis
and the GitHub write.

The point of an end-to-end test here is that the seams are where the bugs live:
each component is correct in isolation and the question is whether the context
builder's line numbers, the validator's gates and the publisher's payload agree
with one another.
"""

from __future__ import annotations

from app.ai.contracts import (
    FindingCategory,
    FindingSeverity,
    FindingSide,
    ProviderUsage,
    RawFinding,
    ReviewResult,
)
from app.review.context import ContextBudget, build_review_context
from app.review.publisher import PublishOutcome, ReviewPublisher, extract_marker
from app.review.validator import FindingValidator

from tests.review.fixtures import FILE_PATH, HEAD_SHA, build_analysis
from tests.review.test_publisher import FakeClient

MODEL_RESPONSE = [
    # Truthful, exactly located.
    RawFinding(
        finding_id="F001",
        category=FindingCategory.RESOURCE,
        severity=FindingSeverity.HIGH,
        title="File handle is never closed",
        description="open() is called without a context manager.",
        file_path=FILE_PATH,
        line_start=14,
        anchor_snippet="handle = open(path)",
        confidence=0.9,
        impact="File descriptors leak until the process exits.",
        rationale="No close() and no with-statement on this path.",
    ),
    # Truthful but miscounted by two lines.
    RawFinding(
        finding_id="F002",
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.MEDIUM,
        title="Path is built from unvalidated input",
        description="key is joined onto the root with no validation.",
        file_path=FILE_PATH,
        line_start=15,
        anchor_snippet="path = os.path.join(self.root, key)",
        confidence=0.7,
        impact="A key containing .. escapes the session directory.",
        rationale="os.path.join happily accepts traversal segments.",
    ),
    # Hallucinated file.
    RawFinding(
        finding_id="F003",
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        title="Hard-coded credential",
        description="A secret is committed to the repository.",
        file_path="../../secret",
        line_start=999999,
        anchor_snippet="SECRET = 'hunter2'",
        confidence=0.99,
        impact="Anyone with the repository has the key.",
        rationale="It is right there.",
    ),
    # A deletion-side claim.
    RawFinding(
        finding_id="F004",
        category=FindingCategory.BUG,
        severity=FindingSeverity.LOW,
        title="Removed guard",
        description="The removed check was load-bearing.",
        file_path=FILE_PATH,
        line_start=14,
        side=FindingSide.LEFT,
        anchor_snippet="handle = open(path)",
        confidence=0.8,
        impact="Unclear.",
        rationale="It was there before.",
    ),
]


class FakeProvider:
    """Returns a scripted response. Records what it was asked."""

    name = "fake"

    def __init__(self, findings=MODEL_RESPONSE, model: str = "fake-model-1"):
        self._findings = findings
        self._model = model
        self.requests: list = []

    async def review(self, request):
        self.requests.append(request)
        return ReviewResult(
            model=self._model,
            summary="Reviewed the changed code.",
            findings=tuple(self._findings),
            usage=ProviderUsage(input_tokens=900, output_tokens=300, latency_ms=1200),
        )


async def _run(client: FakeClient, provider: FakeProvider, *, head_sha: str = HEAD_SHA):
    # The fake GitHub must agree with the fixture about which commit is head;
    # otherwise the publisher's stale-head guard — correctly — refuses to post.
    client.head_sha = head_sha
    analysis = build_analysis(head_sha=head_sha)
    context = build_review_context(analysis, budget=ContextBudget())

    class _Request:
        context_document = context.document
        max_findings = 25

    result = await provider.review(_Request())
    report = FindingValidator().validate(
        result.findings,
        context=context,
        analyses={f.file_path: f for f in analysis.files},
        head_sha=head_sha,
    )
    publisher = ReviewPublisher(
        client=client, installation_id=1, owner="acme", repo="web", pull_number=7
    )
    publish = await publisher.publish(
        findings=report.validated,
        head_sha=head_sha,
        run_id="run-e2e",
        model_name=result.model,
        rejected_count=len(report.rejected),
        context_truncated=context.truncated,
    )
    return context, report, publish


async def test_two_truthful_findings_are_published_and_two_hostile_ones_are_not():
    client = FakeClient()
    _context, report, publish = await _run(client, FakeProvider())

    assert len(report.validated) == 2
    assert len(report.rejected) == 2
    assert publish.outcome == PublishOutcome.PUBLISHED

    comments = client.reviews[0]["comments"]
    assert len(comments) == 2
    assert {c["path"] for c in comments} == {FILE_PATH}
    assert all(c["side"] == "RIGHT" for c in comments)


async def test_no_comment_is_ever_placed_on_a_line_outside_the_diff():
    """The end-to-end restatement of the whole phase's purpose."""
    client = FakeClient()
    analysis = build_analysis()
    line_map = analysis.files[0].line_map
    assert line_map is not None

    await _run(client, FakeProvider())

    for comment in client.reviews[0]["comments"]:
        assert line_map.is_commentable(comment["line"]), (
            f"line {comment['line']} is not in this pull request's diff"
        )


async def test_every_published_line_holds_the_source_the_model_quoted():
    client = FakeClient()
    analysis = build_analysis()
    source = analysis.files[0].source
    assert source is not None

    _context, report, _publish = await _run(client, FakeProvider())

    for finding in report.validated:
        assert finding.anchor_snippet in (source.line(finding.line) or "")


async def test_the_miscounted_finding_is_relocated_and_the_fact_is_disclosed():
    client = FakeClient()
    _context, report, _publish = await _run(client, FakeProvider())

    relocated = [f for f in report.validated if f.anchor_relocated]
    assert len(relocated) == 1
    assert relocated[0].claimed_line == 15
    assert relocated[0].line == 13

    bodies = [c["body"] for c in client.reviews[0]["comments"]]
    assert any("line 15" in body for body in bodies)


async def test_the_review_body_reports_the_rejected_claims_without_naming_them():
    client = FakeClient()
    await _run(client, FakeProvider())

    body = client.reviews[0]["body"]
    assert "2 further model suggestions" in body
    assert "../../secret" not in body, "a hallucinated path is never echoed back"


async def test_a_second_identical_run_posts_nothing_new():
    """Idempotent republication, driven by the fingerprint markers GitHub
    itself is holding."""
    first_client = FakeClient()
    await _run(first_client, FakeProvider())
    posted = [
        {"id": index, "body": comment["body"]}
        for index, comment in enumerate(first_client.reviews[0]["comments"])
    ]

    second_client = FakeClient(existing=posted)
    _context, _report, publish = await _run(second_client, FakeProvider())

    assert publish.outcome == PublishOutcome.NOTHING_TO_PUBLISH
    assert second_client.reviews == []
    assert len(publish.already_present) == 2


async def test_a_model_that_finds_nothing_produces_a_clean_review():
    client = FakeClient()
    _context, report, publish = await _run(client, FakeProvider(findings=[]))

    assert report.validated == ()
    assert publish.outcome == PublishOutcome.NOTHING_TO_PUBLISH
    assert "No issues found" in client.reviews[0]["body"]


async def test_a_model_that_returns_only_garbage_publishes_nothing_inline():
    garbage = [f for f in MODEL_RESPONSE if f.finding_id in ("F003", "F004")]
    client = FakeClient()
    _context, report, _publish = await _run(client, FakeProvider(findings=garbage))

    assert report.validated == ()
    assert len(report.rejected) == 2
    assert client.reviews[0]["comments"] == []


async def test_the_whole_pipeline_is_deterministic():
    first = await _run(FakeClient(), FakeProvider())
    second = await _run(FakeClient(), FakeProvider())

    assert first[0].document == second[0].document
    assert [f.fingerprint for f in first[1].validated] == [
        f.fingerprint for f in second[1].validated
    ]
    assert first[1].rejection_counts == second[1].rejection_counts


async def test_markers_are_unique_per_finding():
    client = FakeClient()
    await _run(client, FakeProvider())

    markers = [extract_marker(c["body"]) for c in client.reviews[0]["comments"]]
    assert len(set(markers)) == len(markers)
    assert all(marker is not None for marker in markers)
