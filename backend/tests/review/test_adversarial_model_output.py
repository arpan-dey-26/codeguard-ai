"""Hostile model output, end to end — Phase 6 §13.

The invariant under test is single and absolute:

    **AI output can never bypass validation and reach GitHub publication.**

``tests/review/test_validator.py`` covers the gates individually and is the
larger suite. This file differs in two ways.

**It names the gate.** Every case asserts the *specific* ``RejectionReason``,
not merely that the claim was refused. A claim rejected for the wrong reason
means some other gate happened to catch it and the intended gate is untested —
a defect of exactly that shape shipped in Phase 3 (``anchor_not_found``
reported where the real failure was ``line_not_presented``). Where the gate
order makes the reason deterministic, the assertion is an identity check.

**It continues past the validator.** A claim that survives validation is still
model-authored *text*, and that text is interpolated into a GitHub comment body
alongside the finding marker that publication identity depends on. The last
section attacks that seam. It is where this file found a live defect (see
``test_a_forged_marker_in_model_prose_cannot_steal_a_finding_identity``).
"""

from __future__ import annotations

import inspect

import pytest
from app.ai.contracts import FindingCategory, FindingSeverity, FindingSide, RawFinding
from app.review.context import ContextBudget, build_review_context
from app.review.publisher import (
    ReviewPublisher,
    extract_marker,
    render_comment_body,
    render_review_body,
)
from app.review.validator import (
    MAX_FINDING_SPAN,
    FindingValidator,
    RejectionReason,
    ValidatedFinding,
    is_safe_repository_path,
)

from tests.review.fixtures import (
    FILE_PATH,
    HEAD_SHA,
    OTHER_SHA,
    SOURCE,
    build_analysis,
    build_file_analysis,
)

# Line 14 of the fixture is `        handle = open(path)` — an added line
# inside SessionStore.load, and one the model was actually shown.
GOOD_LINE = 14
GOOD_ANCHOR = "handle = open(path)"


def _finding(**overrides) -> RawFinding:
    """A claim that is true in every respect, so each test changes exactly one."""
    values = {
        "finding_id": "F001",
        "category": FindingCategory.RESOURCE,
        "severity": FindingSeverity.HIGH,
        "title": "File handle is never closed",
        "description": "open() is called without a context manager.",
        "file_path": FILE_PATH,
        "line_start": GOOD_LINE,
        "anchor_snippet": GOOD_ANCHOR,
        "confidence": 0.9,
        "impact": "File descriptors leak until the process exits.",
        "rationale": "No close() and no with-statement.",
    }
    values.update(overrides)
    return RawFinding(**values)


def _validate(findings, *, analysis=None, head_sha=HEAD_SHA, validator=None):
    analysis = analysis or build_analysis()
    validator = validator or FindingValidator()
    return validator.validate(
        findings,
        context=build_review_context(analysis, budget=ContextBudget()),
        analyses={f.file_path: f for f in analysis.files},
        head_sha=head_sha,
    )


def _refusal(finding, *, analysis=None, head_sha=HEAD_SHA) -> RejectionReason:
    """Validate one hostile claim and return why it was refused.

    Fails loudly if the claim was *accepted* — that is the outcome this whole
    file exists to make impossible, so it is never a quiet boolean.
    """
    report = _validate([finding], analysis=analysis, head_sha=head_sha)
    assert not report.validated, f"a hostile claim was ACCEPTED and would reach GitHub: {finding!r}"
    assert len(report.rejected) == 1
    return report.rejected[0].reason


def _accept(finding, **kwargs) -> ValidatedFinding:
    report = _validate([finding], **kwargs)
    assert len(report.validated) == 1, [r.reason for r in report.rejected]
    return report.validated[0]


# ---------------------------------------------------------------------------
# Path and file identity
# ---------------------------------------------------------------------------


