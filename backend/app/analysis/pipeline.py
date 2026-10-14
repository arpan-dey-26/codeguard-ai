"""The Phase 2 analysis pipeline.

    changed-file metadata (Phase 1)
        → classify          which files are worth reading, and why not
        → parse patch       unified diff → hunks
        → build LineMap     the ground truth for every later validation
        → fetch source      one blob, at the exact head SHA
        → parse             Tree-sitter / native ast → declarations
        → map               changed lines → innermost declaration + parents
        → PullRequestAnalysis

Three properties hold throughout:

*   **Per-file isolation.** Every stage is wrapped so that one malformed,
    oversized or unfetchable file produces a recorded outcome and the run
    continues. A pull request is not all-or-nothing.
*   **Nothing is executed.** Files are parsed. No import, exec, subprocess,
    build or install touches repository content, and the repository is never
    cloned.
*   **Determinism.** Files are processed in sorted path order and every
    collection is sorted before it reaches the artifact, so the result does not
    depend on dict ordering or on which file happened to be fetched first.

No AI provider is called here, and no GitHub comment is created. Phase 2 reads
and analyses; it does not publish.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.artifact import (
    FileAnalysis,
    FileAnalysisOutcome,
    PullRequestAnalysis,
)
from app.analysis.diff.models import FileChangeType, PatchStatus
from app.analysis.diff.parser import build_line_map, parse_patch
from app.analysis.languages import build_registry
from app.analysis.languages.base import AdapterRegistry, ParseStatus
from app.analysis.mapping.mapper import map_changed_lines
from app.analysis.source.classifier import (
    ClassificationLimits,
    classify,
    has_generated_marker,
)
from app.analysis.source.models import SourceStatus
from app.analysis.source.provider import (
    SourceProvider,
    SourceRequest,
    SourceRetrievalError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChangedFileInput:
    """What the pipeline needs about one file, from Phase 1's sync.

    A plain dataclass rather than the ORM row: the pipeline is testable with no
    database, which is the point of keeping GitHub and persistence concerns out
    of the analysis layer.
    """

    path: str
    change_type: FileChangeType
    patch: str | None = None
    previous_path: str | None = None
    blob_sha: str | None = None
    additions: int = 0
    deletions: int = 0
    is_binary: bool = False
    declared_size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Every limit, explicit. No magic numbers at a call site."""

    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 20_000_000
    max_changed_lines_per_file: int = 5_000
    max_files: int = 300

    def limits(self) -> ClassificationLimits:
        return ClassificationLimits(
            max_file_bytes=self.max_file_bytes,
            max_total_bytes=self.max_total_bytes,
            max_changed_lines_per_file=self.max_changed_lines_per_file,
        )


