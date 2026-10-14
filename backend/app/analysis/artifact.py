"""The deterministic analysis artifact.

What Phase 2 produces and Phase 3 consumes. The contract that matters:

    Given the same repository, head SHA, source, diff and configuration,
    ``to_dict()`` returns byte-identical JSON.

Enforced by construction, not by hope:

*   no random ids — declaration identity is a content hash;
*   no wall-clock values inside the artifact — operational timings live on the
    job and the log record, outside the semantic output;
*   every collection is sorted before serialisation, so dict and set iteration
    order cannot leak into the result.

The artifact is **ephemeral**. It is not persisted to PostgreSQL in this
phase: architecture.md §16 defines no Phase 2 table, the inputs it derives from
are already stored (``changed_files`` from Phase 1) or reproducible from
GitHub, and it contains third-party source text — which §16.7 is explicit about
not storing. Phase 3 persists *findings*, which is a different thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.analysis.diff.models import FileChangeType, LineMap, PatchStatus
from app.analysis.languages.base import Declaration, ParseStatus
from app.analysis.mapping.mapper import FileMapping
from app.analysis.source.models import SourceFile, SourceStatus


class FileAnalysisOutcome(StrEnum):
    """The single most useful field in the artifact: what happened to a file."""

    ANALYSED = "analysed"
    ANALYSED_WITH_ERRORS = "analysed_with_errors"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FileAnalysis:
    """Everything Phase 2 determined about one file."""

    file_path: str
    old_path: str | None
    head_sha: str
    change_type: FileChangeType
    outcome: FileAnalysisOutcome
    source_status: SourceStatus
    patch_status: PatchStatus
    language: str | None = None
    parse_status: ParseStatus | None = None
    parser_backend: str = ""
    skip_reason: str | None = None
    line_count: int = 0
    changed_line_count: int = 0
    added_line_count: int = 0
    deleted_line_count: int = 0
    declarations: tuple[Declaration, ...] = ()
    mapping: FileMapping | None = None
    parse_error_count: int = 0
    warnings: tuple[str, ...] = ()
    truncated: bool = False

    # Kept out of the serialised artifact: these hold third-party source text.
    source: SourceFile | None = field(default=None, repr=False, compare=False)
    line_map: LineMap | None = field(default=None, repr=False, compare=False)

    @property
    def is_authoritative(self) -> bool:
        """Whether this analysis may be trusted for line validation.

        ``False`` for a truncated read or a file whose parse failed. Phase 3
        must refuse to anchor a finding against a non-authoritative file:
        declarations derived from a prefix describe a file that does not exist.
        """
        return (
            not self.truncated
            and self.outcome
            in (FileAnalysisOutcome.ANALYSED, FileAnalysisOutcome.ANALYSED_WITH_ERRORS)
            and self.source is not None
        )

    def to_dict(self) -> dict[str, Any]:
        """Deterministic, source-free projection."""
        return {
            "file_path": self.file_path,
            "old_path": self.old_path,
            "change_type": self.change_type.value,
            "outcome": self.outcome.value,
            "source_status": self.source_status.value,
            "patch_status": self.patch_status.value,
            "language": self.language,
            "parse_status": self.parse_status.value if self.parse_status else None,
            "parser_backend": self.parser_backend,
            "skip_reason": self.skip_reason,
            "line_count": self.line_count,
            "changed_line_count": self.changed_line_count,
            "added_line_count": self.added_line_count,
            "deleted_line_count": self.deleted_line_count,
            "parse_error_count": self.parse_error_count,
            "truncated": self.truncated,
            "authoritative": self.is_authoritative,
            "warnings": list(self.warnings),
            "declarations": [
                {
                    "id": d.declaration_id,
                    "kind": d.kind.value,
                    "name": d.name,
                    "qualified_name": d.qualified_name,
                    "start_line": d.start_line,
                    "end_line": d.end_line,
                    "start_column": d.start_column,
                    "end_column": d.end_column,
                    "parent_id": d.parent_id,
                    "modifiers": list(d.modifiers),
                    "signature": d.signature,
                }
                for d in sorted(self.declarations, key=Declaration.sort_key)
            ],
            "line_mappings": [
                {
                    "line": m.line,
                    "side": m.side,
                    "change_type": m.change_type,
                    "innermost_declaration_id": m.innermost_declaration_id,
                    "declaration_chain": list(m.declaration_chain),
                    "qualified_name": m.qualified_name,
                    "kind": m.kind,
                }
                for m in (self.mapping.mappings if self.mapping else ())
            ],
            "unmapped_lines": list(self.mapping.unmapped_lines) if self.mapping else [],
        }


@dataclass(frozen=True, slots=True)
class PullRequestAnalysis:
    """The whole pull request's structural analysis at one commit."""

    repository_full_name: str
    pull_request_number: int
    head_sha: str
    files: tuple[FileAnalysis, ...] = ()
    total_files: int = 0
    analysed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    total_source_bytes: int = 0
    budget_exhausted: bool = False
    files_truncated_by_github: bool = False
    parser_availability: dict[str, bool] = field(default_factory=dict)

    @property
    def changed_line_total(self) -> int:
        return sum(f.changed_line_count for f in self.files)

    @property
    def declaration_total(self) -> int:
        return sum(len(f.declarations) for f in self.files)

    def analysable(self) -> tuple[FileAnalysis, ...]:
        return tuple(f for f in self.files if f.is_authoritative)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic projection. Files sorted by path; no timestamps."""
        return {
            "repository": self.repository_full_name,
            "pull_request_number": self.pull_request_number,
            "head_sha": self.head_sha,
            "totals": {
                "files": self.total_files,
                "analysed": self.analysed_files,
                "skipped": self.skipped_files,
                "failed": self.failed_files,
                "changed_lines": self.changed_line_total,
                "declarations": self.declaration_total,
                "source_bytes": self.total_source_bytes,
            },
            "budget_exhausted": self.budget_exhausted,
            "files_truncated_by_github": self.files_truncated_by_github,
            "parser_availability": dict(sorted(self.parser_availability.items())),
            "files": [f.to_dict() for f in sorted(self.files, key=lambda f: f.file_path)],
        }


__all__ = [
    "FileAnalysis",
    "FileAnalysisOutcome",
    "PullRequestAnalysis",
]