def test_a_file_the_model_was_never_shown_is_rejected():
    """Inventing a filename is the cheapest route to a comment on a file that
    nobody reviewed. The path is well-formed, so gate 3 passes and gate 4 is
    genuinely the one under test."""
    assert _refusal(_finding(file_path="app/never_sent_to_the_model.py")) is (
        RejectionReason.UNKNOWN_FILE
    )


@pytest.mark.parametrize(
    "hostile_path",
    [
        "../../etc/passwd",
        "/etc/shadow",
        "app/../../../secret.env",
        "..\\..\\windows\\system32\\config",  # backslash separators, fixed in Phase 6
        "app\\..\\..\\x",
        "C:/Windows/System32/drivers/etc/hosts",
        "c:\\windows\\win.ini",
        "\\\\attacker\\share\\payload",
        "app/./../../x",
        "//evil/path",
        "app/main\x00.py",
        "app/main\n.py",
        "app/main\r.py",
        "file:///etc/passwd",
        "https://evil.example/x.py",
        "a/" * 300 + "x.py",
        "",
    ],
)
def test_the_path_predicate_refuses_everything_that_is_not_a_repository_path(hostile_path):
    """Checked at the predicate directly, not only through the validator.

    Through the validator these would *also* be caught by gate 4 as unknown
    files, so a test that could not tell the two apart would keep passing if
    the path check were deleted outright. That is the point of defence in
    depth, and testing it only through the layer in front of it defeats it.
    """
    assert not is_safe_repository_path(hostile_path), f"accepted {hostile_path!r}"


def test_the_path_predicate_still_accepts_ordinary_repository_paths():
    """A refusal test alone is satisfied by ``return False``."""
    for path in ("app/session.py", "a.py", "src/deep/nested/mod_x-1.ts", "docs/README.md"):
        assert is_safe_repository_path(path), path


@pytest.mark.parametrize("hostile_path", ["../../etc/passwd", "/etc/shadow", "..\\..\\win"])
def test_a_traversal_path_is_refused_by_the_path_gate_not_by_luck(hostile_path):
    """Gate 3 runs before gate 4, so the reason must be UNSAFE_PATH. If this
    ever reports UNKNOWN_FILE, the path gate stopped running."""
    assert _refusal(_finding(file_path=hostile_path)) is RejectionReason.UNSAFE_PATH


# ---------------------------------------------------------------------------
# Commit identity
# ---------------------------------------------------------------------------


def test_a_claim_validated_against_a_different_head_commit_is_rejected():
    """The most damaging failure a review bot has: a comment anchored to a
    commit the author has already replaced."""
    assert _refusal(_finding(), head_sha=OTHER_SHA) is RejectionReason.SHA_MISMATCH


def test_a_file_analysed_at_a_different_commit_than_the_review_is_rejected():
    """The real shape of the race: a push lands mid-run, most of the analysis
    is re-done, and one file's blob is still the old one. Gate 5 compares three
    independent records of "which commit is this?" precisely because two of
    them agreeing proves nothing."""
    mixed = build_analysis(files=[build_file_analysis(head_sha=OTHER_SHA)])
    assert _refusal(_finding(), analysis=mixed) is RejectionReason.SHA_MISMATCH


def test_a_consistently_stale_review_is_not_rejected():
    """The complement, so the test above cannot be satisfied by refusing every
    SHA that is not the fixture's. Reviewing an older commit is legitimate —
    what gate 5 forbids is the three records *disagreeing*."""
    stale = build_analysis(head_sha=OTHER_SHA)
    assert _accept(_finding(), analysis=stale, head_sha=OTHER_SHA).head_sha == OTHER_SHA


# ---------------------------------------------------------------------------
# Line identity
# ---------------------------------------------------------------------------


def test_a_line_past_the_end_of_the_file_is_rejected():
    assert _refusal(_finding(line_start=999_999)) is RejectionReason.LINE_OUT_OF_RANGE


@pytest.mark.parametrize("line", [0, -1, -999])
def test_a_non_positive_line_is_rejected(line):
    assert _refusal(_finding(line_start=line)) is RejectionReason.LINE_OUT_OF_RANGE


