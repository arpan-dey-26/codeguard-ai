"""The GitHub webhook endpoint.

The only unauthenticated public surface in the application, and therefore kept
trivially small: read bytes, verify HMAC, parse, record, enqueue, return. A
short endpoint is both fast and auditable, and both properties matter here.

Route: ``POST /api/v1/webhooks/github``

**Deviation from architecture.md §17.1**, which specifies ``/webhooks/github``.
Recorded as ADR-0005. The versioned path is preferable and costs nothing: a
GitHub App has exactly one webhook URL, so there is no migration concern. No
alias is added — a second unauthenticated public path is attack surface for no
benefit.

The order of operations below is the security property, not an implementation
detail (architecture.md §8.2):

    1. read RAW bytes            (never a parsed-then-reserialised object)
    2. enforce the size cap      (DoS guard *before* spending CPU on crypto)
    3. verify the HMAC           (constant-time)
    4. only now, parse the JSON
    5. validate the shape
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel

from app.api.deps import SessionDep, SettingsDep
from app.core.correlation import bind_context
from app.core.errors import (
    PayloadTooLargeError,
    WebhookPayloadError,
    WebhookSignatureError,
)
from app.core.logging import get_logger
from app.github.signature import verify_signature
from app.services.webhook_service import IngestOutcome, WebhookIngestService

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookAck(BaseModel):
    """What GitHub receives. Deliberately uninformative.

    Enough for a developer reading the App's delivery log to correlate with
    our records, and nothing that helps someone probing the endpoint.
    """

    status: str
    delivery_id: str


@router.post(
    "/github",
    response_model=WebhookAck,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a GitHub App webhook delivery",
)
async def receive_github_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> WebhookAck:
    # 1. Raw bytes. Anything that parses and re-serialises changes the bytes
    #    (key order, whitespace, unicode escaping) and breaks the HMAC.
    #
    #    The real ceiling is enforced upstream by ``BodySizeLimitMiddleware``,
    #    which counts bytes as they stream and rejects before this line buffers
    #    anything. Until Phase 5 this check was the only one — and it ran
    #    *after* ``request.body()``, so its own comment ("an oversized body
    #    should cost us nothing") described a property the code did not have.
    raw_body = await request.body()

    # 2. Second line of defence, kept deliberately. It costs one comparison and
    #    it holds even if the middleware is ever reordered or removed, which is
    #    the kind of change that looks harmless in a diff.
    if len(raw_body) > settings.github_webhook_max_body_bytes:
        raise PayloadTooLargeError("Webhook body exceeds the configured limit.")

    # 3. Verify. The failure reason is captured for our logs and never
    #    returned — telling a prober *why* verification failed is free
    #    information about the secret.
    failure = verify_signature(
        secret=(
            settings.github_webhook_secret.get_secret_value()
            if settings.github_webhook_secret
            else None
        ),
        body=raw_body,
        header_value=x_hub_signature_256,
    )
    if failure is not None:
        logger.warning(
            "webhook.signature.invalid",
            reason=failure.value,
            delivery_id=x_github_delivery,
            event_type=x_github_event,
            client_host=request.client.host if request.client else None,
        )
        raise WebhookSignatureError()

    # Headers are required, and a valid signature does not make them present.
    if not x_github_event or not x_github_delivery:
        raise WebhookPayloadError("Missing X-GitHub-Event or X-GitHub-Delivery header.")

    bind_context(delivery_id=x_github_delivery, event_type=x_github_event)

    # 4. Parse only after authenticity is established.
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WebhookPayloadError("Body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise WebhookPayloadError("Body is not a JSON object.")

    # 5. Record and enqueue — one transaction, committed here and nowhere else.
    service = WebhookIngestService(session, settings)
    result = await service.ingest(
        delivery_id=x_github_delivery,
        event_type=x_github_event,
        raw_body=raw_body,
        payload=payload,
    )
    await session.commit()

    if result.outcome is IngestOutcome.IGNORED:
        # 204: authentic, recorded, but not work for us. Distinct from 202 so
        # the App's delivery log stays informative for a developer.
        response.status_code = status.HTTP_204_NO_CONTENT

    return WebhookAck(status=result.outcome.value, delivery_id=result.delivery_id)
