"""Webhook delivery ledger data access.

The whole module exists for one query:

    INSERT INTO webhook_events (...) VALUES (...)
    ON CONFLICT (github_delivery_id) DO NOTHING
    RETURNING id;

``ON CONFLICT DO NOTHING ... RETURNING`` returns a row on insert and **no row**
on conflict. That single statement is the idempotency mechanism: it is atomic,
it is enforced by the storage engine, and two concurrent deliveries of the same
event cannot both receive a row.

The alternative — ``SELECT`` then ``INSERT`` — is a race that both requests can
win, because nothing prevents a second transaction from passing the same check
before the first commits. The architecture calls this out explicitly
(§23.1), and it is the difference between an idempotency *convention* and an
idempotency *guarantee*.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.webhook_event import WebhookEvent

_INSERT_SQL = text(
    """
    INSERT INTO webhook_events (
        github_delivery_id, event_type, action,
        github_installation_id, github_repo_id, repository_full_name,
        github_pr_number, signature_valid, payload_digest, payload_bytes,
        processing_status, ignored_reason, received_at
    ) VALUES (
        :github_delivery_id, :event_type, :action,
        :github_installation_id, :github_repo_id, :repository_full_name,
        :github_pr_number, :signature_valid, :payload_digest, :payload_bytes,
        :processing_status, :ignored_reason, now()
    )
    ON CONFLICT (github_delivery_id) DO NOTHING
    RETURNING id;
    """
)


class WebhookEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_new(
        self,
        *,
        github_delivery_id: str,
        event_type: str,
        action: str | None,
        github_installation_id: int | None,
        github_repo_id: int | None,
        repository_full_name: str | None,
        github_pr_number: int | None,
        payload_digest: str,
        payload_bytes: int,
        processing_status: str,
        ignored_reason: str | None = None,
        signature_valid: bool = True,
    ) -> int | None:
        """Record a delivery. Returns its id, or ``None`` if already seen.

        ``None`` is the duplicate signal, and the caller turns it into a 202
        rather than an error: from GitHub's perspective a duplicate has been
        fully handled, and returning an error would make its delivery log show
        failures for correct behaviour (architecture.md §8.4).
        """
        result = await self._session.execute(
            _INSERT_SQL,
            {
                "github_delivery_id": github_delivery_id[:64],
                "event_type": event_type[:64],
                "action": action[:64] if action else None,
                "github_installation_id": github_installation_id,
                "github_repo_id": github_repo_id,
                "repository_full_name": (
                    repository_full_name[:512] if repository_full_name else None
                ),
                "github_pr_number": github_pr_number,
                "signature_valid": signature_valid,
                "payload_digest": payload_digest,
                "payload_bytes": payload_bytes,
                "processing_status": processing_status,
                "ignored_reason": ignored_reason[:64] if ignored_reason else None,
            },
        )
        row = result.first()
        return int(row[0]) if row else None

    async def mark_processed(self, event_id: int) -> None:
        await self._session.execute(
            text(
                """
                UPDATE webhook_events
                   SET processing_status = 'processed', processed_at = now()
                 WHERE id = :id
                """
            ),
            {"id": event_id},
        )

    async def mark_coalesced(self, event_id: int) -> None:
        """Record that this delivery folded into an already-active job.

        A terminal state: nothing further will process this row. Written so the
        audit trail distinguishes "we dropped duplicate work on purpose" from
        "a job should exist for this and does not".
        """
        await self._session.execute(
            text(
                """
                UPDATE webhook_events
                   SET processing_status = 'coalesced', processed_at = now(),
                       ignored_reason = 'duplicate_active_job'
                 WHERE id = :id
                """
            ),
            {"id": event_id},
        )

    async def mark_failed(self, event_id: int, *, error: str) -> None:
        await self._session.execute(
            text(
                """
                UPDATE webhook_events
                   SET processing_status = 'failed', processed_at = now(),
                       error_message = :error
                 WHERE id = :id
                """
            ),
            # Truncated and never containing payload content — the message is
            # ours, not GitHub's.
            {"id": event_id, "error": error[:2000]},
        )

    async def get_by_delivery_id(self, delivery_id: str) -> WebhookEvent | None:
        result = await self._session.execute(
            text("SELECT * FROM webhook_events WHERE github_delivery_id = :d"),
            {"d": delivery_id},
        )
        row: Any = result.mappings().first()
        if row is None:
            return None
        return WebhookEvent(**dict(row))
