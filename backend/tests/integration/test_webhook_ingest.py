"""Webhook ingestion against a real PostgreSQL.

The two properties under test are the ones the architecture calls invariants,
and neither can be verified without a real database:

*   **Idempotency** — enforced by a UNIQUE constraint, not by an application
    check. The concurrency test below is the one that actually proves it: an
    ``if exists()`` guard passes this suite's sequential test and fails the
    concurrent one.
*   **Atomicity** — the webhook row and its job commit together or not at all.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from app.core.config import AppEnv, Settings
from app.services.webhook_service import IngestOutcome, WebhookIngestService
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import TEST_DATABASE_URL, requires_database

pytestmark = [pytest.mark.integration, requires_database]

SAMPLES = Path(__file__).resolve().parents[3] / "infrastructure" / "scripts" / "sample_payloads"


def _settings() -> Settings:
    return Settings(
        app_env=AppEnv.TEST,
        database_url=TEST_DATABASE_URL,
        log_level="WARNING",
        synchronize_debounce_seconds=20,
    )


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
        await conn.execute(text("DELETE FROM webhook_events"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM jobs"))
        await conn.execute(text("DELETE FROM webhook_events"))


def _payload(name: str = "pull_request_opened.json") -> tuple[bytes, dict]:
    raw = (SAMPLES / name).read_bytes()
    return raw, json.loads(raw)


async def _ingest(session_factory, delivery_id: str, name: str = "pull_request_opened.json"):  # type: ignore[no-untyped-def]
    raw, payload = _payload(name)
    async with session_factory() as session:
        result = await WebhookIngestService(session, _settings()).ingest(
            delivery_id=delivery_id,
            event_type="issues" if name == "unsupported_event.json" else "pull_request",
            raw_body=raw,
            payload=payload,
        )
        await session.commit()
        return result


async def _count(session_factory, table: str) -> int:
    async with session_factory() as session:
        return int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


# -- the transactional invariant ------------------------------------------


async def test_a_supported_event_creates_exactly_one_event_and_one_job(session_factory) -> None:  # type: ignore[no-untyped-def]
    result = await _ingest(session_factory, "delivery-1")
    assert result.outcome is IngestOutcome.QUEUED
    assert result.webhook_event_id is not None
    assert result.job_id is not None
    assert await _count(session_factory, "webhook_events") == 1
    assert await _count(session_factory, "jobs") == 1


async def test_the_job_references_its_originating_delivery(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Traceability from a job back to the delivery that caused it."""
    result = await _ingest(session_factory, "delivery-trace")
    async with session_factory() as session:
        linked = (
            await session.execute(
                text("SELECT webhook_event_id FROM jobs WHERE id = :i"), {"i": result.job_id}
            )
        ).scalar_one()
    assert linked == result.webhook_event_id


