"""Webhook ingestion.

The hot path, and deliberately the shortest one in the system. GitHub records a
delivery as failed if the server does not respond within **10 seconds**, and —
critically — **GitHub does not automatically redeliver failed deliveries**.
A slow endpoint therefore loses events permanently.

So this service does exactly four things and then returns:

    verify → record → enqueue → commit

No GitHub API call, no file fetch, no analysis. All of that belongs to the
worker, after the acknowledgement has been sent.

The transactional invariant (architecture.md §22.4, NFR-R1):

    BEGIN
      INSERT webhook_events ... ON CONFLICT DO NOTHING RETURNING id
      INSERT jobs ...
    COMMIT

Both rows commit together or neither does. This is the transactional-outbox
property, and it is the single strongest argument for putting the queue in
PostgreSQL rather than in a second system: an acknowledged delivery can never
exist without the job that processes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.repositories.jobs import JobRepository
from app.db.repositories.webhook_events import WebhookEventRepository
from app.github.events import EventDecision, route_event
from app.github.payloads import EnvelopeRef
from app.github.signature import body_digest

_PULL_REQUEST_JOB_TYPES = frozenset({"review_pull_request", "sync_pull_request"})
"""Job types whose dedupe key and debounce are keyed on the head commit."""

logger = get_logger(__name__)


class IngestOutcome(StrEnum):
    QUEUED = "queued"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    COALESCED = "coalesced"
    """Authentic, recorded, and folded into a job that is already active.

    Distinct from DUPLICATE, which means the *delivery* was seen before. This
    is a new delivery whose work an in-flight job already covers — the partial
    unique index on ``dedupe_key`` is what decides, and a collapse there is the
    queue working as designed, not a failure.
    """


@dataclass(frozen=True, slots=True)
class IngestResult:
    outcome: IngestOutcome
    delivery_id: str
    webhook_event_id: int | None = None
    job_id: int | None = None
    reason: str | None = None


class WebhookIngestService:
    """Records a delivery and, when appropriate, enqueues its job."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._events = WebhookEventRepository(session)
        self._jobs = JobRepository(session)

    async def ingest(
        self,
        *,
        delivery_id: str,
        event_type: str,
        raw_body: bytes,
        payload: dict[str, object],
    ) -> IngestResult:
        """Ingest one verified delivery.

        The signature has already been checked by the caller: this service is
        only reached for authentic bytes. Authenticity is not validity though,
        so the payload shape is still parsed defensively.
        """
        envelope = _parse_envelope(payload)
        action = envelope.action
        is_draft = bool(envelope.pull_request and envelope.pull_request.draft)

        installation_id = envelope.installation.id if envelope.installation else None
        routing = route_event(
            event_type,
            action,
            is_draft=is_draft,
            has_installation=installation_id is not None,
        )

        repo = envelope.repository
        pr_number = envelope.pull_request_number

        log = logger.bind(
            delivery_id=delivery_id,
            event_type=event_type,
            action=action,
            installation_id=installation_id,
            repository=repo.full_name if repo else None,
            pull_request_number=pr_number,
        )

        # --- the atomic unit: event row (+ job row) --------------------------
        event_id = await self._events.insert_if_new(
            github_delivery_id=delivery_id,
            event_type=event_type,
            action=action,
            github_installation_id=installation_id,
            github_repo_id=repo.id if repo else None,
            repository_full_name=repo.full_name if repo else None,
            github_pr_number=pr_number,
            payload_digest=body_digest(raw_body),
            payload_bytes=len(raw_body),
            processing_status=("queued" if routing.decision is EventDecision.QUEUE else "ignored"),
            ignored_reason=(routing.ignore_reason.value if routing.ignore_reason else None),
            signature_valid=True,
        )

        if event_id is None:
            # The UNIQUE constraint rejected the insert: this delivery has been
            # seen. No job is created, nothing else happens, and the caller
            # returns 202 — a duplicate is a *successfully handled* delivery.
            log.info("webhook.delivery.duplicate")
            return IngestResult(IngestOutcome.DUPLICATE, delivery_id)

        if routing.decision is EventDecision.IGNORE:
            log.info(
                "webhook.delivery.ignored",
                reason=routing.ignore_reason.value if routing.ignore_reason else None,
            )
            return IngestResult(
                IngestOutcome.IGNORED,
                delivery_id,
                webhook_event_id=event_id,
                reason=routing.ignore_reason.value if routing.ignore_reason else None,
            )

        job_id = await self._enqueue(
            routing.job_type or "review_pull_request",
            event_id=event_id,
            envelope=envelope,
            action=action,
            installation_id=installation_id,
        )

        if job_id is None:
            # An equivalent job is already queued or running. The delivery is
            # authentic and is still recorded; only the duplicate work is
            # dropped. Marked on the audit row so the delivery log shows what
            # happened rather than an unexplained absence of a job.
            await self._events.mark_coalesced(event_id)
            log.info("webhook.delivery.coalesced", webhook_event_id=event_id)
            return IngestResult(IngestOutcome.COALESCED, delivery_id, webhook_event_id=event_id)

        log.info("webhook.delivery.queued", webhook_event_id=event_id, job_id=job_id)
        return IngestResult(
            IngestOutcome.QUEUED, delivery_id, webhook_event_id=event_id, job_id=job_id
        )

    async def _enqueue(
        self,
        job_type: str,
        *,
        event_id: int,
        envelope: EnvelopeRef,
        action: str | None,
        installation_id: int | None,
    ) -> int | None:
        repo = envelope.repository
        pr = envelope.pull_request

        payload: dict[str, object] = {
            "installation_id": installation_id,
            "repository_full_name": repo.full_name if repo else None,
            "github_repo_id": repo.id if repo else None,
            "pull_request_number": envelope.pull_request_number,
            "head_sha": pr.head.sha if pr else None,
            "action": action,
            "trigger": action,
            "webhook_event_id": event_id,
        }

        dedupe_key: str | None = None
        delay = 0
        if job_type in _PULL_REQUEST_JOB_TYPES and repo and pr:
            # Keyed on the commit *and the job type*, not the pull request: a
            # new push is genuinely new work, while a redelivery of the same
            # push is not. Including the job type keeps a sync job and a review
            # job for the same commit from cancelling each other out.
            dedupe_key = f"{job_type}:{repo.id}:{pr.number}:{pr.head.sha}"
            # Only `synchronize` is debounced. Three pushes in thirty seconds
            # produce three events; a short delay lets the newest supersede the
            # older ones before any work starts. `opened` is not delayed —
            # the first review should feel immediate.
            if action == "synchronize":
                delay = self._settings.synchronize_debounce_seconds
                await self._jobs.supersede_queued_for_pull_request(
                    job_type=job_type,
                    repository_full_name=repo.full_name,
                    pull_request_number=pr.number,
                    keep_dedupe_key=dedupe_key,
                )
        elif installation_id is not None:
            dedupe_key = f"{job_type}:{installation_id}"

        job = await self._jobs.enqueue(
            job_type=job_type,
            payload=payload,
            dedupe_key=dedupe_key,
            webhook_event_id=event_id,
            delay_seconds=delay,
            max_attempts=self._settings.job_max_attempts,
        )
        return int(job.id) if job is not None else None


def _parse_envelope(payload: dict[str, object]) -> EnvelopeRef:
    """Permissive first pass over any delivery.

    Never raises: the audit row must be written even for a payload shape we
    do not recognise, because "an event arrived and we could not read it" is
    exactly the situation where the record is most useful.
    """
    try:
        return EnvelopeRef.model_validate(payload)
    except ValidationError:
        logger.warning("webhook.envelope.unparsed")
        return EnvelopeRef()