def test_a_line_that_exists_but_is_not_in_the_diff_is_rejected():
    """Line 3 is ``import os`` — real code, in the file, and untouched by this
    pull request. Commenting there is commenting on code the author did not
    write in this change, which is how a review bot becomes noise."""
    claim = _finding(line_start=3, anchor_snippet="import os")
    assert _refusal(claim) is RejectionReason.LINE_NOT_IN_DIFF


def test_a_left_side_claim_is_rejected_rather_than_coerced():
    """ADR-0010: the alternative — silently treating LEFT as RIGHT — publishes
    a comment about deleted code onto whatever now occupies that line."""
    assert _refusal(_finding(side=FindingSide.LEFT)) is RejectionReason.WRONG_SIDE


@pytest.mark.parametrize(
    "line_end",
    [GOOD_LINE - 1, GOOD_LINE + MAX_FINDING_SPAN + 1],
)
def test_an_impossible_line_span_is_rejected(line_end):
    """Backwards, or so wide it is not a location at all."""
    assert _refusal(_finding(line_end=line_end)) is RejectionReason.MALFORMED


# ---------------------------------------------------------------------------
# Anchor integrity — the gate that makes every other one meaningful
# ---------------------------------------------------------------------------


def test_a_fabricated_anchor_is_rejected():
    """The model quotes source that is not there. Without this gate, every
    other check is defeated by a plausible-looking line number."""
    assert _refusal(_finding(anchor_snippet="this text appears nowhere in the file")) is (
        RejectionReason.ANCHOR_NOT_FOUND
    )


@pytest.mark.parametrize("anchor", ["", "   ", "x", ")", "\t\n "])
def test_an_anchor_too_short_to_falsify_anything_is_rejected(anchor):
    """``)`` appears on hundreds of lines; an anchor that matches everywhere
    verifies nothing, so it is refused rather than trivially satisfied."""
    assert _refusal(_finding(anchor_snippet=anchor)) is RejectionReason.ANCHOR_TOO_SHORT


def test_a_megabyte_anchor_is_rejected_rather_than_searched_for():
    assert _refusal(_finding(anchor_snippet="A" * 100_000)) is RejectionReason.ANCHOR_NOT_FOUND


def test_an_anchor_from_elsewhere_in_the_file_does_not_relocate_across_the_file():
    """Relocation is ±3 lines and exists for off-by-one model arithmetic. A
    model citing line 14 while quoting line 22 is not off by one; it is wrong,
    and the comment must not silently move twelve lines to accommodate it."""
    claim = _finding(line_start=GOOD_LINE, anchor_snippet='with open(path, "w") as handle:')
    assert _refusal(claim) is RejectionReason.ANCHOR_NOT_FOUND


# ---------------------------------------------------------------------------
# Content safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "B" * 24,
        "AIza" + "b" * 35,
        "sk-ant-" + "c" * 24,
        "sk-" + "d" * 40,
        "AKIA" + "E" * 16,
        "xoxb-" + "f" * 20,
        "-----BEGIN " + "RSA PRIVATE KEY-----",
    ],
)
@pytest.mark.parametrize("field", ["description", "title", "impact", "rationale", "suggested_fix"])
def test_a_finding_that_quotes_a_credential_is_rejected_from_any_field(secret, field):
    """A model that read a key out of the source must not publish it back into
    a pull-request comment that may be world-readable. Every prose field the
    model controls is covered, because a gate applied to four fields out of
    five is not a gate."""
    claim = _finding(**{field: f"The value {secret} is hard-coded here."})
    assert _refusal(claim) is RejectionReason.SECRET_IN_TEXT


def test_a_credential_hidden_in_the_anchor_is_rejected():
    """The anchor is prose the model wrote too, and it is published verbatim."""
    claim = _finding(anchor_snippet="token = 'ghp_" + "z" * 36 + "'")
    assert _refusal(claim) is RejectionReason.SECRET_IN_TEXT


