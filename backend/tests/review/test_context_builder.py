"""Context builder tests.

Three properties are asserted, because they are the three the validator and the
publisher depend on: the document is bounded, it is deterministic, and every
line number in it is a real line number in the real file at the real commit.
"""

from __future__ import annotations

import re

from app.analysis.artifact import FileAnalysis, FileAnalysisOutcome, PullRequestAnalysis
from app.review.context import (
    ContextBudget,
    ExclusionReason,
    build_review_context,
    select_regions,
)

from tests.review.fixtures import (
    FILE_PATH,
    HEAD_SHA,
    SOURCE,
    build_analysis,
    build_file_analysis,
    build_wide_analysis,
)

_LINE_RE = re.compile(r"^\s*(\d+) (.) \| (.*)$")


def _rendered_lines(document: str) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for line in document.splitlines():
        match = _LINE_RE.match(line)
        if match:
            out[int(match.group(1))] = (match.group(2), match.group(3))
    return out


# -- correctness of the rendered line numbers --------------------------------


def test_every_rendered_line_is_the_real_file_content_at_that_number():
    """The single property the anchor gate depends on."""
    analysis = build_analysis()
    context = build_review_context(analysis, budget=ContextBudget())
    source = analysis.files[0].source
    assert source is not None

    rendered = _rendered_lines(context.document)
    assert rendered, "the document must contain numbered source"
    for number, (_marker, text) in rendered.items():
        assert text == source.line(number), f"line {number} does not match the file"


def test_presented_lines_matches_what_was_actually_rendered():
    context = build_review_context(build_analysis(), budget=ContextBudget())
    rendered = set(_rendered_lines(context.document))
    assert context.presented_lines[FILE_PATH] == frozenset(rendered)


def test_changed_lines_are_marked_and_unchanged_lines_are_not():
    analysis = build_analysis()
    context = build_review_context(analysis, budget=ContextBudget())
    line_map = analysis.files[0].line_map
    assert line_map is not None

    for number, (marker, _text) in _rendered_lines(context.document).items():
        expected = "+" if line_map.is_changed(number) else " "
        assert marker == expected, f"line {number} carries the wrong marker"


def test_the_anchor_is_everything_after_the_pipe():
    """Stated in the prompt; asserted here so the two cannot drift apart."""
    context = build_review_context(build_analysis(), budget=ContextBudget())
    for line in context.document.splitlines():
        match = _LINE_RE.match(line)
        if match and match.group(3).strip():
            assert match.group(3) in SOURCE


# -- determinism -------------------------------------------------------------


def test_the_same_analysis_produces_a_byte_identical_document():
    first = build_review_context(build_analysis(), budget=ContextBudget())
    second = build_review_context(build_analysis(), budget=ContextBudget())
    assert first.document == second.document
    assert first.byte_size == second.byte_size


def test_file_order_in_the_document_is_path_order_not_selection_order():
    """Selection ranks by changed-line count; emission must not."""
    big = build_file_analysis(path="z_big.py")
    small = build_file_analysis(path="a_small.py")
    analysis = PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=HEAD_SHA,
        files=(big, small),
        total_files=2,
    )
    context = build_review_context(analysis, budget=ContextBudget())
    positions = [
        context.document.index("FILE: a_small.py"),
        context.document.index("FILE: z_big.py"),
    ]
    assert positions == sorted(positions)
    assert [f.file_path for f in context.included] == ["a_small.py", "z_big.py"]


# -- budgets -----------------------------------------------------------------


def test_the_file_budget_excludes_rather_than_truncates_mid_file():
    files = [build_file_analysis(path=f"pkg/mod{i}.py") for i in range(5)]
    analysis = PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=HEAD_SHA,
        files=tuple(files),
        total_files=5,
    )
    context = build_review_context(analysis, budget=ContextBudget(max_files=2))

    assert len(context.included) == 2
    assert context.truncated is True
    reasons = {entry.reason for entry in context.excluded}
    assert ExclusionReason.FILE_BUDGET in reasons


