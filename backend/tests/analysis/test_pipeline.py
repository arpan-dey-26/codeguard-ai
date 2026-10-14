"""End-to-end Phase 2 analysis, with no GitHub and no network.

Source comes from `InMemorySourceProvider` — a second honest implementation of
the `SourceProvider` contract, not a fake GitHub. Nothing here pretends an API
was called.
"""

from __future__ import annotations

import json

import pytest
from app.analysis.artifact import FileAnalysisOutcome
from app.analysis.diff.models import FileChangeType
from app.analysis.pipeline import AnalysisConfig, AnalysisPipeline, ChangedFileInput
from app.analysis.source.models import SourceStatus
from app.analysis.source.provider import InMemorySourceProvider

HEAD = "e" * 40

GOOD = """import os


class Invoice:
    def total(self):
        subtotal = 0
        for line in self.lines:
            subtotal += line.amount
        return subtotal
"""

BROKEN = "class Broken:\n    def m(self:\n        return\n"

PATCH = (
    "@@ -5,4 +5,5 @@ class Invoice:\n"
    "     def total(self):\n"
    "         subtotal = 0\n"
    "         for line in self.lines:\n"
    "-            subtotal += line\n"
    "+            subtotal += line.amount\n"
    "+        return subtotal\n"
)


async def run(files, provider=None, config=None):  # type: ignore[no-untyped-def]
    pipeline = AnalysisPipeline(
        provider or InMemorySourceProvider({"src/invoice.py": GOOD}), config=config
    )
    return await pipeline.analyse(
        repository_full_name="acme/widgets",
        pull_request_number=42,
        head_sha=HEAD,
        changed_files=files,
    )


def modified(path="src/invoice.py", patch=PATCH, **kw):  # type: ignore[no-untyped-def]
    return ChangedFileInput(path=path, change_type=FileChangeType.MODIFIED, patch=patch, **kw)


async def test_a_pull_request_is_analysed_end_to_end() -> None:
    result = await run([modified()])
    assert result.analysed_files == 1
    file = result.files[0]
    assert file.outcome is FileAnalysisOutcome.ANALYSED
    assert file.language == "python"
    assert file.parser_backend == "python-ast"
    assert file.is_authoritative
    assert {d.qualified_name for d in file.declarations} >= {"<module>", "Invoice", "Invoice.total"}
    assert file.mapping is not None and file.mapping.mappings


async def test_changed_lines_map_to_the_enclosing_method() -> None:
    result = await run([modified()])
    mapping = result.files[0].mapping
    assert mapping is not None
    assert {m.qualified_name for m in mapping.mappings} == {"Invoice.total"}


async def test_one_broken_file_does_not_stop_the_others() -> None:
    """Failure isolation: a pull request is not all-or-nothing."""
    provider = InMemorySourceProvider({"src/invoice.py": GOOD, "src/broken.py": BROKEN})
    result = await run(
        [modified(), modified(path="src/broken.py", patch="@@ -1,1 +1,2 @@\n ctx\n+x\n")],
        provider=provider,
    )
    by_path = {f.file_path: f for f in result.files}
    assert by_path["src/invoice.py"].outcome is FileAnalysisOutcome.ANALYSED
    assert by_path["src/broken.py"].outcome in (
        FileAnalysisOutcome.FAILED,
        FileAnalysisOutcome.ANALYSED_WITH_ERRORS,
    )
    assert result.analysed_files >= 1


async def test_an_unfetchable_file_fails_only_itself() -> None:
    provider = InMemorySourceProvider({"src/invoice.py": GOOD})  # missing the second file
    result = await run([modified(), modified(path="src/missing.py")], provider=provider)
    by_path = {f.file_path: f for f in result.files}
    assert by_path["src/missing.py"].outcome is FileAnalysisOutcome.FAILED
    assert by_path["src/missing.py"].source_status is SourceStatus.FETCH_FAILED
    assert by_path["src/invoice.py"].outcome is FileAnalysisOutcome.ANALYSED


@pytest.mark.parametrize(
    ("path", "change_type", "expected"),
    [
        ("src/gone.py", FileChangeType.DELETED, SourceStatus.DELETED),
        ("assets/logo.png", FileChangeType.MODIFIED, SourceStatus.BINARY),
        ("node_modules/a/i.js", FileChangeType.MODIFIED, SourceStatus.VENDORED),
        ("package-lock.json", FileChangeType.MODIFIED, SourceStatus.GENERATED),
        ("src/main.go", FileChangeType.MODIFIED, SourceStatus.UNSUPPORTED_LANGUAGE),
    ],
)
async def test_skipped_files_are_classified_with_a_reason(
    path: str, change_type: FileChangeType, expected: SourceStatus
) -> None:
    result = await run([ChangedFileInput(path=path, change_type=change_type, patch=PATCH)])
    file = result.files[0]
    assert file.outcome is FileAnalysisOutcome.SKIPPED
    assert file.source_status is expected
    assert file.skip_reason


