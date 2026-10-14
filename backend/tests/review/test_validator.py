"""FindingValidator tests — the largest suite in Phase 3, deliberately.

Every test here answers the same question from a different angle: *can a model
claim get past the gates without being true?* The suite is adversarial by
construction. A model that behaves is easy; a model that lies, guesses,
hallucinates, or repeats an attacker's instructions is what the validator
exists for.

Nothing here proves the model finds real vulnerabilities. It proves that
whatever the model says, only claims that match the real diff and the real file
can reach GitHub.
"""

from __future__ import annotations

import pytest
from app.ai.contracts import FindingCategory, FindingSeverity, FindingSide, RawFinding
from app.review.context import ContextBudget, build_review_context
from app.review.validator import (
    FindingValidator,
    RejectionReason,
    compute_fingerprint,
    contains_secret,
    is_safe_repository_path,
)

from tests.review.fixtures import FILE_PATH, HEAD_SHA, OTHER_SHA, build_analysis

# Line 14 of the fixture is `        handle = open(path)` — an added line,
# inside SessionStore.load, and shown to the model.
GOOD_LINE = 14
GOOD_ANCHOR = "handle = open(path)"


def _context(analysis=None):
    return build_review_context(analysis or build_analysis(), budget=ContextBudget())


def _analyses(analysis=None):
    analysis = analysis or build_analysis()
    return {f.file_path: f for f in analysis.files}


def _finding(**overrides) -> RawFinding:
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


def _validate(findings, *, analysis=None, head_sha=HEAD_SHA, validator=None, context=None):
    analysis = analysis or build_analysis()
    validator = validator or FindingValidator()
    return validator.validate(
        findings,
        context=context or _context(analysis),
        analyses=_analyses(analysis),
        head_sha=head_sha,
    )


def _reasons(report):
    return {item.reason for item in report.rejected}


# -- the happy path ----------------------------------------------------------


def test_a_truthful_finding_survives_every_gate():
    report = _validate([_finding()])

    assert len(report.validated) == 1
    finding = report.validated[0]
    assert finding.line == GOOD_LINE
    assert finding.claimed_line == GOOD_LINE
    assert finding.anchor_relocated is False
    assert finding.source_line == "        handle = open(path)"
    assert finding.head_sha == HEAD_SHA
    assert finding.side is FindingSide.RIGHT
    assert finding.qualified_name == "SessionStore.load"


def test_a_finding_on_an_unchanged_context_line_in_the_hunk_is_allowed():
    """GitHub accepts a comment on any line in the diff, and a change can make
    an unchanged line wrong."""
    report = _validate(
        [_finding(line_start=19, anchor_snippet="path = os.path.join(self.root, key)")]
    )
    assert len(report.validated) == 1


# -- THE MALICIOUS MODEL -----------------------------------------------------


def test_a_fully_malicious_response_produces_zero_publishable_findings():
    """The mandated adversarial fixture.

    Every claim here is hostile in a different way. None may reach GitHub.
    """
    hostile = [
        # Path traversal, out-of-range line, fabricated anchor.
        _finding(
            finding_id="M1",
            file_path="../../secret",
            line_start=999999,
            anchor_snippet="SECRET_KEY = 'hunter2'",
        ),
        # An absolute path outside the repository.
        _finding(finding_id="M2", file_path="/etc/passwd", line_start=1),
        # A real file, a real-looking line, an anchor that is not there.
        _finding(
            finding_id="M3",
            line_start=GOOD_LINE,
            anchor_snippet="os.system(user_input)",
        ),
        # A real file and a real anchor, but a line the diff never touched.
        _finding(finding_id="M4", line_start=3, anchor_snippet="import os"),
        # A deletion-side finding.
        _finding(finding_id="M5", side=FindingSide.LEFT),
        # A file that exists in the repository but was not shown.
        _finding(finding_id="M6", file_path="app/other.py"),
        # A credential quoted verbatim in the model's own prose.
        _finding(
            finding_id="M7",
            description="The token ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA is hard-coded.",
        ),
        # A URL masquerading as a path.
        _finding(finding_id="M8", file_path="https://evil.test/app/session.py"),
        # A Windows absolute path.
        _finding(finding_id="M9", file_path="C:\\Windows\\system32\\config"),
        # An anchor so short it matches anything.
        _finding(finding_id="M10", anchor_snippet=")"),
    ]

    report = _validate(hostile)

    assert report.validated == (), "no hostile claim may become publishable"
    assert len(report.rejected) == len(hostile)
    assert _reasons(report) >= {
        RejectionReason.UNSAFE_PATH,
        RejectionReason.UNKNOWN_FILE,
        RejectionReason.ANCHOR_NOT_FOUND,
        RejectionReason.LINE_NOT_IN_DIFF,
        RejectionReason.WRONG_SIDE,
        RejectionReason.SECRET_IN_TEXT,
        RejectionReason.ANCHOR_TOO_SHORT,
    }


