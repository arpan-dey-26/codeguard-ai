"""Event routing — which deliveries become work, and which are acknowledged.

A valid signature proves GitHub sent the bytes. It says nothing about whether
the event is one we handle, so the event name is validated independently and
is never trusted merely because the HMAC passed.

Three outcomes, and the distinction between the last two matters:

    QUEUE     supported event and action  → a job is created
    IGNORE    authentic but not ours      → recorded, acknowledged, no job
    REJECT    malformed or unparseable    → 4xx, never retried

``IGNORE`` is recorded with a reason rather than dropped silently, so that
"the webhook fired but nothing happened" always has an answer in the database
(architecture.md §8.4). Returning an error for an unsubscribed event would
instead make GitHub's delivery log show failures for correct behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EventDecision(StrEnum):
    QUEUE = "queue"
    IGNORE = "ignore"


class IgnoreReason(StrEnum):
    UNSUPPORTED_EVENT = "unsupported_event"
    UNSUPPORTED_ACTION = "unsupported_action"
    DRAFT_PULL_REQUEST = "draft_pull_request"
    BOT_AUTHOR = "bot_author"
    PING = "ping"
    NO_INSTALLATION = "no_installation"


# Events the App subscribes to. Anything else is acknowledged and ignored.
# Least privilege applies to events, not only to permissions: every extra
# subscription is volume and attack surface for no requirement.
SUPPORTED_EVENTS: frozenset[str] = frozenset(
    {
        "pull_request",
        "installation",
        "installation_repositories",
        "ping",
    }
)

# Actions that should trigger a pull-request synchronisation.
#   opened            first review
#   synchronize       a new push to the PR head
#   reopened          the PR is live again
#   ready_for_review  a draft became a real PR — the first moment review is wanted
PULL_REQUEST_QUEUE_ACTIONS: frozenset[str] = frozenset(
    {"opened", "synchronize", "reopened", "ready_for_review"}
)

# Recorded for lifecycle accuracy but not queued: ``closed`` updates state, it
# does not warrant fetching the diff.
PULL_REQUEST_RECORDED_ACTIONS: frozenset[str] = frozenset({"closed", "edited"})

INSTALLATION_ACTIONS: frozenset[str] = frozenset(
    {"created", "deleted", "suspend", "unsuspend", "new_permissions_accepted"}
)

INSTALLATION_REPOSITORIES_ACTIONS: frozenset[str] = frozenset({"added", "removed"})


@dataclass(frozen=True, slots=True)
class RoutingResult:
    decision: EventDecision
    job_type: str | None = None
    ignore_reason: IgnoreReason | None = None


_IGNORE_UNSUPPORTED_EVENT = RoutingResult(
    EventDecision.IGNORE, ignore_reason=IgnoreReason.UNSUPPORTED_EVENT
)
_IGNORE_UNSUPPORTED_ACTION = RoutingResult(
    EventDecision.IGNORE, ignore_reason=IgnoreReason.UNSUPPORTED_ACTION
)


def route_event(
    event_type: str,
    action: str | None,
    *,
    is_draft: bool = False,
    has_installation: bool = True,
) -> RoutingResult:
    """Decide what to do with a delivery.

    Args:
        event_type: the ``X-GitHub-Event`` header value.
        action: the payload's ``action`` field, when the event has one.
        is_draft: whether a pull-request payload is a draft.
        has_installation: whether the payload identified an App installation.

    Pure, total and side-effect free, so every branch is directly testable.
    """
    if event_type not in SUPPORTED_EVENTS:
        return _IGNORE_UNSUPPORTED_EVENT

    if event_type == "ping":
        # GitHub sends this once on setup and expects a 2xx. Acknowledging it
        # is what makes the App's "recent deliveries" page show green.
        return RoutingResult(EventDecision.IGNORE, ignore_reason=IgnoreReason.PING)

    if event_type == "pull_request":
        if action is None:
            return _IGNORE_UNSUPPORTED_ACTION
        if action not in PULL_REQUEST_QUEUE_ACTIONS:
            return _IGNORE_UNSUPPORTED_ACTION
        # A draft PR is explicitly "not ready for review". Reviewing it wastes
        # work and annoys the author; ``ready_for_review`` is the event that
        # says the author now wants it, and that action is not filtered here.
        if is_draft and action != "ready_for_review":
            return RoutingResult(
                EventDecision.IGNORE, ignore_reason=IgnoreReason.DRAFT_PULL_REQUEST
            )
        if not has_installation:
            # Without an installation there is no token to authenticate with, so
            # the job could only ever fail. Enqueueing work that is guaranteed
            # to fail turns an ignorable event into retry noise and a
            # dead-lettered job.
            return RoutingResult(EventDecision.IGNORE, ignore_reason=IgnoreReason.NO_INSTALLATION)
        # Phase 3: a pull-request event now queues the full review, not just
        # the metadata sync. The review handler syncs first, so this is a
        # strict superset of the Phase 1 behaviour rather than a replacement.
        return RoutingResult(EventDecision.QUEUE, job_type="review_pull_request")

    if event_type == "installation":
        if action is None or action not in INSTALLATION_ACTIONS:
            return _IGNORE_UNSUPPORTED_ACTION
        return RoutingResult(EventDecision.QUEUE, job_type="sync_installation")

    if event_type == "installation_repositories":
        if action is None or action not in INSTALLATION_REPOSITORIES_ACTIONS:
            return _IGNORE_UNSUPPORTED_ACTION
        return RoutingResult(EventDecision.QUEUE, job_type="sync_repositories")

    return _IGNORE_UNSUPPORTED_EVENT  # pragma: no cover — defensive
