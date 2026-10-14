"""Worker process entrypoint.

Run with ``python -m app.worker``.

Phase 0 shipped this process as a heartbeat with no queue behind it, to
establish the two-process topology from architecture.md §5.2 before anything
depended on it. Phase 1 replaces the heartbeat with the real claim loop: the
worker now claims jobs with ``FOR UPDATE SKIP LOCKED``, executes them, retries
transient failures with exponential backoff and jitter, and reclaims jobs
abandoned by workers that died.

Several of these processes may run concurrently against one database with no
coordination beyond the claim query itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import uuid

from app.core.config import get_settings
from app.core.correlation import bind_context, new_correlation_id, set_correlation_id
from app.core.logging import configure_logging, get_logger
from app.db.session import check_database_connection, dispose_engine
from app.jobs.runner import JobRunner

logger = get_logger(__name__)


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings)

    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    set_correlation_id(new_correlation_id())
    bind_context(worker_id=worker_id)

    # Checked, but not fatal: the database may simply be starting up alongside
    # us. The claim loop retries, so refusing to start would turn a five-second
    # ordering issue into a crash loop.
    db_ok = await check_database_connection()
    logger.info(
        "worker.startup.completed",
        worker_id=worker_id,
        database_reachable=db_ok,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        visibility_timeout_seconds=settings.job_visibility_timeout_seconds,
        max_attempts=settings.job_max_attempts,
        **settings.safe_summary(),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(signal_name: str) -> None:
        # Logged, because "why did the worker exit?" should have an answer in
        # the log rather than requiring an orchestrator's event history.
        if not stop.is_set():
            logger.info("worker.shutdown.signal", worker_id=worker_id, signal=signal_name)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop, sig.name)

    runner = JobRunner(settings, worker_id=worker_id)
    try:
        # Stop taking new work at once; let the in-flight job finish and report
        # within a bounded deadline. See ``JobRunner.run_until_drained``.
        await runner.run_until_drained(
            stop, drain_deadline_seconds=settings.worker_shutdown_drain_seconds
        )
    finally:
        await dispose_engine()
        logger.info("worker.shutdown.completed", worker_id=worker_id)


def main() -> None:
    # Interactive Ctrl-C is a normal way to stop a worker, not an error to
    # print a traceback for. The signal handler installed in `run_worker`
    # has already asked the loop to drain by the time this is reached.
    with contextlib.suppress(KeyboardInterrupt):  # pragma: no cover
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
