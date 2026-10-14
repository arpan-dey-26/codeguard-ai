"""The durable job queue, against a real PostgreSQL.

These tests exist because the queue's correctness is *database* behaviour, not
Python behaviour. ``FOR UPDATE SKIP LOCKED``, partial unique indexes and
transactional atomicity cannot be verified against a mock or against SQLite —
SQLite has none of them. Testing against a different database than production
tests a different system (architecture.md §25.3).
"""

from __future__ import annotations

import asyncio

import pytest
from app.db.models.job import Job
from app.db.repositories.jobs import JobRepository
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import TEST_DATABASE_URL, requires_database

pytestmark = [pytest.mark.integration, requires_database]


@pytest.fixture
async def engine():  # type: ignore[no-untyped-def]
    eng = create_async_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):  # type: ignore[no-untyped-def]
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean(engine):  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM jobs"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM jobs"))


async def _enqueue(session_factory, **kwargs):  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        job = await JobRepository(session).enqueue(
            job_type=kwargs.pop("job_type", "sync_pull_request"),
            payload=kwargs.pop("payload", {"pull_request_number": 1}),
            **kwargs,
        )
        await session.commit()
        return job.id


# -- claiming -------------------------------------------------------------


async def test_a_queued_job_can_be_claimed(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _enqueue(session_factory)
    async with session_factory() as session:
        claimed = await JobRepository(session).claim("worker-a")
        await session.commit()
    assert claimed is not None and claimed["id"] == job_id
    assert claimed["attempts"] == 1  # incremented at claim time


async def test_claiming_sets_running_and_records_the_worker(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _enqueue(session_factory)
    async with session_factory() as session:
        await JobRepository(session).claim("worker-a")
        await session.commit()
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text("SELECT status, locked_by, locked_at, started_at FROM jobs WHERE id = :i"),
                    {"i": job_id},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "running"
    assert row["locked_by"] == "worker-a"
    assert row["locked_at"] is not None
    assert row["started_at"] is not None


async def test_an_empty_queue_returns_none(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        assert await JobRepository(session).claim("worker-a") is None


async def test_a_job_scheduled_in_the_future_is_not_claimed(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Powers both the synchronize debounce and retry backoff."""
    await _enqueue(session_factory, delay_seconds=3600)
    async with session_factory() as session:
        assert await JobRepository(session).claim("worker-a") is None


async def test_higher_priority_is_claimed_first(session_factory) -> None:  # type: ignore[no-untyped-def]
    await _enqueue(session_factory, priority=100, dedupe_key="low")
    urgent = await _enqueue(session_factory, priority=10, dedupe_key="high")
    async with session_factory() as session:
        claimed = await JobRepository(session).claim("worker-a")
        await session.commit()
    assert claimed is not None and claimed["id"] == urgent


# -- concurrency: the load-bearing test -----------------------------------


async def test_two_workers_never_claim_the_same_job(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The core guarantee of ``FOR UPDATE SKIP LOCKED``.

    Ten jobs, five concurrent workers, each claiming until the queue is empty.
    Every job must be claimed exactly once. Without ``SKIP LOCKED`` the workers
    would serialise behind one row; without the lock entirely, two workers
    would claim the same job.
    """
    for i in range(10):
        await _enqueue(session_factory, dedupe_key=f"job-{i}", payload={"n": i})

    async def worker(name: str) -> list[int]:
        got: list[int] = []
        while True:
            async with session_factory() as session:
                claimed = await JobRepository(session).claim(name)
                await session.commit()
            if claimed is None:
                return got
            got.append(int(claimed["id"]))
            await asyncio.sleep(0)  # yield, to interleave the workers

    results = await asyncio.gather(*(worker(f"worker-{i}") for i in range(5)))

    claimed_ids = [job_id for batch in results for job_id in batch]
    assert len(claimed_ids) == 10, "every job must be claimed"
    assert len(set(claimed_ids)) == 10, "no job may be claimed twice"


async def test_a_claimed_job_is_invisible_to_other_workers(session_factory) -> None:  # type: ignore[no-untyped-def]
    await _enqueue(session_factory)
    async with session_factory() as s1:
        first = await JobRepository(s1).claim("worker-a")
        await s1.commit()
    async with session_factory() as s2:
        second = await JobRepository(s2).claim("worker-b")
    assert first is not None
    assert second is None


# -- deduplication --------------------------------------------------------


async def test_the_partial_unique_index_blocks_a_second_active_job(session_factory) -> None:  # type: ignore[no-untyped-def]
    """ "At most one active job per key" is a database invariant, not a convention."""
    await _enqueue(session_factory, dedupe_key="sync_pr:1:42:abc")
    with pytest.raises(IntegrityError) as exc:
        async with session_factory() as session:
            await session.execute(
                insert(Job).values(
                    job_type="sync_pull_request", payload={}, dedupe_key="sync_pr:1:42:abc"
                )
            )
    assert "uq_jobs_dedupe_key_active" in str(exc.value) or "unique" in str(exc.value).lower()


async def test_a_completed_job_frees_its_dedupe_key(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A full unique index would forbid ever re-syncing a pull request."""
    job_id = await _enqueue(session_factory, dedupe_key="sync_pr:1:42:abc")
    async with session_factory() as session:
        await JobRepository(session).mark_completed(job_id)
        await session.commit()
    second = await _enqueue(session_factory, dedupe_key="sync_pr:1:42:abc")
    assert second != job_id


async def test_supersession_cancels_older_queued_jobs(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Three pushes in thirty seconds must not produce three syncs."""
    payload = {"repository_full_name": "acme/widgets", "pull_request_number": 42}
    old = await _enqueue(
        session_factory, payload=payload, dedupe_key="sync_pr:1:42:oldsha", delay_seconds=30
    )
    async with session_factory() as session:
        cancelled = await JobRepository(session).supersede_queued_for_pull_request(
            job_type="sync_pull_request",
            repository_full_name="acme/widgets",
            pull_request_number=42,
            keep_dedupe_key="sync_pr:1:42:newsha",
        )
        await session.commit()
    assert old in cancelled

    async with session_factory() as session:
        status = (
            await session.execute(text("SELECT status FROM jobs WHERE id = :i"), {"i": old})
        ).scalar_one()
    assert status == "cancelled"


async def test_supersession_leaves_a_running_job_alone(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Cancelling mid-flight work is a different, harder problem.

    The handler's own head-SHA check already makes a stale run harmless.
    """
    payload = {"repository_full_name": "acme/widgets", "pull_request_number": 42}
    job_id = await _enqueue(session_factory, payload=payload, dedupe_key="sync_pr:1:42:oldsha")
    async with session_factory() as session:
        await JobRepository(session).claim("worker-a")
        await session.commit()
    async with session_factory() as session:
        cancelled = await JobRepository(session).supersede_queued_for_pull_request(
            job_type="sync_pull_request",
            repository_full_name="acme/widgets",
            pull_request_number=42,
            keep_dedupe_key="sync_pr:1:42:newsha",
        )
        await session.commit()
    assert job_id not in cancelled


# -- state transitions ----------------------------------------------------


async def test_completion_clears_the_lock(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _enqueue(session_factory)
    async with session_factory() as session:
        repo = JobRepository(session)
        await repo.claim("worker-a")
        await repo.mark_completed(job_id)
        await session.commit()
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text("SELECT status, locked_by, completed_at FROM jobs WHERE id = :i"),
                    {"i": job_id},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "completed"
    assert row["locked_by"] is None
    assert row["completed_at"] is not None


async def test_retry_returns_the_job_to_the_queue_with_a_delay(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _enqueue(session_factory)
    async with session_factory() as session:
        repo = JobRepository(session)
        await repo.claim("worker-a")
        await repo.mark_retry(
            job_id, delay_seconds=60, error="github 503", error_type="TransientError"
        )
        await session.commit()
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT status, attempts, last_error_type, run_after > now() AS future "
                        "FROM jobs WHERE id = :i"
                    ),
                    {"i": job_id},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["last_error_type"] == "TransientError"
    assert row["future"] is True


async def test_permanent_failure_is_terminal(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _enqueue(session_factory)
    async with session_factory() as session:
        repo = JobRepository(session)
        await repo.claim("worker-a")
        await repo.mark_failed(job_id, error="bad payload", error_type="PermanentInputError")
        await session.commit()
    async with session_factory() as session:
        status = (
            await session.execute(text("SELECT status FROM jobs WHERE id = :i"), {"i": job_id})
        ).scalar_one()
    assert status == "failed"
    async with session_factory() as session:
        assert await JobRepository(session).claim("worker-b") is None


async def test_exhausted_attempts_dead_letter(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _enqueue(session_factory, max_attempts=1)
    async with session_factory() as session:
        repo = JobRepository(session)
        await repo.claim("worker-a")
        await repo.mark_failed(job_id, error="still down", error_type="TransientError", dead=True)
        await session.commit()
    async with session_factory() as session:
        status = (
            await session.execute(text("SELECT status FROM jobs WHERE id = :i"), {"i": job_id})
        ).scalar_one()
    assert status == "dead"


async def test_an_invalid_status_is_refused_by_the_check_constraint(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Important state is a constrained enum, not a free-text column."""
    job_id = await _enqueue(session_factory)
    async with session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE jobs SET status = 'nonsense' WHERE id = :i"), {"i": job_id}
            )
            await session.commit()


# -- stale job recovery ---------------------------------------------------


async def test_a_stale_running_job_is_reclaimed(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Recovers from a worker that was SIGKILLed or whose machine vanished."""
    job_id = await _enqueue(session_factory)
    async with session_factory() as session:
        await JobRepository(session).claim("dead-worker")
        await session.commit()
    # Simulate a worker that died 20 minutes ago.
    async with session_factory() as session:
        await session.execute(
            text("UPDATE jobs SET locked_at = now() - interval '20 minutes' WHERE id = :i"),
            {"i": job_id},
        )
        await session.commit()

    async with session_factory() as session:
        reclaimed = await JobRepository(session).sweep_stale(timeout_seconds=900)
        await session.commit()
    assert job_id in reclaimed

    async with session_factory() as session:
        claimed = await JobRepository(session).claim("worker-b")
        await session.commit()
    assert claimed is not None and claimed["id"] == job_id
    # The crashed attempt was still counted, so a poison job cannot loop forever.
    assert claimed["attempts"] == 2


async def test_a_recently_claimed_job_is_not_reclaimed(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Reclaiming a job that is still running means doing the work twice."""
    await _enqueue(session_factory)
    async with session_factory() as session:
        await JobRepository(session).claim("worker-a")
        await session.commit()
    async with session_factory() as session:
        assert await JobRepository(session).sweep_stale(timeout_seconds=900) == []


async def test_dead_lettering_picks_up_exhausted_jobs(session_factory) -> None:  # type: ignore[no-untyped-def]
    job_id = await _enqueue(session_factory, max_attempts=1)
    async with session_factory() as session:
        await session.execute(text("UPDATE jobs SET attempts = 1 WHERE id = :i"), {"i": job_id})
        await session.commit()
    async with session_factory() as session:
        dead = await JobRepository(session).dead_letter_exhausted()
        await session.commit()
    assert job_id in dead
