"""Job queue data access.

All queue SQL lives here. The claim query is the load-bearing one and is worth
reading closely:

    UPDATE jobs SET status = 'running', locked_at = now(), locked_by = :worker
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'queued' AND run_after <= now()
        ORDER BY priority, run_after
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING *;

``FOR UPDATE SKIP LOCKED`` is what makes a PostgreSQL queue genuinely viable.
It lets N workers pull from one table with zero coordination: each transaction
locks a row, and any other transaction *skips* locked rows instead of blocking
on them. Without ``SKIP LOCKED`` every worker queues behind the same row and
throughput collapses to that of a single worker.

The inner ``SELECT ... LIMIT 1`` with an outer ``UPDATE ... WHERE id =`` is
deliberate: it picks and locks exactly one row, then updates that row, so the
claim is atomic and the worker identity is written in the same statement that
takes the job.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.job import Job

logger = get_logger(__name__)

_CLAIM_SQL = text(
    """
    UPDATE jobs
       SET status = 'running',
           locked_at = now(),
           locked_by = :worker_id,
           started_at = COALESCE(started_at, now()),
           attempts = attempts + 1,
           updated_at = now()
     WHERE id = (
           SELECT id
             FROM jobs
            WHERE status = 'queued'
              AND run_after <= now()
            ORDER BY priority ASC, run_after ASC, id ASC
              FOR UPDATE SKIP LOCKED
            LIMIT 1
     )
    RETURNING id, job_type, payload, attempts, max_attempts, webhook_event_id;
    """
)

_SWEEP_SQL = text(
    """
    UPDATE jobs
       SET status = 'queued',
           locked_at = NULL,
           locked_by = NULL,
           last_error = COALESCE(last_error, 'reclaimed after visibility timeout'),
           last_error_type = COALESCE(last_error_type, 'VisibilityTimeout'),
           run_after = now(),
           updated_at = now()
     WHERE status = 'running'
       AND locked_at < now() - make_interval(secs => :timeout_seconds)
    RETURNING id, locked_by, attempts;
    """
)

_DEAD_LETTER_SQL = text(
    """
    UPDATE jobs
       SET status = 'dead',
           failed_at = now(),
           updated_at = now()
     WHERE status = 'queued'
       AND attempts >= max_attempts
    RETURNING id;
    """
)

_SUPERSEDE_SQL = text(
    """
    UPDATE jobs
       SET status = 'cancelled',
           updated_at = now(),
           last_error = 'superseded by a newer delivery for the same pull request',
           last_error_type = 'Superseded'
     WHERE status = 'queued'
       AND job_type = :job_type
       AND payload ->> 'repository_full_name' = :repository_full_name
       AND payload ->> 'pull_request_number' = :pull_request_number
       AND dedupe_key IS DISTINCT FROM :dedupe_key
    RETURNING id;
    """
)


# The lock fence.  Every statement that *releases* a job carries it.
#
# Phase 5 defect: the three release statements keyed on ``id`` alone. The
# sweeper reclaims a job whose worker has exceeded the visibility timeout, a
# second worker claims it, and the first worker — still alive, just slow — then
# marks the job completed or requeues it out from under the second. The fence
# makes a release conditional on still holding the lock, so a revoked worker's
# write matches zero rows instead of corrupting another worker's state.
#
# ``worker_id`` is nullable so the sweeper and the tests can release without
# claiming; passing ``None`` disables the fence, which is why every production
# call site passes the real id.
_FENCE = "AND (CAST(:worker_id AS TEXT) IS NULL OR locked_by = :worker_id)"


def _warn_if_fenced_out(rowcount: int, job_id: int, worker_id: str | None, action: str) -> None:
    """A zero-row release means this worker no longer owns the job.

    Not an error to raise — the work is already done or already reassigned, and
    raising here would turn a benign race into a failed job. It is logged at
    warning because a *sustained* stream of these means the visibility timeout
    is shorter than the real job duration, which is an operator's decision to
    make.
    """
    if rowcount == 0 and worker_id is not None:
        logger.warning(
            "jobs.release.fenced_out",
            job_id=job_id,
            action=action,
            detail="lock was revoked before the worker reported; release ignored",
        )


class JobRepository:
    """Transactional operations on the durable queue."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- producing ---------------------------------------------------------
    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        webhook_event_id: int | None = None,
        priority: int = 100,
        delay_seconds: int = 0,
        max_attempts: int = 3,
    ) -> Job | None:
        """Create a job, or return ``None`` when an equivalent one is already active.

        **No commit happens here.** The caller owns the transaction, which is
        precisely what allows the webhook row and its job to be written
        atomically (architecture.md §22.4). A repository that commits on the
        caller's behalf makes that invariant impossible to express.

        **Phase 5 fix — the dedupe key is a constraint, not a suggestion.**
        ``uq_jobs_dedupe_key_active`` is a partial unique index over
        ``status IN ('queued','running')``: at most one active job per key. The
        previous implementation inserted blind, so a second delivery that mapped
        to a live key raised ``IntegrityError`` — which surfaced as a 500, and,
        because the webhook row and the job share one transaction, rolled the
        *audit record* back with it. GitHub does not redeliver a failed
        delivery, so the event was lost with no trace that it had ever arrived.

        Two deliveries collapsing into one job is the index doing its job. It is
        an outcome to report, not an error to raise, so this returns ``None``
        and the caller records the delivery as deduplicated.

        A ``SAVEPOINT`` wraps the insert because a constraint violation poisons
        the enclosing transaction in PostgreSQL: without the nested block, the
        caller could no longer commit the webhook row either.
        """
        run_after = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        if dedupe_key is None:
            job = Job(
                job_type=job_type,
                status="queued",
                payload=payload,
                dedupe_key=None,
                webhook_event_id=webhook_event_id,
                priority=priority,
                attempts=0,
                max_attempts=max_attempts,
                run_after=run_after,
            )
            self._session.add(job)
            await self._session.flush()
            return job

        try:
            async with self._session.begin_nested():
                job = Job(
                    job_type=job_type,
                    status="queued",
                    payload=payload,
                    dedupe_key=dedupe_key,
                    webhook_event_id=webhook_event_id,
                    priority=priority,
                    attempts=0,
                    max_attempts=max_attempts,
                    run_after=run_after,
                )
                self._session.add(job)
                await self._session.flush()
        except IntegrityError:
            # The savepoint is rolled back; the caller's transaction is intact.
            logger.info(
                "jobs.enqueue.deduplicated",
                job_type=job_type,
                dedupe_key=dedupe_key,
                detail="an equivalent job is already queued or running",
            )
            return None
        return job

    async def supersede_queued_for_pull_request(
        self,
        *,
        job_type: str,
        repository_full_name: str,
        pull_request_number: int,
        keep_dedupe_key: str | None,
    ) -> list[int]:
        """Cancel older queued jobs for the same pull request.

        A developer pushing three commits in thirty seconds produces three
        deliveries. Analysing all three wastes work and produces overlapping
        results, so the older queued jobs are cancelled when a newer one
        arrives (architecture.md §8.5).

        Only ``queued`` jobs are touched. A job already ``running`` is left
        alone — cancelling mid-flight work is a different, harder problem, and
        the handler's own head-SHA check already makes a stale run harmless.
        """
        result = await self._session.execute(
            _SUPERSEDE_SQL,
            {
                "job_type": job_type,
                "repository_full_name": repository_full_name,
                # Compared as TEXT, never cast. PostgreSQL is free to evaluate
                # AND conditions in any order, so a single job whose payload
                # carries a non-numeric value here would make `::int` raise and
                # abort supersession for every other row in the statement.
                "pull_request_number": str(pull_request_number),
                "dedupe_key": keep_dedupe_key,
            },
        )
        return [row[0] for row in result.fetchall()]

    # -- consuming ---------------------------------------------------------
    async def claim(self, worker_id: str) -> dict[str, Any] | None:
        """Atomically claim one job, or return ``None`` when none is available."""
        result = await self._session.execute(_CLAIM_SQL, {"worker_id": worker_id})
        row = result.mappings().first()
        return dict(row) if row else None

    async def mark_completed(self, job_id: int, *, worker_id: str | None = None) -> None:
        result = await self._session.execute(
            text(
                f"""
                UPDATE jobs
                   SET status = 'completed', completed_at = now(),
                       locked_at = NULL, locked_by = NULL,
                       last_error = NULL, last_error_type = NULL,
                       updated_at = now()
                 WHERE id = :job_id {_FENCE}
                """
            ),
            {"job_id": job_id, "worker_id": worker_id},
        )
        _warn_if_fenced_out(
            cast(CursorResult[Any], result).rowcount, job_id, worker_id, "completed"
        )

    async def mark_retry(
        self,
        job_id: int,
        *,
        delay_seconds: float,
        error: str,
        error_type: str,
        worker_id: str | None = None,
    ) -> None:
        """Return a job to the queue after a retryable failure.

        ``attempts`` was already incremented at claim time, so a worker that
        dies without reporting anything still burns an attempt. That is
        intentional: a job that reliably crashes its worker would otherwise be
        retried forever, taking down every worker in turn.
        """
        result = await self._session.execute(
            text(
                f"""
                UPDATE jobs
                   SET status = 'queued',
                       locked_at = NULL, locked_by = NULL,
                       run_after = now() + make_interval(secs => :delay),
                       last_error = :error, last_error_type = :error_type,
                       updated_at = now()
                 WHERE id = :job_id {_FENCE}
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "delay": float(delay_seconds),
                "error": error[:2000],
                "error_type": error_type[:64],
            },
        )
        _warn_if_fenced_out(cast(CursorResult[Any], result).rowcount, job_id, worker_id, "retry")

    async def mark_failed(
        self,
        job_id: int,
        *,
        error: str,
        error_type: str,
        dead: bool = False,
        worker_id: str | None = None,
    ) -> None:
        """Terminal failure. ``dead`` marks it for human attention."""
        result = await self._session.execute(
            text(
                f"""
                UPDATE jobs
                   SET status = :status, failed_at = now(),
                       locked_at = NULL, locked_by = NULL,
                       last_error = :error, last_error_type = :error_type,
                       updated_at = now()
                 WHERE id = :job_id {_FENCE}
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "status": "dead" if dead else "failed",
                "error": error[:2000],
                "error_type": error_type[:64],
            },
        )
        _warn_if_fenced_out(cast(CursorResult[Any], result).rowcount, job_id, worker_id, "failed")

    # -- maintenance -------------------------------------------------------
    async def sweep_stale(self, *, timeout_seconds: int) -> list[int]:
        """Reclaim jobs whose worker died while holding them.

        A visibility timeout rather than a heartbeat: a heartbeat needs a
        background task per job and still fails if the process is SIGKILLed or
        the machine vanishes. A timeout sweep needs no cooperation from the
        dead worker, so it recovers from every failure mode.

        The timeout is deliberately generous. Reclaiming a job that is still
        running means doing the work twice; waiting longer for a genuinely
        crashed worker costs only latency.
        """
        result = await self._session.execute(_SWEEP_SQL, {"timeout_seconds": int(timeout_seconds)})
        rows = result.fetchall()
        if rows:
            logger.warning(
                "jobs.sweep.reclaimed",
                count=len(rows),
                job_ids=[r[0] for r in rows][:20],
            )
        return [r[0] for r in rows]

    async def dead_letter_exhausted(self) -> list[int]:
        """Move attempt-exhausted jobs to ``dead``.

        Never retried automatically after this. A job that has failed its full
        budget has a real problem, and automatic retry converts a visible
        failure into an invisible recurring cost.
        """
        result = await self._session.execute(_DEAD_LETTER_SQL)
        return [r[0] for r in result.fetchall()]