def test_describing_a_credential_without_quoting_it_is_still_allowed():
    """The gate must not make the security category unusable. This is the
    finding a real secret-scanning review would produce."""
    claim = _finding(
        severity=FindingSeverity.CRITICAL,
        category=FindingCategory.SECURITY,
        description="This line hard-codes a GitHub personal access token. Rotate it and "
        "read it from the environment instead.",
    )
    assert _accept(claim).category is FindingCategory.SECURITY


@pytest.mark.parametrize("title", ["", "   \t\n  "])
def test_a_title_that_is_only_whitespace_is_rejected(title):
    assert _refusal(_finding(title=title)) is RejectionReason.MALFORMED


def test_an_empty_description_is_rejected():
    assert _refusal(_finding(description="  ")) is RejectionReason.MALFORMED


def test_an_enormous_description_is_truncated_rather_than_rejected():
    """Deliberately *not* a rejection, and recorded here so the behaviour is a
    decision rather than an oversight: length is not a trust question, and
    dropping a true finding because the model was verbose would be worse than
    shortening it. The bound is what matters — the body must not be unbounded
    model output."""
    finding = _accept(_finding(description="D" * 500_000))
    assert len(finding.description) <= 1200
    assert len(render_comment_body(finding)) < 10_000


# ---------------------------------------------------------------------------
# Confidence and duplication
# ---------------------------------------------------------------------------


def test_a_low_confidence_claim_is_rejected():
    assert _refusal(_finding(confidence=0.01)) is RejectionReason.LOW_CONFIDENCE


@pytest.mark.parametrize("confidence", [-1.0, -0.001, 1.5, 99.0])
def test_a_confidence_outside_zero_to_one_is_rejected(confidence):
    """Out of range is malformed, not merely low: a model returning 99.0 has
    misunderstood the field, and treating it as 'very confident' would invert
    the gate."""
    assert _refusal(_finding(confidence=confidence)) is RejectionReason.MALFORMED


def test_the_second_of_two_identical_claims_is_rejected():
    """A model repeating itself must not produce two comments on one line."""
    report = _validate([_finding(finding_id="F001"), _finding(finding_id="F002")])
    assert len(report.validated) == 1, "a duplicate claim would have been published twice"
    assert len(report.rejected) == 1
    assert report.rejected[0].reason is RejectionReason.DUPLICATE


def test_a_reworded_duplicate_is_still_a_duplicate():
    """The fingerprint case-folds and whitespace-collapses the title, so
    re-titling is not a way to say the same thing twice."""
    report = _validate(
        [
            _finding(finding_id="F001"),
            _finding(finding_id="F002", title="File  Handle Is NEVER Closed"),
        ]
    )
    assert len(report.validated) == 1
    assert report.rejected[0].reason is RejectionReason.DUPLICATE


def test_more_claims_than_the_cap_are_refused_with_over_limit():
    """The cap is not silent truncation: the surplus is recorded as rejected,
    so the funnel on the dashboard still adds up."""
    validator = FindingValidator(max_findings=1)
    report = _validate(
        [
            _finding(finding_id="F001"),
            _finding(finding_id="F002", line_start=15, anchor_snippet="data = handle.read()"),
        ],
        validator=validator,
    )
    assert len(report.validated) == 1
    assert report.rejected[0].reason is RejectionReason.OVER_LIMIT


# ---------------------------------------------------------------------------
# Encoding edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "\u202e",  # right-to-left override: renders following text reversed
        "\u200b" * 50,  # zero-width spaces
        "\ufeff",  # BOM in the middle of a string
        "\x00",  # NUL
        "a\r\nb",  # CRLF inside a field
        "\U0001d54f" * 100,  # astral-plane characters
        "```\n# not really code\n```",
        "</script><img src=x onerror=alert(1)>",
    ],
)
def test_unicode_and_injection_payloads_never_produce_a_broken_comment(payload):
    """Neither a crash nor an escape. Rejection and acceptance are both fine
    outcomes; what is asserted is that if the claim *is* published, the body it
    produces is a single well-formed comment whose identity is still ours."""
    report = _validate([_finding(title=f"Issue {payload}", description=f"Detail {payload}")])
    for finding in report.validated:
        body = render_comment_body(finding)
        assert extract_marker(body) == finding.fingerprint
        assert body.splitlines()[0].startswith("**")