def test_a_hostile_claim_beside_a_truthful_one_does_not_poison_it():
    """Per-claim isolation: one bad finding must not discard the good ones."""
    report = _validate(
        [
            _finding(finding_id="BAD", file_path="../../secret", line_start=999999),
            _finding(finding_id="GOOD"),
        ]
    )
    assert len(report.validated) == 1
    assert report.validated[0].model_finding_id == "GOOD"
    assert len(report.rejected) == 1


# -- gate by gate ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["../../secret", "/etc/passwd", "a/../../b", "C:\\x", "https://x.test/a.py", "a//b"],
)
def test_unsafe_paths_are_rejected(path):
    assert is_safe_repository_path(path) is False


@pytest.mark.parametrize("path", ["a.py", "app/session.py", "a/b/c/d.ts"])
def test_ordinary_paths_are_accepted(path):
    assert is_safe_repository_path(path) is True


def test_a_file_that_was_not_shown_is_rejected_even_if_it_exists():
    report = _validate([_finding(file_path="app/unrelated.py")])
    assert _reasons(report) == {RejectionReason.UNKNOWN_FILE}


def test_a_line_beyond_the_end_of_the_file_is_rejected():
    report = _validate([_finding(line_start=999999)])
    assert _reasons(report) == {RejectionReason.LINE_OUT_OF_RANGE}


def test_a_line_outside_the_diff_is_rejected():
    report = _validate([_finding(line_start=1, anchor_snippet='"""Session handling."""')])
    assert _reasons(report) == {RejectionReason.LINE_NOT_IN_DIFF}


def test_left_side_findings_are_rejected_explicitly_not_coerced():
    """ADR-0010: silent coercion to RIGHT is the bug this prevents."""
    report = _validate([_finding(side=FindingSide.LEFT)])
    assert _reasons(report) == {RejectionReason.WRONG_SIDE}
    assert report.validated == ()


def test_an_analysis_from_another_commit_is_rejected():
    """The stale-SHA gate, checked against our own records."""
    stale = build_analysis(head_sha=OTHER_SHA)
    report = _validate([_finding()], analysis=stale, head_sha=HEAD_SHA)
    assert _reasons(report) == {RejectionReason.SHA_MISMATCH}


def test_a_context_from_another_commit_is_rejected():
    analysis = build_analysis()
    context = build_review_context(build_analysis(head_sha=OTHER_SHA), budget=ContextBudget())
    report = FindingValidator().validate(
        [_finding()],
        context=context,
        analyses=_analyses(analysis),
        head_sha=HEAD_SHA,
    )
    assert _reasons(report) == {RejectionReason.SHA_MISMATCH}


def test_a_line_that_was_never_shown_is_rejected_even_though_it_is_in_the_diff():
    """Stricter than "the line exists": a model citing a line it was never
    shown is guessing, and a lucky guess is still a guess."""
    analysis = build_analysis()
    context = _context(analysis)
    narrowed = type(context)(
        repository_full_name=context.repository_full_name,
        pull_request_number=context.pull_request_number,
        head_sha=context.head_sha,
        document=context.document,
        included=context.included,
        excluded=context.excluded,
        presented_lines={FILE_PATH: frozenset({12, 13})},
        estimated_tokens=context.estimated_tokens,
        byte_size=context.byte_size,
    )
    report = _validate([_finding()], analysis=analysis, context=narrowed)
    assert _reasons(report) == {RejectionReason.LINE_NOT_PRESENTED}


def test_an_anchor_that_is_not_in_the_file_is_rejected():
    report = _validate([_finding(anchor_snippet="os.system(user_input)")])
    assert _reasons(report) == {RejectionReason.ANCHOR_NOT_FOUND}


