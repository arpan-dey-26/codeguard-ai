"""Persistence for review runs, findings and posted comments.

Raw SQL for the same reason as the Phase 1 repositories: every statement here
needs a PostgreSQL feature the ORM abstracts away or expresses awkwardly —
``ON CONFLICT ... DO NOTHING RETURNING``, ``FOR UPDATE``, a partial unique
index — and hiding those behind an ORM would obscure exactly the behaviour
that matters.

**Persist before publish.** ``persist_findings`` runs and commits before the
publisher touches GitHub (architecture.md §8.4 step 13). If publication then
fails, the analysis is not lost: the run degrades to ``partial_success`` and
every finding is still visible in the dashboard. The reverse ordering — publish
then persist — loses the work whenever the process dies in between, and gives
the user comments on GitHub that the dashboard cannot explain.

**Idempotent by construction.** ``persist_findings`` is
``ON CONFLICT (review_run_id, fingerprint) DO NOTHING``, so a worker that
crashed after persisting and before publishing can retry the whole step
without duplicating rows.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.core.logging import get_logger
from app.review.validator import RejectedFinding, ValidatedFinding

logger = get_logger(__name__)

_MAX_ERROR_MESSAGE = 2000


@dataclass(frozen=True, slots=True)
class PersistedFindings:
    """What persistence actually wrote.

    Returned instead of a bare id map because the review run stores a funnel,
    and the funnel must report **rows written**, not claims offered. Two
    rejected claims can collapse onto one row (same gate, same file, same
    line), and counting the claims would make ``findings_persisted`` exceed the
    number of rows a query can find — a dashboard number that cannot be
    reconciled with the table it claims to describe.
    """

    ids_by_fingerprint: dict[str, uuid.UUID]
    validated_rows: int = 0
    rejected_rows: int = 0

    @property
    def total_rows(self) -> int:
        return self.validated_rows + self.rejected_rows


class ReviewRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        pull_request_id: int,
        repository_id: int,
        head_sha: str,
        base_sha: str = "",
        trigger: str,
        webhook_event_id: int | None = None,
        attempt: int = 1,
    ) -> uuid.UUID | None:
        """Create the run row, or return ``None`` if it already exists.

        ``ON CONFLICT DO NOTHING`` on ``(pull_request_id, head_sha, attempt)``.
        A ``None`` return is not an error: it means another worker already
        claimed this exact (commit, attempt), and the caller should stop rather
        than produce a second review of the same code.
        """
        run_id = uuid7()
        result = await self._session.execute(
            text(
                """
                INSERT INTO review_runs (
                    id, pull_request_id, repository_id, webhook_event_id,
                    trigger, head_sha, base_sha, attempt, status, started_at,
                    created_at, updated_at
                ) VALUES (
                    :id, :pull_request_id, :repository_id, :webhook_event_id,
                    :trigger, :head_sha, :base_sha, :attempt, 'running', now(),
                    now(), now()
                )
                ON CONFLICT (pull_request_id, head_sha, attempt) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": run_id,
                "pull_request_id": pull_request_id,
                "repository_id": repository_id,
                "webhook_event_id": webhook_event_id,
                "trigger": trigger,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "attempt": attempt,
            },
        )
        row = result.first()
        return row[0] if row else None

    async def next_attempt(self, *, pull_request_id: int, head_sha: str) -> int:
        result = await self._session.execute(
            text(
                """
                SELECT COALESCE(MAX(attempt), 0) + 1
                FROM review_runs
                WHERE pull_request_id = :pull_request_id AND head_sha = :head_sha
                """
            ),
            {"pull_request_id": pull_request_id, "head_sha": head_sha},
        )
        return int(result.scalar_one())

    async def record_provider_call(
        self,
        run_id: uuid.UUID,
        *,
        provider: str,
        model_name: str,
        prompt_version: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        truncated: bool,
    ) -> None:
        await self._session.execute(
            text(
                """
                UPDATE review_runs SET
                    provider = :provider,
                    model_name = :model_name,
                    prompt_version = :prompt_version,
                    input_tokens = :input_tokens,
                    output_tokens = :output_tokens,
                    ai_latency_ms = :latency_ms,
                    ai_truncated = :truncated,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "provider": provider,
                "model_name": model_name[:200],
                "prompt_version": prompt_version[:32],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "truncated": truncated,
            },
        )

    async def record_context(
        self,
        run_id: uuid.UUID,
        *,
        files_changed: int,
        files_analysed: int,
        files_skipped: int,
        context_truncated: bool,
    ) -> None:
        await self._session.execute(
            text(
                """
                UPDATE review_runs SET
                    files_changed = :files_changed,
                    files_analysed = :files_analysed,
                    files_skipped = :files_skipped,
                    context_truncated = :context_truncated,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "files_changed": files_changed,
                "files_analysed": files_analysed,
                "files_skipped": files_skipped,
                "context_truncated": context_truncated,
            },
        )

    async def record_funnel(
        self,
        run_id: uuid.UUID,
        *,
        emitted: int,
        persisted: int,
        inline: int,
        summary: int,
        dashboard_only: int,
        dropped: int,
    ) -> None:
        """Store the validation funnel.

        These are historical facts, not aggregates: ``emitted`` counts claims
        that in many cases have no row anywhere, and ``dropped`` counts
        rejections that were never publishable. Neither is reconstructable from
        the surviving rows, which is precisely the criterion for storing a
        number rather than computing it (architecture.md §16.15).
        """
        await self._session.execute(
            text(
                """
                UPDATE review_runs SET
                    findings_emitted = :emitted,
                    findings_persisted = :persisted,
                    findings_inline = :inline,
                    findings_summary = :summary,
                    findings_dashboard_only = :dashboard_only,
                    findings_dropped = :dropped,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "emitted": emitted,
                "persisted": persisted,
                "inline": inline,
                "summary": summary,
                "dashboard_only": dashboard_only,
                "dropped": dropped,
            },
        )

    async def abandon_stale(self, *, timeout_seconds: int) -> list[uuid.UUID]:
        """Close review runs no worker is working on any more.

        **Phase 5 fix.** ``ReviewService`` re-raises a retryable AI failure so
        the runner can reschedule the job, leaving the run row ``running``. The
        comment there claimed "the stale-job sweeper reclaims it" — it does not:
        ``JobRepository.sweep_stale`` touches ``jobs`` and nothing else, and
        ``supersede_older_runs`` matches on ``head_sha <> :keep``, so the
        retry's own run (same commit) never supersedes its predecessor. Every
        retryable AI failure therefore leaked a permanently-``running`` row,
        which the dashboard counted in its run totals for ever.

        The timeout is a multiple of the job visibility timeout: by the time it
        elapses, any worker that could still be running this job has already
        been reclaimed by the job sweeper. Marking it ``failed`` with an
        explicit ``abandoned`` code is honest — the run did not complete and
        nobody is going to complete it — and it is distinguishable in the
        dashboard from a run that failed for a diagnosed reason.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE review_runs
                   SET status = 'failed',
                       error_code = COALESCE(error_code, 'abandoned'),
                       error_message = COALESCE(
                           error_message,
                           'No worker reported on this run before the timeout.'
                       ),
                       finished_at = now(),
                       duration_ms = CASE
                           WHEN started_at IS NOT NULL
                           THEN (EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int
                           ELSE NULL
                       END,
                       updated_at = now()
                 WHERE status = 'running'
                   AND started_at < now() - make_interval(secs => :timeout_seconds)
                RETURNING id
                """
            ),
            {"timeout_seconds": int(timeout_seconds)},
        )
        ids = [row[0] for row in result.fetchall()]
        if ids:
            logger.warning(
                "review_runs.abandoned",
                count=len(ids),
                timeout_seconds=timeout_seconds,
            )
        return ids

    async def finish(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        github_review_id: int | None = None,
        quality_score: float | None = None,
    ) -> str | None:
        """Close the run and compute its duration in the database.

        Returns the status the run **actually** ended in, which is not always
        the one requested — see the guard below — or ``None`` if the row is
        gone. The caller is expected to report the returned value rather than
        the one it asked for.

        ``duration_ms`` is derived from ``started_at`` and ``now()`` server
        side rather than from a Python clock, so it cannot be skewed by a
        worker whose clock differs from the database's.

        **Only a run that is still open can be closed.** ``WHERE ... AND status
        IN ('queued', 'running')`` was added in Phase 6, after a real-PostgreSQL
        test of the §14 interleaving:

            1  push A  → worker 1 opens run R1 (running)
            2  push B  → ``supersede_older_runs`` marks R1 superseded, and
                         worker 2 opens R2 for the new head
            3  worker 1, still in flight, calls ``finish(R1, 'succeeded')``

        Without the guard, step 3 moved R1 back out of ``superseded``. The run
        the system had already decided was obsolete then counted as a success:
        two succeeded runs for one pull request at two different commits, a
        ``duration_ms`` measured across the supersession, and a success rate
        that counts work whose output was thrown away. ``supersede_older_runs``
        exists to make those runs stop counting, and a flag that the next
        statement overwrites is not a mechanism.

        The same guard makes ``finish`` idempotent: a redelivered completion or
        a retried worker can no longer overwrite the first recorded outcome,
        which is the value an operator reads when asking why a review failed.

        Publication was never at risk from any of this — the publisher re-reads
        the head commit from GitHub before posting — so what this protects is
        the run record, not the pull request.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE review_runs SET
                    status = :status,
                    error_code = :error_code,
                    error_message = :error_message,
                    github_review_id = COALESCE(:github_review_id, github_review_id),
                    quality_score = :quality_score,
                    finished_at = now(),
                    duration_ms = CASE
                        WHEN started_at IS NOT NULL
                        THEN (EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int
                        ELSE NULL
                    END,
                    updated_at = now()
                WHERE id = :id
                  AND status IN ('queued', 'running')
                RETURNING status
                """
            ),
            {
                "id": run_id,
                "status": status,
                "error_code": error_code,
                "error_message": (error_message or None) and error_message[:_MAX_ERROR_MESSAGE],
                "github_review_id": github_review_id,
                "quality_score": quality_score,
            },
        )
        row = result.first()
        if row is not None:
            return str(row[0])

        # The guard declined. Report what the run actually is, so the caller
        # does not go on to describe a superseded run as a success.
        settled = await self._session.execute(
            text("SELECT status FROM review_runs WHERE id = :id"), {"id": run_id}
        )
        actual = settled.scalar_one_or_none()
        return str(actual) if actual is not None else None

    async def supersede_older_runs(self, *, pull_request_id: int, keep_head_sha: str) -> int:
        """Mark still-running reviews of *older* commits as superseded.

        Called when a new head arrives. Comparison is on ``head_sha``
        inequality rather than on time: the ordering that matters is "is this
        the commit we are now reviewing?", and two runs started milliseconds
        apart can otherwise both believe they are current.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE review_runs
                SET status = 'superseded', finished_at = now(), updated_at = now()
                WHERE pull_request_id = :pull_request_id
                  AND head_sha <> :keep_head_sha
                  AND status IN ('queued', 'running')
                """
            ),
            {"pull_request_id": pull_request_id, "keep_head_sha": keep_head_sha},
        )
        return cast(CursorResult[Any], result).rowcount or 0

    async def get(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        result = await self._session.execute(
            text("SELECT * FROM review_runs WHERE id = :id"), {"id": run_id}
        )
        row = result.mappings().first()
        return dict(row) if row else None


class FindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_findings(
        self,
        *,
        run_id: uuid.UUID,
        repository_id: int,
        pull_request_id: int,
        validated: Sequence[ValidatedFinding],
        rejected: Iterable[RejectedFinding] = (),
        changed_file_ids: dict[str, int] | None = None,
    ) -> PersistedFindings:
        """Insert this run's findings. Returns ``fingerprint -> finding id``.

        Validated findings become ``mapping_status = 'inline'``; rejected
        claims become ``dashboard_only`` and carry their rejection reason.
        **Both are persisted**, because the rejections are the dataset that
        makes the platform's own accuracy measurable (see
        ``app.db.models.finding``).

        Returns the ids of rows *this call* owns — including ones a previous
        attempt inserted — so a retry after a crash still gets the mapping it
        needs to publish. ``DO NOTHING`` alone would return nothing for
        pre-existing rows, and the publisher would then have no finding id to
        attach a comment to.
        """
        file_ids = changed_file_ids or {}
        ids: dict[str, uuid.UUID] = {}
        validated_rows = 0
        rejected_rows = 0

        for finding in validated:
            params = {
                "id": uuid7(),
                "review_run_id": run_id,
                "repository_id": repository_id,
                "pull_request_id": pull_request_id,
                "changed_file_id": file_ids.get(finding.file_path),
                "model_finding_id": finding.model_finding_id,
                "category": finding.category.value,
                "severity": finding.severity.value,
                "title": finding.title,
                "description": finding.description,
                "impact": finding.impact,
                "suggested_fix": finding.suggested_fix,
                "rationale": finding.rationale,
                "cwe_id": _normalise_cwe(finding.cwe_id),
                "file_path": finding.file_path,
                "claimed_line_start": finding.claimed_line,
                "claimed_line_end": finding.line_end,
                "resolved_line": finding.line,
                "anchor_offset": finding.line - finding.claimed_line,
                "side": finding.side.value,
                "anchor_snippet": finding.anchor_snippet,
                "anchor_verified": True,
                "model_confidence": round(finding.confidence, 2),
                "effective_confidence": round(finding.confidence, 2),
                "mapping_status": "inline",
                "rejection_reason": None,
                "fingerprint": finding.fingerprint,
                "declaration_id": finding.declaration_id,
                "qualified_name": finding.qualified_name,
            }
            finding_id, inserted = await self._insert_one(params)
            if finding_id is not None:
                ids[finding.fingerprint] = finding_id
            if inserted:
                validated_rows += 1

        for claim in rejected:
            fingerprint = _rejected_fingerprint(claim)
            params = {
                "id": uuid7(),
                "review_run_id": run_id,
                "repository_id": repository_id,
                "pull_request_id": pull_request_id,
                "changed_file_id": file_ids.get(claim.claimed_file),
                "model_finding_id": claim.model_finding_id,
                "category": "maintainability",
                "severity": "info",
                "title": f"Rejected claim ({claim.reason.value})",
                "description": claim.detail or claim.reason.value,
                "impact": "",
                "suggested_fix": None,
                "rationale": None,
                "cwe_id": None,
                "file_path": claim.claimed_file[:400] or "(unknown)",
                "claimed_line_start": max(claim.claimed_line, 0),
                "claimed_line_end": None,
                "resolved_line": None,
                "anchor_offset": None,
                "side": "RIGHT",
                "anchor_snippet": "",
                "anchor_verified": False,
                "model_confidence": 0.0,
                "effective_confidence": 0.0,
                "mapping_status": "dashboard_only",
                "rejection_reason": claim.reason.value,
                "fingerprint": fingerprint,
                "declaration_id": None,
                "qualified_name": "",
            }
            _id, inserted = await self._insert_one(params)
            if inserted:
                rejected_rows += 1

        return PersistedFindings(
            ids_by_fingerprint=ids,
            validated_rows=validated_rows,
            rejected_rows=rejected_rows,
        )

    async def _insert_one(self, params: dict[str, Any]) -> tuple[uuid.UUID | None, bool]:
        """Insert one finding. Returns ``(id, inserted_now)``.

        ``inserted_now`` distinguishes "this call wrote the row" from "a
        previous attempt of this same run already wrote it". The caller needs
        both: the id, to attach a comment, and the fact, to count rows rather
        than attempts.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO findings (
                    id, review_run_id, repository_id, pull_request_id, changed_file_id,
                    model_finding_id, category, severity, title, description, impact,
                    suggested_fix, rationale, cwe_id, file_path, claimed_line_start,
                    claimed_line_end, resolved_line, anchor_offset, side, anchor_snippet,
                    anchor_verified, model_confidence, effective_confidence,
                    mapping_status, rejection_reason, fingerprint, declaration_id,
                    qualified_name, status, created_at, updated_at
                ) VALUES (
                    :id, :review_run_id, :repository_id, :pull_request_id, :changed_file_id,
                    :model_finding_id, :category, :severity, :title, :description, :impact,
                    :suggested_fix, :rationale, :cwe_id, :file_path, :claimed_line_start,
                    :claimed_line_end, :resolved_line, :anchor_offset, :side, :anchor_snippet,
                    :anchor_verified, :model_confidence, :effective_confidence,
                    :mapping_status, :rejection_reason, :fingerprint, :declaration_id,
                    :qualified_name, 'open', now(), now()
                )
                ON CONFLICT (review_run_id, fingerprint) DO NOTHING
                RETURNING id
                """
            ),
            params,
        )
        row = result.first()
        if row is not None:
            return (row[0], True)
        # Already inserted by an earlier attempt of this same run. Read the
        # existing id rather than reporting failure: the row is ours and the
        # publisher needs its id.
        existing = await self._session.execute(
            text(
                """
                SELECT id FROM findings
                WHERE review_run_id = :review_run_id AND fingerprint = :fingerprint
                """
            ),
            {
                "review_run_id": params["review_run_id"],
                "fingerprint": params["fingerprint"],
            },
        )
        found = existing.first()
        return (found[0] if found else None, False)

    async def previously_reported(
        self, *, pull_request_id: int, fingerprints: Sequence[str]
    ) -> set[str]:
        """Fingerprints already published on this PR by an earlier run.

        Used to suppress re-posting the same issue after every push
        (FR-5.5). Restricted to findings that actually became comments: a
        finding rejected last time is not "already reported" and should be
        re-evaluated.
        """
        if not fingerprints:
            return set()
        result = await self._session.execute(
            text(
                """
                SELECT DISTINCT f.fingerprint
                FROM findings f
                JOIN review_comments c ON c.finding_id = f.id
                WHERE f.pull_request_id = :pull_request_id
                  AND f.fingerprint = ANY(:fingerprints)
                """
            ),
            {"pull_request_id": pull_request_id, "fingerprints": list(fingerprints)},
        )
        return {row[0] for row in result.all()}

    async def mark_summary_only(self, finding_ids: Sequence[uuid.UUID]) -> None:
        """Demote findings that could not be published inline.

        The database CHECK forbids ``inline`` without a verified anchor, so a
        demotion is always *away* from ``inline`` and never towards it.
        """
        if not finding_ids:
            return
        await self._session.execute(
            text(
                """
                UPDATE findings
                SET mapping_status = 'summary', updated_at = now()
                WHERE id = ANY(:ids) AND mapping_status = 'inline'
                """
            ),
            {"ids": list(finding_ids)},
        )


class ReviewCommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        finding_id: uuid.UUID,
        run_id: uuid.UUID,
        path: str,
        line: int,
        side: str,
        commit_sha: str,
        body_hash: str,
        github_review_id: int | None = None,
        github_comment_id: int | None = None,
        html_url: str | None = None,
        posted_at: datetime | None = None,
    ) -> bool:
        """Record a comment GitHub confirmed. ``False`` if already recorded.

        Written *after* GitHub's response, never before. A row here asserts
        "this exists on GitHub", and asserting that before GitHub agrees is how
        reconciliation ends up skipping a comment that was never posted.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO review_comments (
                    finding_id, review_run_id, github_review_id, github_comment_id,
                    path, line, start_line, side, commit_sha, body_hash, html_url, posted_at
                ) VALUES (
                    :finding_id, :run_id, :github_review_id, :github_comment_id,
                    :path, :line, NULL, :side, :commit_sha, :body_hash, :html_url, :posted_at
                )
                ON CONFLICT (finding_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "finding_id": finding_id,
                "run_id": run_id,
                "github_review_id": github_review_id,
                "github_comment_id": github_comment_id,
                "path": path,
                "line": line,
                "side": side,
                "commit_sha": commit_sha,
                "body_hash": body_hash,
                "html_url": html_url,
                "posted_at": posted_at or datetime.now(UTC),
            },
        )
        return result.first() is not None

    async def existing_body_hashes(self, run_id: uuid.UUID) -> set[str]:
        result = await self._session.execute(
            text("SELECT body_hash FROM review_comments WHERE review_run_id = :id"),
            {"id": run_id},
        )
        return {row[0] for row in result.all()}


def _normalise_cwe(value: str | None) -> str | None:
    """Return a CWE id only if it matches the stored format exactly.

    A wrong CWE is **dropped, not corrected** (architecture.md §12.1). Guessing
    at what the model meant would put a fabricated taxonomy reference on a
    security finding, which is worse than having none.
    """
    if not value:
        return None
    candidate = value.strip().upper()
    if not candidate.startswith("CWE-"):
        return None
    digits = candidate[4:]
    if digits.isdigit() and 1 <= len(digits) <= 4:
        return candidate
    return None


def _rejected_fingerprint(claim: RejectedFinding) -> str:
    """A fingerprint for a claim that never got one.

    Rejected claims still need a value for the NOT NULL column and the
    ``(review_run_id, fingerprint)`` unique index. Derived from the rejection's
    own identity so that re-running the persist step after a crash converges on
    the same row rather than inserting a second one.
    """

    material = (
        f"rejected\x00{claim.reason.value}\x00{claim.claimed_file}"
        f"\x00{claim.claimed_line}\x00{claim.model_finding_id}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "FindingRepository",
    "PersistedFindings",
    "ReviewCommentRepository",
    "ReviewRunRepository",
]