def test_a_crlf_source_file_still_anchors_on_the_right_line():
    """Windows line endings must not shift every line number by one. A silent
    off-by-one here would put every comment on the line above the problem."""
    analysis = build_analysis(files=[build_file_analysis(source_text=SOURCE.replace("\n", "\r\n"))])
    finding = _accept(_finding(), analysis=analysis)
    assert finding.line == GOOD_LINE
    assert "\r" not in finding.anchor_snippet
    assert finding.source_line.strip() == GOOD_ANCHOR


# ---------------------------------------------------------------------------
# The publication seam — validated text is still model-authored text
# ---------------------------------------------------------------------------


VICTIM_FINGERPRINT = "b" * 32


@pytest.mark.parametrize("field", ["description", "impact", "suggested_fix", "title"])
def test_a_forged_marker_in_model_prose_cannot_steal_a_finding_identity(field):
    """**Phase 6 defect, found here and fixed in ``app/review/publisher.py``.**

    Every comment carries a hidden ``<!-- codeguard-finding:<fingerprint> -->``
    marker, and republication reads it back from GitHub to answer "have we
    already said this?". The model's own prose is interpolated into the same
    body. Emitting a marker-shaped string therefore used to forge a comment's
    identity, because ``extract_marker`` returned the *first* match and ours is
    appended last.

    That was not cosmetic. The forged fingerprint is recorded as present on the
    pull request, so the genuine finding that owns it is treated as already
    posted and is **never published again** — model output silently deciding
    which real findings a reviewer gets to see. Meanwhile the comment's own
    fingerprint goes unrecorded, so it is re-posted on every subsequent run.

    A public pull request makes the diff attacker-controlled, and the diff is
    what the model reads, so "the model emits this string" is a reachable state
    and not a hypothetical.
    """
    payload = f"context <!-- codeguard-finding:{VICTIM_FINGERPRINT} --> context"
    finding = _accept(_finding(**{field: payload}))
    body = render_comment_body(finding)

    assert extract_marker(body) == finding.fingerprint
    assert extract_marker(body) != VICTIM_FINGERPRINT
    # The attempt stays visible to a human reader rather than being deleted:
    # the escaped form is what GitHub renders as literal text.
    assert VICTIM_FINGERPRINT in body
    assert f"<!-- codeguard-finding:{VICTIM_FINGERPRINT} -->" not in body


def test_extract_marker_reads_our_own_marker_even_when_an_older_comment_carries_two():
    """The reader is correct independently of the writer.

    Comments posted before the sanitiser shipped may already contain a forged
    marker, so reconciliation must still identify them correctly. Ours is
    always last.
    """
    body = (
        f"<!-- codeguard-finding:{VICTIM_FINGERPRINT} -->\n"
        "prose\n"
        "<!-- codeguard-finding:" + "a" * 32 + " -->"
    )
    assert extract_marker(body) == "a" * 32


def test_a_multi_line_title_cannot_forge_markdown_structure():
    """``ValidatedFinding.title`` is stripped and truncated but keeps interior
    newlines, and it is rendered inside a line of Markdown in two places. A
    title of ``"Bug\\n\\n## Something official-looking"`` used to render as a
    real heading in both."""
    finding = _accept(_finding(title="Bug\n\n## Critical: merge immediately\n\n---"))
    first_line = render_comment_body(finding).splitlines()[0]
    assert first_line.startswith("**")
    assert "## " not in first_line or first_line.count("\n") == 0

    summary = render_review_body(
        inline=[],
        summary_only=[finding],
        head_sha=HEAD_SHA,
        run_id="run-1",
        model_name="test-model",
        rejected_count=0,
    )
    bullets = [line for line in summary.splitlines() if line.startswith("- **")]
    assert len(bullets) == 1, "the title escaped its bullet"