async def test_a_deleted_file_is_never_fetched() -> None:
    """The provider holds nothing, so a fetch attempt would fail the file."""
    result = await run(
        [ChangedFileInput(path="src/gone.py", change_type=FileChangeType.DELETED, patch=None)],
        provider=InMemorySourceProvider({}),
    )
    assert result.files[0].source_status is SourceStatus.DELETED
    assert result.files[0].outcome is FileAnalysisOutcome.SKIPPED


async def test_missing_patch_is_skipped_not_treated_as_no_changes() -> None:
    result = await run([modified(patch=None)])
    assert result.files[0].source_status is SourceStatus.NO_PATCH


async def test_renames_retain_the_previous_path() -> None:
    provider = InMemorySourceProvider({"src/new.py": GOOD})
    result = await run(
        [
            ChangedFileInput(
                path="src/new.py",
                change_type=FileChangeType.RENAMED,
                previous_path="src/old.py",
                patch="@@ -1,1 +1,2 @@\n ctx\n+added\n",
            )
        ],
        provider=provider,
    )
    assert result.files[0].old_path == "src/old.py"


async def test_file_cap_is_enforced_and_recorded() -> None:
    files = [modified(path=f"src/f{i}.py") for i in range(5)]
    provider = InMemorySourceProvider({f"src/f{i}.py": GOOD for i in range(5)})
    result = await run(files, provider=provider, config=AnalysisConfig(max_files=2))
    assert result.budget_exhausted
    assert sum(1 for f in result.files if f.source_status is SourceStatus.PR_BUDGET_EXCEEDED) == 3


async def test_byte_budget_is_enforced() -> None:
    provider = InMemorySourceProvider({f"src/f{i}.py": GOOD for i in range(3)})
    result = await run(
        [modified(path=f"src/f{i}.py") for i in range(3)],
        provider=provider,
        config=AnalysisConfig(max_total_bytes=len(GOOD) + 1),
    )
    assert result.budget_exhausted
    assert result.total_source_bytes <= len(GOOD) * 2


async def test_oversized_file_is_skipped_never_silently_truncated() -> None:
    result = await run([modified()], config=AnalysisConfig(max_file_bytes=10))
    assert result.files[0].source_status is SourceStatus.TOO_LARGE
    assert not result.files[0].is_authoritative


async def test_generated_marker_in_content_is_detected() -> None:
    provider = InMemorySourceProvider(
        {"src/invoice.py": "# Code generated by protoc. DO NOT EDIT.\n" + GOOD}
    )
    result = await run([modified()], provider=provider)
    assert result.files[0].source_status is SourceStatus.GENERATED


# -- determinism ----------------------------------------------------------


async def test_the_artifact_is_byte_identical_across_runs() -> None:
    """§18: same inputs, same output. No UUIDs, no timestamps, no set order."""
    first = await run([modified()])
    second = await run([modified()])
    assert json.dumps(first.to_dict()) == json.dumps(second.to_dict())


async def test_file_order_in_the_artifact_does_not_depend_on_input_order() -> None:
    provider = InMemorySourceProvider({f"src/{n}.py": GOOD for n in ("a", "b", "c")})
    forward = await run([modified(path=f"src/{n}.py") for n in ("a", "b", "c")], provider=provider)
    reverse = await run([modified(path=f"src/{n}.py") for n in ("c", "b", "a")], provider=provider)
    assert json.dumps(forward.to_dict()) == json.dumps(reverse.to_dict())


async def test_the_artifact_contains_no_source_text() -> None:
    """The artifact is passed around and logged; source code must not be in it."""
    result = await run([modified()])
    rendered = json.dumps(result.to_dict())
    assert "subtotal += line.amount" not in rendered
    assert "import os" not in rendered


async def test_parser_availability_is_reported_honestly() -> None:
    """Deployments differ; the artifact must say which backends actually ran."""
    result = await run([modified()])
    assert result.parser_availability["python"] is True
    assert set(result.parser_availability) == {"python", "javascript", "typescript", "java"}