def test_the_byte_budget_is_enforced():
    files = [build_file_analysis(path=f"pkg/mod{i}.py") for i in range(5)]
    analysis = PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=HEAD_SHA,
        files=tuple(files),
        total_files=5,
    )
    context = build_review_context(analysis, budget=ContextBudget(max_bytes=1500))

    assert context.byte_size <= 1500
    assert context.truncated is True


def test_excluded_files_are_named_in_the_document_so_the_model_avoids_them():
    files = [build_file_analysis(path=f"pkg/mod{i}.py") for i in range(3)]
    analysis = PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=HEAD_SHA,
        files=tuple(files),
        total_files=3,
    )
    context = build_review_context(analysis, budget=ContextBudget(max_files=1))
    assert "FILES CHANGED BUT NOT SHOWN" in context.document
    for entry in context.excluded:
        assert entry.file_path in context.document


# -- what must never be sent -------------------------------------------------


def test_a_truncated_file_is_excluded_entirely():
    """Its line numbers describe a file that does not exist."""
    truncated = build_file_analysis(truncated=True)
    analysis = PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=HEAD_SHA,
        files=(truncated,),
        total_files=1,
    )
    context = build_review_context(analysis, budget=ContextBudget())

    assert context.is_empty
    assert context.excluded[0].reason == ExclusionReason.NOT_AUTHORITATIVE
    assert FILE_PATH not in _document_body(context.document)


def test_a_failed_parse_is_excluded_entirely():
    failed = build_file_analysis(outcome=FileAnalysisOutcome.FAILED)
    analysis = PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=HEAD_SHA,
        files=(failed,),
        total_files=1,
    )
    context = build_review_context(analysis, budget=ContextBudget())
    assert context.is_empty


def test_a_file_with_no_source_is_excluded():
    analysis_file = build_file_analysis()
    stripped = FileAnalysis(
        file_path=analysis_file.file_path,
        old_path=None,
        head_sha=analysis_file.head_sha,
        change_type=analysis_file.change_type,
        outcome=analysis_file.outcome,
        source_status=analysis_file.source_status,
        patch_status=analysis_file.patch_status,
        source=None,
        line_map=analysis_file.line_map,
    )
    analysis = PullRequestAnalysis(
        repository_full_name="acme/web",
        pull_request_number=7,
        head_sha=HEAD_SHA,
        files=(stripped,),
        total_files=1,
    )
    context = build_review_context(analysis, budget=ContextBudget())
    assert context.is_empty
    assert context.excluded[0].reason == ExclusionReason.NO_SOURCE


def test_an_enclosing_class_contributes_a_header_not_its_whole_body():
    """Otherwise one changed method drags every sibling method into the
    context, which is the opposite of scoping to the change.

    `Wide` spans lines 1-19 and is "touched" because `beta` changed. Only its
    header must appear; `gamma` (lines 17-19) must not.
    """
    analysis = build_wide_analysis()
    regions, _truncated = select_regions(
        analysis.files[0], budget=ContextBudget(lines_around_declaration=0)
    )
    covered = {n for start, end in regions for n in range(start, end + 1)}

    assert covered >= {7, 8, 9, 10, 11}, "the changed method is shown in full"
    assert 1 in covered, "the enclosing class header is shown"
    assert covered.isdisjoint({17, 18, 19}), "an untouched sibling method is not"


def test_omitted_regions_are_announced_so_the_model_knows_what_it_cannot_see():
    context = build_review_context(
        build_wide_analysis(), budget=ContextBudget(lines_around_declaration=0)
    )
    assert "... lines 3-6 omitted ..." in context.document


def _document_body(document: str) -> str:
    """The document minus its manifest, so a path listed as *excluded* does not
    count as the file being present."""
    marker = "FILE: "
    index = document.find("\n" + marker)
    return document[index:] if index != -1 else ""