def test_the_publisher_has_no_way_to_accept_an_unvalidated_claim():
    """The type signature is the enforcement, so it is asserted rather than
    trusted. ``RawFinding`` is not imported by the publishing module at all,
    and ``publish`` is annotated with the validated type."""
    import app.review.publisher as publisher

    assert not hasattr(publisher, "RawFinding")
    annotation = inspect.signature(ReviewPublisher.publish).parameters["findings"].annotation
    assert "ValidatedFinding" in str(annotation)
    assert "RawFinding" not in str(annotation)


# ---------------------------------------------------------------------------
# The invariant, stated once more as a whole
# ---------------------------------------------------------------------------


def test_a_barrage_of_hostile_claims_publishes_nothing():
    """Every attack above in one response, as a compromised model would send
    it. Not one may survive to publication, and every refusal must carry a
    reason from the closed vocabulary — a free-text reason would make "is the
    model getting better or worse?" unanswerable."""
    hostile = [
        _finding(finding_id="M01", file_path="../../etc/passwd"),
        _finding(finding_id="M02", file_path="/etc/shadow"),
        _finding(finding_id="M03", file_path="app/invented.py"),
        _finding(finding_id="M04", line_start=999_999),
        _finding(finding_id="M05", line_start=0),
        _finding(finding_id="M06", line_start=3, anchor_snippet="import os"),
        _finding(finding_id="M07", side=FindingSide.LEFT),
        _finding(finding_id="M08", anchor_snippet="not in the file at all"),
        _finding(finding_id="M09", anchor_snippet="x"),
        _finding(finding_id="M10", description="key ghp_" + "z" * 36),
        _finding(finding_id="M11", confidence=0.0),
        _finding(finding_id="M12", confidence=42.0),
        _finding(finding_id="M13", title="   "),
        _finding(finding_id="M14", description=""),
        _finding(finding_id="M15", line_end=GOOD_LINE - 5),
    ]
    report = _validate(hostile)

    assert report.validated == (), (
        f"{len(report.validated)} hostile claims would have been published"
    )
    assert len(report.rejected) == len(hostile)
    assert all(isinstance(r.reason, RejectionReason) for r in report.rejected)
    # Every gate that should have fired, fired — not one gate catching all of it.
    assert {r.reason for r in report.rejected} >= {
        RejectionReason.UNSAFE_PATH,
        RejectionReason.UNKNOWN_FILE,
        RejectionReason.LINE_OUT_OF_RANGE,
        RejectionReason.LINE_NOT_IN_DIFF,
        RejectionReason.WRONG_SIDE,
        RejectionReason.ANCHOR_NOT_FOUND,
        RejectionReason.ANCHOR_TOO_SHORT,
        RejectionReason.SECRET_IN_TEXT,
        RejectionReason.LOW_CONFIDENCE,
        RejectionReason.MALFORMED,
    }


def test_one_true_claim_among_the_barrage_still_survives():
    """The complement, and the reason the file is not satisfied by a validator
    that rejects everything. A gate that blocks the truth too is not a gate,
    it is an outage."""
    hostile = [
        _finding(finding_id="M01", file_path="../../etc/passwd"),
        _finding(finding_id="M02", anchor_snippet="not in the file at all"),
        _finding(finding_id="M03", description="key ghp_" + "z" * 36),
    ]
    report = _validate([*hostile, _finding(finding_id="TRUE")])

    assert len(report.validated) == 1
    survivor = report.validated[0]
    assert survivor.model_finding_id == "TRUE"
    assert survivor.line == GOOD_LINE
    assert survivor.head_sha == HEAD_SHA
    assert extract_marker(render_comment_body(survivor)) == survivor.fingerprint