class AnalysisPipeline:
    """Runs Phase 2 for one pull request at one commit."""

    def __init__(
        self,
        source_provider: SourceProvider,
        *,
        registry: AdapterRegistry | None = None,
        config: AnalysisConfig | None = None,
    ) -> None:
        self._source = source_provider
        self._registry = registry or build_registry()
        self._config = config or AnalysisConfig()

    async def analyse(
        self,
        *,
        repository_full_name: str,
        pull_request_number: int,
        head_sha: str,
        changed_files: list[ChangedFileInput],
        files_truncated_by_github: bool = False,
    ) -> PullRequestAnalysis:
        limits = self._config.limits()
        # Sorted, so the order files are processed — and therefore which ones
        # hit the byte budget — is a property of the input, not of iteration.
        ordered = sorted(changed_files, key=lambda f: f.path)

        analyses: list[FileAnalysis] = []
        consumed_bytes = 0
        budget_exhausted = False

        for index, item in enumerate(ordered):
            if index >= self._config.max_files:
                analyses.append(
                    _skipped(
                        item,
                        head_sha,
                        SourceStatus.PR_BUDGET_EXCEEDED,
                        f"Exceeded the {self._config.max_files}-file analysis cap.",
                    )
                )
                budget_exhausted = True
                continue

            analysis, used = await self._analyse_file(
                item,
                head_sha=head_sha,
                limits=limits,
                remaining_bytes=self._config.max_total_bytes - consumed_bytes,
            )
            consumed_bytes += used
            if analysis.source_status is SourceStatus.PR_BUDGET_EXCEEDED:
                budget_exhausted = True
            analyses.append(analysis)

        analysed = sum(
            1
            for a in analyses
            if a.outcome in (FileAnalysisOutcome.ANALYSED, FileAnalysisOutcome.ANALYSED_WITH_ERRORS)
        )
        skipped = sum(1 for a in analyses if a.outcome is FileAnalysisOutcome.SKIPPED)
        failed = sum(1 for a in analyses if a.outcome is FileAnalysisOutcome.FAILED)

        logger.info(
            "analysis.pull_request.completed",
            repository=repository_full_name,
            pull_request_number=pull_request_number,
            head_sha=head_sha[:12],
            files_total=len(analyses),
            files_analysed=analysed,
            files_skipped=skipped,
            files_failed=failed,
            declarations=sum(len(a.declarations) for a in analyses),
            source_bytes=consumed_bytes,
            budget_exhausted=budget_exhausted,
        )

        return PullRequestAnalysis(
            repository_full_name=repository_full_name,
            pull_request_number=pull_request_number,
            head_sha=head_sha,
            files=tuple(sorted(analyses, key=lambda a: a.file_path)),
            total_files=len(analyses),
            analysed_files=analysed,
            skipped_files=skipped,
            failed_files=failed,
            total_source_bytes=consumed_bytes,
            budget_exhausted=budget_exhausted,
            files_truncated_by_github=files_truncated_by_github,
            parser_availability=self._registry.availability(),
        )

    async def _analyse_file(
        self,
        item: ChangedFileInput,
        *,
        head_sha: str,
        limits: ClassificationLimits,
        remaining_bytes: int,
    ) -> tuple[FileAnalysis, int]:
        # 1. Parse the patch. Never raises; a bad patch becomes a status.
        file_diff = parse_patch(
            item.patch,
            old_path=item.previous_path or item.path,
            new_path=None if item.change_type is FileChangeType.DELETED else item.path,
            change_type=item.change_type,
            is_binary=item.is_binary,
        )
        line_map = build_line_map(file_diff, head_sha=head_sha)
        changed_count = len(line_map.changed_lines)

        # 2. Classify before fetching — the whole point is not downloading a
        #    4 MB lockfile to discover it is a lockfile.
        verdict = classify(
            path=item.path,
            change_type=item.change_type,
            patch_status=file_diff.patch_status,
            changed_line_count=changed_count,
            limits=limits,
            declared_size_bytes=item.declared_size_bytes,
        )
        if not verdict.should_fetch:
            return (
                _skipped(item, head_sha, verdict.status, verdict.reason, file_diff, line_map),
                0,
            )

        if remaining_bytes <= 0:
            return (
                _skipped(
                    item,
                    head_sha,
                    SourceStatus.PR_BUDGET_EXCEEDED,
                    "Pull-request source budget exhausted before this file.",
                    file_diff,
                    line_map,
                ),
                0,
            )

        # 3. Fetch the head version. Deleted files never reach here.
        try:
            source = await self._source.get_source(
                SourceRequest(path=item.path, head_sha=head_sha, blob_sha=item.blob_sha)
            )
        except SourceRetrievalError as exc:
            return (
                _failed(
                    item, head_sha, SourceStatus.FETCH_FAILED, str(exc)[:200], file_diff, line_map
                ),
                0,
            )
        # Deliberately broad: per-file isolation is the requirement — one
        # file's parse failure must not abort the pipeline.
        except Exception as exc:
            logger.warning(
                "analysis.source.unexpected_error",
                path=item.path,
                error_type=type(exc).__name__,
            )
            return (
                _failed(
                    item,
                    head_sha,
                    SourceStatus.FETCH_FAILED,
                    f"Unexpected {type(exc).__name__} retrieving source.",
                    file_diff,
                    line_map,
                ),
                0,
            )

        used = source.byte_size
        if used > limits.max_file_bytes:
            return (
                _skipped(
                    item,
                    head_sha,
                    SourceStatus.TOO_LARGE,
                    f"Fetched file is {used} bytes, over the {limits.max_file_bytes} limit.",
                    file_diff,
                    line_map,
                ),
                used,
            )
        if source.line_count == 0:
            return (
                _skipped(item, head_sha, SourceStatus.EMPTY, "File is empty.", file_diff, line_map),
                used,
            )
        if has_generated_marker(source.text):
            # Only detectable with content in hand, which is why this check
            # lives here and not in the pre-fetch classifier.
            return (
                _skipped(
                    item,
                    head_sha,
                    SourceStatus.GENERATED,
                    "File carries a generated-code marker.",
                    file_diff,
                    line_map,
                ),
                used,
            )

        # 4. Parse. Adapters never raise on bad source.
        adapter = self._registry.for_language(verdict.language or "")
        if adapter is None:  # pragma: no cover — classify() already excluded this
            return (
                _skipped(
                    item,
                    head_sha,
                    SourceStatus.UNSUPPORTED_LANGUAGE,
                    f"No adapter registered for {verdict.language!r}.",
                    file_diff,
                    line_map,
                ),
                used,
            )
        parse_result = adapter.parse(source)

        # 5. Map changed lines onto declarations.
        mapping = map_changed_lines(
            line_map=line_map, declarations=parse_result.sorted_declarations()
        )

        outcome = (
            FileAnalysisOutcome.ANALYSED
            if parse_result.status is ParseStatus.OK
            else FileAnalysisOutcome.ANALYSED_WITH_ERRORS
            if parse_result.ok
            else FileAnalysisOutcome.FAILED
        )

        return (
            FileAnalysis(
                file_path=item.path,
                old_path=file_diff.old_path if file_diff.is_rename else None,
                head_sha=head_sha,
                change_type=item.change_type,
                outcome=outcome,
                source_status=SourceStatus.AVAILABLE,
                patch_status=file_diff.patch_status,
                language=verdict.language,
                parse_status=parse_result.status,
                parser_backend=parse_result.parser_backend,
                line_count=source.line_count,
                changed_line_count=changed_count,
                added_line_count=len(line_map.added_new_lines),
                deleted_line_count=len(line_map.deleted_old_lines),
                declarations=parse_result.sorted_declarations(),
                mapping=mapping,
                parse_error_count=parse_result.error_count,
                warnings=parse_result.warnings,
                truncated=source.truncated,
                source=source,
                line_map=line_map,
            ),
            used,
        )


