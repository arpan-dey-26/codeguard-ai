"""The worker claim loop.

One iteration:

    1. claim a job     (UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED))
    2. COMMIT the claim   ← before doing any work
    3. run the handler
    4. mark completed, or schedule a retry, or fail

Step 2 is the part that is easy to get wrong. The claim is committed in its own
short transaction *before* the handler runs, for two reasons:

*   Holding a transaction open across a network call to GitHub holds a pooled
    connection for the whole duration and exhausts the pool under concurrency.
    This is the single most common way a pipeline like this falls over
    (architecture.md §22.4).
*   If the worker dies mid-handler, the claim is durable, so the sweeper can
    see a ``running`` job with a stale ``locked_at`` and reclaim it. An
    uncommitted claim would simply roll back and the job would look untouched —
    indistinguishable from a job that was never started.

``attempts`` is incremented at claim time rather than on failure. A worker that
dies without reporting anything therefore still burns an attempt, so a job that
reliably crashes its worker eventually dead-letters instead of taking down every
worker in turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from app.core.config import Settings
from app.core.correlation import new_correlation_id, scoped_context, set_correlation_id
from app.core.errors import CodeGuardError
from app.core.logging import get_logger
from app.db.repositories.auth import AuthRepository
from app.db.repositories.jobs import JobRepository
from app.db.repositories.reviews import ReviewRunRepository
from app.db.session import get_session_factory
from app.jobs.backoff import RetryPolicy, classify_error
from app.jobs.handlers import HANDLERS

logger = get_logger(__name__)


class JobRunner:
    """Claims and executes jobs until asked to stop."""

    def __init__(self, settings: Settings, *, worker_id: str | None = None) -> None:
        self._settings = settings
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._policy = RetryPolicy(
            max_attempts=settings.job_max_attempts,
            base_seconds=settings.job_backoff_base_seconds,
            cap_seconds=settings.job_backoff_cap_seconds,
        )

    # -- one iteration -----------------------------------------------------
    async def run_once(self) -> bool:
        """Claim and process at most one job. Returns True if one was handled."""
        factory = get_session_factory()

        # Transaction 1: claim, commit immediately.
        async with factory() as session:
            jobs = JobRepository(session)
            claimed = await jobs.claim(self.worker_id)
            await session.commit()

        if claimed is None:
            return False

        job_id = int(claimed["id"])
        job_type = str(claimed["job_type"])
        payload: dict[str, Any] = dict(claimed.get("payload") or {})
        attempts = int(claimed["attempts"])

        set_correlation_id(new_correlation_id())
        # Scoped, not merely bound: the worker loop is one long-lived task, so
        # an unreset binding would carry this job's repository and PR number
        # into the next job's log lines.
        with scoped_context(
            job_id=job_id,
            job_type=job_type,
            worker_id=self.worker_id,
            attempt=attempts,
            installation_id=payload.get("installation_id"),
            repository=payload.get("repository_full_name"),
            pull_request_number=payload.get("pull_request_number"),
        ):
            logger.info("job.claimed", job_id=job_id, job_type=job_type, attempt=attempts)
            # Transaction 2: the work itself.
            await self._execute(job_id, job_type, payload, attempts)
        return True

    async def _execute(
        self, job_id: int, job_type: str, payload: dict[str, Any], attempts: int
    ) -> None:
        handler = HANDLERS.get(job_type)
        factory = get_session_factory()

        if handler is None:
            # Unknown job type: permanent by definition. Retrying cannot make
            # a handler appear.
            async with factory() as session:
                await JobRepository(session).mark_failed(
                    job_id,
                    error=f"No handler registered for job type {job_type!r}",
                    error_type="UnknownJobType",
                    worker_id=self.worker_id,
                )
                await session.commit()
            logger.error("job.unknown_type", job_id=job_id, job_type=job_type)
            return

        try:
            async with factory() as session:
                await handler(payload, session, self._settings)
                await session.commit()
        # Deliberately broad: the exception CLASS is what drives the retry
        # decision below, so nothing may be swallowed or narrowed here.
        except Exception as exc:
            await self._handle_failure(job_id, attempts, exc)
            return

        async with factory() as session:
            await JobRepository(session).mark_completed(job_id, worker_id=self.worker_id)
            await session.commit()
        logger.info("job.completed", job_id=job_id, job_type=job_type)

    async def _handle_failure(self, job_id: int, attempts: int, exc: Exception) -> None:
        """Classify a failure and either reschedule it or end it.

        The decision comes from the exception *class*, never from a string
        match on its message.
        """
        error_type = classify_error(exc)
        retryable = self._policy.should_retry(attempts, exc)
        factory = get_session_factory()

        if retryable:
            delay = self._policy.delay_for(attempts, error=exc)
            async with factory() as session:
                await JobRepository(session).mark_retry(
                    job_id,
                    delay_seconds=delay,
                    error=str(exc),
                    error_type=error_type,
                    worker_id=self.worker_id,
                )
                await session.commit()
            logger.warning(
                "job.retry_scheduled",
                job_id=job_id,
                attempt=attempts,
                error_type=error_type,
                delay_seconds=round(delay, 2),
            )
            return

        exhausted = attempts >= self._policy.max_attempts
        async with factory() as session:
            await JobRepository(session).mark_failed(
                job_id,
                error=str(exc),
                error_type=error_type,
                # Dead-letter only when the budget ran out. A permanent error
                # is `failed`: it does not need a human to decide whether to
                # retry, because retrying is known to be pointless.
                dead=exhausted,
                worker_id=self.worker_id,
            )
            await session.commit()

        if isinstance(exc, CodeGuardError):
            logger.error(
                "job.failed",
                job_id=job_id,
                error_type=error_type,
                attempt=attempts,
                dead_lettered=exhausted,
                retryable=exc.retryable,
            )
        else:
            # Not one of ours: a bug. Full traceback, and it should page.
            logger.exception(
                "job.failed_unexpected",
                job_id=job_id,
                error_type=error_type,
                attempt=attempts,
            )

    # -- maintenance -------------------------------------------------------
    async def sweep(self) -> None:
        """Reclaim jobs from dead workers, dead-letter exhausted ones, and
        close review runs nobody is working on.

        The third of those is a Phase 5 addition. Sweeping ``jobs`` alone left
        ``review_runs`` rows stuck in ``running`` for ever whenever a retryable
        AI failure abandoned one — see ``ReviewRunRepository.abandon_stale``.

        The review timeout is deliberately a multiple of the job visibility
        timeout: a run may legitimately outlive one job attempt, and closing it
        early would mark a live review as abandoned.
        """
        factory = get_session_factory()
        async with factory() as session:
            jobs = JobRepository(session)
            await jobs.sweep_stale(timeout_seconds=self._settings.job_visibility_timeout_seconds)
            await jobs.dead_letter_exhausted()
            await ReviewRunRepository(session).abandon_stale(
                timeout_seconds=(
                    self._settings.job_visibility_timeout_seconds
                    * self._settings.review_run_abandon_timeout_multiplier
                )
            )
            # Identity housekeeping. Dead session and OAuth-state rows are kept
            # for a while — they answer "when did this person sign in?" — and
            # then removed, because an indefinitely retained record of somebody's
            # activity is a liability rather than an asset.
            auth = AuthRepository(session)
            await auth.purge_expired_oauth_states()
            await auth.purge_expired_sessions()
            await session.commit()

    # -- the loop ----------------------------------------------------------
    async def run_forever(self, stop: asyncio.Event) -> None:
        """Poll until asked to stop.

        Polling rather than ``LISTEN``/``NOTIFY``: polling is simpler, has no
        reconnection semantics to get wrong, and at this volume a two-second
        interval is indistinguishable from instant. The seam is here if the
        latency ever matters.
        """
        last_sweep = 0.0
        loop = asyncio.get_running_loop()

        while not stop.is_set():
            try:
                now = loop.time()
                if now - last_sweep >= self._settings.job_sweep_interval_seconds:
                    await self.sweep()
                    last_sweep = now

                did_work = await self.run_once()
                if did_work:
                    # Drain a backlog without waiting out the poll interval.
                    continue
            # Deliberately broad: the loop must survive anything a handler
            # can raise, or one bad job stops the whole worker.
            except Exception:
                logger.exception("worker.loop.error", worker_id=self.worker_id)
                await asyncio.sleep(self._settings.worker_poll_interval_seconds)
                continue

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=self._settings.worker_poll_interval_seconds
                )

        logger.info("worker.loop.stopped", worker_id=self.worker_id)

    async def run_until_drained(
        self, stop: asyncio.Event, *, drain_deadline_seconds: float
    ) -> None:
        """Run the loop, then give the in-flight job a bounded chance to finish.

        **Phase 5 addition.** ``run_forever`` observes ``stop`` only between
        jobs, and ``run_once`` has no cancellation point inside the handler. A
        review routinely outlives Docker's default 10-second SIGTERM grace — the
        Gemini timeout alone is 90 seconds — so the process was SIGKILLed
        mid-handler and its job sat ``running`` until the visibility timeout
        reclaimed it, fifteen minutes later.

        The fix is cooperative, not forceful: the loop stops taking *new* work
        immediately, and the current job is allowed to finish and report within
        a deadline that the deployment sets to match its stop grace period.
        Killing an in-flight review would not make it finish faster; it would
        only make the job invisible for longer.
        """
        task = asyncio.create_task(self.run_forever(stop))
        await stop.wait()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=drain_deadline_seconds)
        except TimeoutError:
            logger.warning(
                "worker.shutdown.drain_timeout",
                worker_id=self.worker_id,
                drain_deadline_seconds=drain_deadline_seconds,
                detail=(
                    "an in-flight job did not finish before the deadline; it "
                    "will be reclaimed by the visibility-timeout sweep"
                ),
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