def test_a_credential_in_the_model_prose_is_rejected_before_persistence():
    report = _validate([_finding(suggested_fix="Use AIza" + "B" * 35 + " instead.")])
    assert _reasons(report) == {RejectionReason.SECRET_IN_TEXT}


@pytest.mark.parametrize(
    "text",
    [
        "ghp_" + "A" * 20,
        "AIza" + "B" * 35,
        "AKIA" + "C" * 16,
        "sk-ant-" + "D" * 24,
        "xoxb-" + "E" * 20,
    ],
)
def test_credential_shapes_are_detected(text):
    assert contains_secret(f"the value {text} appears here") is not None


def test_ordinary_prose_is_not_flagged_as_a_credential():
    assert contains_secret("This hard-codes an API key; move it to an env var.") is None


def test_a_malformed_span_is_rejected():
    report = _validate([_finding(line_end=GOOD_LINE - 5)])
    assert _reasons(report) == {RejectionReason.MALFORMED}


def test_an_absurdly_long_span_is_rejected():
    report = _validate([_finding(line_end=GOOD_LINE + 400)])
    assert _reasons(report) == {RejectionReason.MALFORMED}


def test_confidence_out_of_range_is_rejected():
    report = _validate([_finding(confidence=1.7)])
    assert _reasons(report) == {RejectionReason.MALFORMED}


# -- confidence thresholds ---------------------------------------------------


def test_a_low_confidence_finding_is_not_published():
    report = _validate([_finding(confidence=0.4)])
    assert _reasons(report) == {RejectionReason.LOW_CONFIDENCE}


def test_high_severity_gets_a_lower_bar_because_the_cost_matrix_is_asymmetric():
    validator = FindingValidator(min_confidence=0.6, min_confidence_high_severity=0.5)
    critical = _validate(
        [_finding(severity=FindingSeverity.CRITICAL, confidence=0.55)],
        validator=validator,
    )
    low = _validate([_finding(severity=FindingSeverity.LOW, confidence=0.55)], validator=validator)

    assert len(critical.validated) == 1, "a missed critical bug costs more"
    assert _reasons(low) == {RejectionReason.LOW_CONFIDENCE}


# -- anchor relocation (ADR-0011) -------------------------------------------


def test_an_off_by_two_claim_is_relocated_and_the_offset_is_recorded():
    report = _validate([_finding(line_start=GOOD_LINE + 2)])

    assert len(report.validated) == 1
    finding = report.validated[0]
    assert finding.line == GOOD_LINE
    assert finding.claimed_line == GOOD_LINE + 2
    assert finding.anchor_relocated is True


def test_an_exact_hit_at_the_cited_line_wins_without_searching():
    report = _validate([_finding()])
    assert report.validated[0].anchor_relocated is False


def test_a_claim_beyond_the_relocation_window_is_not_rescued():
    report = _validate([_finding(line_start=GOOD_LINE + 5)])
    assert report.validated == ()
    assert _reasons(report) == {RejectionReason.ANCHOR_NOT_FOUND}


def test_an_ambiguous_anchor_is_rejected_rather_than_guessed():
    """Two identical candidate lines: any tie-break would be a coin flip, and a
    comment on the wrong one of two identical lines looks correct."""
    from tests.review.fixtures import build_analysis as _build
    from tests.review.fixtures import build_file_analysis

    source = "def f():\n    total = 0\n    total += 1\n    total += 1\n    return total\n"
    patch = (
        "@@ -1,4 +1,5 @@\n"
        " def f():\n"
        "     total = 0\n"
        "+    total += 1\n"
        "     total += 1\n"
        "     return total\n"
    )
    analysis = _build([build_file_analysis(path="dup.py", source_text=source, patch=patch)])
    report = _validate(
        [_finding(file_path="dup.py", line_start=2, anchor_snippet="total += 1")],
        analysis=analysis,
    )
    assert report.validated == ()
    assert _reasons(report) == {RejectionReason.ANCHOR_AMBIGUOUS}


def test_relocation_cannot_move_a_finding_onto_a_line_outside_the_diff():
    """The window search re-applies the diff gate to every candidate."""
    report = _validate([_finding(line_start=6, anchor_snippet="import os")])
    assert report.validated == ()