def _base_kwargs(item: ChangedFileInput, head_sha: str, file_diff=None, line_map=None):  # type: ignore[no-untyped-def]
    return {
        "file_path": item.path,
        "old_path": item.previous_path,
        "head_sha": head_sha,
        "change_type": item.change_type,
        "patch_status": file_diff.patch_status if file_diff else PatchStatus.ABSENT_UNKNOWN,
        "changed_line_count": len(line_map.changed_lines) if line_map else 0,
        "added_line_count": len(line_map.added_new_lines) if line_map else 0,
        "deleted_line_count": len(line_map.deleted_old_lines) if line_map else 0,
        "line_map": line_map,
    }


def _skipped(item, head_sha, status, reason, file_diff=None, line_map=None):  # type: ignore[no-untyped-def]
    return FileAnalysis(
        outcome=FileAnalysisOutcome.SKIPPED,
        source_status=status,
        skip_reason=reason,
        **_base_kwargs(item, head_sha, file_diff, line_map),
    )


def _failed(item, head_sha, status, reason, file_diff=None, line_map=None):  # type: ignore[no-untyped-def]
    return FileAnalysis(
        outcome=FileAnalysisOutcome.FAILED,
        source_status=status,
        skip_reason=reason,
        **_base_kwargs(item, head_sha, file_diff, line_map),
    )


__all__ = ["AnalysisConfig", "AnalysisPipeline", "ChangedFileInput"]