async def test_a_rolled_back_transaction_leaves_neither_row(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The invariant: a webhook can never exist without its job."""
    raw, payload = _payload()
    async with session_factory() as session:
        await WebhookIngestService(session, _settings()).ingest(
            delivery_id="delivery-rollback",
            event_type="pull_request",
            raw_body=raw,
            payload=payload,
        )
        await session.rollback()
    assert await _count(session_factory, "webhook_events") == 0
    assert await _count(session_factory, "jobs") == 0


# -- idempotency ----------------------------------------------------------


async def test_a_duplicate_delivery_creates_nothing_new(session_factory) -> None:  # type: ignore[no-untyped-def]
    first = await _ingest(session_factory, "delivery-dup")
    second = await _ingest(session_factory, "delivery-dup")
    assert first.outcome is IngestOutcome.QUEUED
    assert second.outcome is IngestOutcome.DUPLICATE
    assert second.job_id is None
    assert await _count(session_factory, "webhook_events") == 1
    assert await _count(session_factory, "jobs") == 1


async def test_concurrent_duplicate_deliveries_produce_one_job(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The test that distinguishes a guarantee from a convention.

    An application-level ``if exists()`` check passes the sequential test above
    and fails this one, because nothing stops a second transaction passing the
    same check before the first commits. Only the UNIQUE constraint holds here.
    """
    raw, payload = _payload()

    async def ingest_once() -> IngestOutcome:
        async with session_factory() as session:
            try:
                result = await WebhookIngestService(session, _settings()).ingest(
                    delivery_id="delivery-race",
                    event_type="pull_request",
                    raw_body=raw,
                    payload=payload,
                )
                await session.commit()
                return result.outcome
            except Exception:
                # A loser of the race may surface as an integrity error rather
                # than as a clean duplicate; both outcomes are correct, and
                # neither may leave a second job behind.
                await session.rollback()
                return IngestOutcome.DUPLICATE

    outcomes = await asyncio.gather(*(ingest_once() for _ in range(8)))

    assert outcomes.count(IngestOutcome.QUEUED) == 1
    assert await _count(session_factory, "webhook_events") == 1
    assert await _count(session_factory, "jobs") == 1


async def test_different_deliveries_are_independent(session_factory) -> None:  # type: ignore[no-untyped-def]
    await _ingest(session_factory, "delivery-a")
    await _ingest(session_factory, "delivery-b", "pull_request_synchronize.json")
    assert await _count(session_factory, "webhook_events") == 2
    assert await _count(session_factory, "jobs") == 2


# -- ignored events -------------------------------------------------------


async def test_an_unsupported_event_is_recorded_but_creates_no_job(session_factory) -> None:  # type: ignore[no-untyped-def]
    """ "The webhook fired but nothing happened" must have an answer in the database."""
    result = await _ingest(session_factory, "delivery-issues", "unsupported_event.json")
    assert result.outcome is IngestOutcome.IGNORED
    assert result.reason == "unsupported_event"
    assert await _count(session_factory, "webhook_events") == 1
    assert await _count(session_factory, "jobs") == 0

    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT processing_status, ignored_reason FROM webhook_events "
                        "WHERE github_delivery_id = 'delivery-issues'"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["processing_status"] == "ignored"
    assert row["ignored_reason"] == "unsupported_event"


async def test_a_draft_pull_request_creates_no_job(session_factory) -> None:  # type: ignore[no-untyped-def]
    result = await _ingest(session_factory, "delivery-draft", "pull_request_draft.json")
    assert result.outcome is IngestOutcome.IGNORED
    assert result.reason == "draft_pull_request"
    assert await _count(session_factory, "jobs") == 0


# -- what is and is not persisted ----------------------------------------


async def test_the_payload_body_is_not_stored(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Only a digest. Third-party content has no retention value here."""
    await _ingest(session_factory, "delivery-digest")
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT payload_digest, payload_bytes, github_installation_id, "
                        "github_repo_id, github_pr_number, repository_full_name "
                        "FROM webhook_events WHERE github_delivery_id = 'delivery-digest'"
                    )
                )
            )
            .mappings()
            .one()
        )

    assert len(row["payload_digest"]) == 64
    assert row["payload_bytes"] > 0
    # The identifiers needed for audit and debugging are extracted; the body is not.
    assert row["github_installation_id"] == 77000001
    assert row["github_repo_id"] == 550001
    assert row["github_pr_number"] == 42
    assert row["repository_full_name"] == "acme-demo/widget-service"

    async with session_factory() as session:
        columns = {
            r[0]
            for r in (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'webhook_events'"
                    )
                )
            ).fetchall()
        }
    assert "payload" not in columns
    assert "body" not in columns


async def test_synchronize_jobs_are_debounced(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A push burst must not produce a burst of syncs."""
    result = await _ingest(session_factory, "delivery-sync", "pull_request_synchronize.json")
    async with session_factory() as session:
        future = (
            await session.execute(
                text("SELECT run_after > now() AS future FROM jobs WHERE id = :i"),
                {"i": result.job_id},
            )
        ).scalar_one()
    assert future is True


async def test_opened_jobs_are_not_debounced(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The first review should feel immediate."""
    result = await _ingest(session_factory, "delivery-open")
    async with session_factory() as session:
        future = (
            await session.execute(
                text("SELECT run_after > now() AS future FROM jobs WHERE id = :i"),
                {"i": result.job_id},
            )
        ).scalar_one()
    assert future is False


async def test_the_job_payload_carries_ids_not_content(session_factory) -> None:  # type: ignore[no-untyped-def]
    result = await _ingest(session_factory, "delivery-payload")
    async with session_factory() as session:
        payload = (
            await session.execute(
                text("SELECT payload FROM jobs WHERE id = :i"), {"i": result.job_id}
            )
        ).scalar_one()
    assert payload["repository_full_name"] == "acme-demo/widget-service"
    assert payload["pull_request_number"] == 42
    assert payload["installation_id"] == 77000001
    # No titles, no bodies, no diffs — the worker re-reads what it needs.
    assert "title" not in payload
    assert "patch" not in payload