def test_whitespace_normalised_anchors_still_match():
    report = _validate([_finding(anchor_snippet="handle  =  open(path)")])
    assert len(report.validated) == 1


def test_an_anchor_is_never_matched_case_insensitively():
    """Tolerance stops at whitespace. `OPEN` is not `open`."""
    report = _validate([_finding(anchor_snippet="HANDLE = OPEN(PATH)")])
    assert report.validated == ()


# -- deduplication -----------------------------------------------------------


def test_two_claims_about_the_same_thing_produce_one_finding():
    report = _validate(
        [
            _finding(finding_id="F001", title="Unclosed file handle"),
            _finding(finding_id="F002", title="unclosed  FILE   handle"),
        ]
    )
    assert len(report.validated) == 1
    assert _reasons(report) == {RejectionReason.DUPLICATE}


def test_the_fingerprint_ignores_the_model_generated_id():
    """Otherwise two runs over unchanged code would report the same issue
    twice, because the model regenerates its ids every response."""
    first = _validate([_finding(finding_id="F001")]).validated[0]
    second = _validate([_finding(finding_id="XYZ9")]).validated[0]
    assert first.fingerprint == second.fingerprint


def test_the_fingerprint_changes_when_the_location_changes():
    a = compute_fingerprint(file_path="a.py", line=10, side="RIGHT", category="bug", title="T")
    b = compute_fingerprint(file_path="a.py", line=11, side="RIGHT", category="bug", title="T")
    assert a != b


def test_the_fingerprint_is_derived_from_the_resolved_line_not_the_claimed_one():
    exact = _validate([_finding()]).validated[0]
    relocated = _validate([_finding(line_start=GOOD_LINE + 2)]).validated[0]
    assert exact.fingerprint == relocated.fingerprint


# -- limits and determinism --------------------------------------------------


def test_the_finding_cap_is_enforced_and_the_excess_is_counted():
    findings = [
        _finding(finding_id=f"F{i}", title=f"Issue {i}", line_start=GOOD_LINE) for i in range(5)
    ]
    # Distinct lines so they are not deduplicated away first.
    findings = [
        _finding(finding_id="F0", title="A", line_start=12, anchor_snippet="def load(self, key):"),
        _finding(
            finding_id="F1",
            title="B",
            line_start=13,
            anchor_snippet="path = os.path.join(self.root, key)",
        ),
        _finding(finding_id="F2", title="C", line_start=14, anchor_snippet="handle = open(path)"),
        _finding(finding_id="F3", title="D", line_start=15, anchor_snippet="data = handle.read()"),
    ]
    report = _validate(findings, validator=FindingValidator(max_findings=2))

    assert len(report.validated) == 2
    assert RejectionReason.OVER_LIMIT in _reasons(report)


def test_validation_is_deterministic_regardless_of_response_order():
    findings = [
        _finding(finding_id="A", title="A", line_start=12, anchor_snippet="def load(self, key):"),
        _finding(finding_id="B", title="B", line_start=15, anchor_snippet="data = handle.read()"),
        _finding(finding_id="C", title="C", line_start=14, anchor_snippet="handle = open(path)"),
    ]
    forward = _validate(findings)
    backward = _validate(list(reversed(findings)))

    assert [f.fingerprint for f in forward.validated] == [f.fingerprint for f in backward.validated]


def test_the_report_counts_every_claim_exactly_once():
    findings = [_finding(finding_id="A"), _finding(finding_id="B", file_path="nope.py")]
    report = _validate(findings)
    assert report.total_claims == 2
    assert len(report.validated) + len(report.rejected) == 2


def test_an_empty_response_is_a_valid_outcome():
    report = _validate([])
    assert report.validated == ()
    assert report.rejected == ()
    assert report.acceptance_ratio == 0.0


# -- what a ValidatedFinding guarantees --------------------------------------


def test_string_fields_are_truncated_so_model_length_limits_are_not_relied_on():
    report = _validate([_finding(title="X" * 400, description="Y" * 5000)])
    finding = report.validated[0]
    assert len(finding.title) <= 120
    assert len(finding.description) <= 1200


def test_the_source_line_is_carried_so_publication_needs_no_second_read():
    finding = _validate([_finding()]).validated[0]
    assert finding.source_line == "        handle = open(path)"
