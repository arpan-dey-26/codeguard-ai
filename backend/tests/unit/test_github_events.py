"""Event routing and payload parsing.

Pure logic, so every branch is directly reachable. The security-relevant
property under test: a valid signature proves GitHub sent the bytes, and
nothing more — the event name is still validated independently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.github.events import (
    PULL_REQUEST_QUEUE_ACTIONS,
    SUPPORTED_EVENTS,
    EventDecision,
    IgnoreReason,
    route_event,
)
from app.github.payloads import EnvelopeRef, PullRequestEvent

SAMPLES = Path(__file__).resolve().parents[3] / "infrastructure" / "scripts" / "sample_payloads"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


# -- routing --------------------------------------------------------------


@pytest.mark.parametrize("action", sorted(PULL_REQUEST_QUEUE_ACTIONS))
def test_supported_pull_request_actions_are_queued(action: str) -> None:
    """Phase 3 changed the job type from ``sync_pull_request`` to
    ``review_pull_request``. Not a replacement but a superset: the review
    handler syncs first, then reviews, so everything Phase 1 did still
    happens."""
    result = route_event("pull_request", action)
    assert result.decision is EventDecision.QUEUE
    assert result.job_type == "review_pull_request"


@pytest.mark.parametrize("action", ["closed", "labeled", "edited", "assigned", "review_requested"])
def test_other_pull_request_actions_are_ignored_not_rejected(action: str) -> None:
    """Ignored, with a recorded reason — never an error.

    Returning an error would make GitHub's delivery log show failures for
    correct behaviour.
    """
    result = route_event("pull_request", action)
    assert result.decision is EventDecision.IGNORE
    assert result.ignore_reason is IgnoreReason.UNSUPPORTED_ACTION


@pytest.mark.parametrize("event", ["issues", "push", "star", "workflow_run", "fork"])
def test_unsubscribed_events_are_ignored(event: str) -> None:
    result = route_event(event, "opened")
    assert result.decision is EventDecision.IGNORE
    assert result.ignore_reason is IgnoreReason.UNSUPPORTED_EVENT


def test_a_forged_event_name_cannot_smuggle_work() -> None:
    """Signature validity does not make an event name trustworthy."""
    assert route_event("pull_request_review_comment", "created").decision is EventDecision.IGNORE
    assert route_event("../../admin", "opened").decision is EventDecision.IGNORE
    assert route_event("", None).decision is EventDecision.IGNORE


def test_draft_pull_requests_are_not_reviewed() -> None:
    result = route_event("pull_request", "opened", is_draft=True)
    assert result.decision is EventDecision.IGNORE
    assert result.ignore_reason is IgnoreReason.DRAFT_PULL_REQUEST


def test_ready_for_review_is_queued_even_though_the_payload_says_draft() -> None:
    """The action *is* the transition out of draft, so it must not be filtered."""
    result = route_event("pull_request", "ready_for_review", is_draft=True)
    assert result.decision is EventDecision.QUEUE


def test_ping_is_acknowledged() -> None:
    result = route_event("ping", None)
    assert result.decision is EventDecision.IGNORE
    assert result.ignore_reason is IgnoreReason.PING


def test_missing_action_is_ignored() -> None:
    assert route_event("pull_request", None).decision is EventDecision.IGNORE


def test_installation_events_are_queued() -> None:
    assert route_event("installation", "created").job_type == "sync_installation"
    assert route_event("installation_repositories", "added").job_type == "sync_repositories"


def test_supported_events_set_is_minimal() -> None:
    """Least privilege applies to event subscriptions, not only permissions."""
    assert {
        "pull_request",
        "installation",
        "installation_repositories",
        "ping",
    } == SUPPORTED_EVENTS


# -- payload parsing ------------------------------------------------------


def test_pull_request_payload_parses() -> None:
    event = PullRequestEvent.model_validate(_sample("pull_request_opened.json"))
    assert event.action == "opened"
    assert event.pull_request.number == 42
    assert event.repository.full_name == "acme-demo/widget-service"
    assert event.repository.owner_login == "acme-demo"
    assert event.installation is not None
    assert event.installation.id == 77000001
    assert event.pull_request.head.sha.startswith("a1b2c3d4")


def test_unknown_fields_are_ignored_not_rejected() -> None:
    """GitHub adds fields regularly; a payload that gains one must still parse."""
    payload = _sample("pull_request_opened.json")
    payload["brand_new_field_2027"] = {"nested": True}
    payload["pull_request"]["another_new_one"] = 1
    assert PullRequestEvent.model_validate(payload).pull_request.number == 42


def test_merged_state_is_collapsed_correctly() -> None:
    """GitHub reports a merged PR as closed+merged; we store one value."""
    payload = _sample("pull_request_opened.json")
    payload["pull_request"]["state"] = "closed"
    payload["pull_request"]["merged"] = True
    payload["pull_request"]["merged_at"] = "2026-09-15T11:00:00Z"
    assert PullRequestEvent.model_validate(payload).pull_request.effective_state == "merged"

    payload["pull_request"]["merged"] = False
    payload["pull_request"]["merged_at"] = None
    assert PullRequestEvent.model_validate(payload).pull_request.effective_state == "closed"


def test_owner_login_falls_back_to_full_name() -> None:
    """Sparser payload shapes omit the nested owner; full_name is always owner/name."""
    payload = _sample("pull_request_opened.json")
    del payload["repository"]["owner"]
    assert PullRequestEvent.model_validate(payload).repository.owner_login == "acme-demo"


def test_envelope_never_raises_on_an_unknown_shape() -> None:
    """The audit row must be writable even for a payload we cannot interpret."""
    assert EnvelopeRef.model_validate({}).installation is None
    assert EnvelopeRef.model_validate(_sample("unsupported_event.json")).repository is not None


def test_envelope_extracts_identifiers_for_the_audit_row() -> None:
    env = EnvelopeRef.model_validate(_sample("pull_request_opened.json"))
    assert env.installation is not None and env.installation.id == 77000001
    assert env.repository is not None and env.repository.id == 550001
    assert env.pull_request_number == 42


def test_synchronize_sample_has_a_different_head_sha() -> None:
    """The fixture must actually exercise supersession, not just repeat itself."""
    opened = EnvelopeRef.model_validate(_sample("pull_request_opened.json"))
    sync = EnvelopeRef.model_validate(_sample("pull_request_synchronize.json"))
    assert opened.pull_request and sync.pull_request
    assert opened.pull_request.head.sha != sync.pull_request.head.sha
