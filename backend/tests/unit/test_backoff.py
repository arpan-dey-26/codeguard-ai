"""Retry policy.

Pure logic with an injectable RNG, so every branch is reachable without
waiting and without patching the clock.
"""

from __future__ import annotations

import random

import pytest
from app.core.errors import (
    ConfigurationError,
    PermanentAccessError,
    PermanentInputError,
    ThrottledError,
    TransientError,
    WebhookSignatureError,
)
from app.jobs.backoff import JITTER_MAX, JITTER_MIN, RetryPolicy, classify_error

POLICY = RetryPolicy(max_attempts=3, base_seconds=30.0, cap_seconds=900.0)


# -- which errors are retried --------------------------------------------


@pytest.mark.parametrize(
    "error",
    [TransientError("github down"), ThrottledError("slow down", retry_after=10)],
)
def test_transient_failures_are_retried(error: Exception) -> None:
    assert POLICY.should_retry(attempt=1, error=error) is True


@pytest.mark.parametrize(
    "error",
    [
        PermanentInputError("malformed payload"),
        PermanentAccessError("repository gone"),
        ConfigurationError("no private key"),
        WebhookSignatureError(),
    ],
)
def test_permanent_failures_are_never_retried(error: Exception) -> None:
    """Retrying these burns the budget on an outcome that cannot change.

    An invalid signature, a malformed payload, a missing credential and lost
    access are all permanent by definition.
    """
    assert POLICY.should_retry(attempt=1, error=error) is False


def test_unknown_exceptions_are_treated_as_permanent() -> None:
    """An unrecognised exception is a bug, and bugs do not fix themselves.

    Retrying one produces the same traceback three times while delaying the
    signal that something is broken.
    """
    assert POLICY.should_retry(attempt=1, error=ValueError("boom")) is False
    assert POLICY.should_retry(attempt=1, error=TypeError("boom")) is False


def test_the_attempt_budget_is_a_hard_ceiling() -> None:
    """Also a cost control: from Phase 3 a retry may re-run a paid AI call."""
    error = TransientError("still down")
    assert POLICY.should_retry(attempt=2, error=error) is True
    assert POLICY.should_retry(attempt=3, error=error) is False
    assert POLICY.should_retry(attempt=99, error=error) is False


# -- how long we wait -----------------------------------------------------


def test_backoff_grows_exponentially() -> None:
    rng = random.Random(7)

    # Compare midpoints across many samples so jitter does not mask the trend.
    def mean(attempt: int) -> float:
        return sum(POLICY.delay_for(attempt, rng=rng) for _ in range(200)) / 200

    assert mean(1) < mean(2) < mean(3)


def test_backoff_is_capped() -> None:
    rng = random.Random(7)
    for _ in range(100):
        assert POLICY.delay_for(20, rng=rng) <= POLICY.cap_seconds * JITTER_MAX


def test_jitter_stays_within_bounds() -> None:
    rng = random.Random(3)
    for attempt in (1, 2, 3):
        base = min(POLICY.base_seconds * 2 ** (attempt - 1), POLICY.cap_seconds)
        for _ in range(200):
            delay = POLICY.delay_for(attempt, rng=rng)
            assert base * JITTER_MIN <= delay <= base * JITTER_MAX


def test_jitter_actually_varies() -> None:
    """The point of jitter: without it, everything retries in lockstep."""
    rng = random.Random(11)
    delays = {round(POLICY.delay_for(2, rng=rng), 9) for _ in range(50)}
    assert len(delays) > 40


def test_a_server_directed_delay_overrides_our_backoff() -> None:
    delay = POLICY.delay_for(1, error=ThrottledError("slow", retry_after=120))
    assert delay == 120.0


def test_a_server_directed_delay_is_still_capped() -> None:
    """A hostile or buggy Retry-After must not park a job for a week."""
    delay = POLICY.delay_for(1, error=ThrottledError("slow", retry_after=10**9))
    assert delay == POLICY.cap_seconds


def test_a_negative_retry_after_is_clamped() -> None:
    assert POLICY.delay_for(1, error=ThrottledError("slow", retry_after=-5)) == 0.0


def test_throttled_without_a_delay_falls_back_to_backoff() -> None:
    rng = random.Random(5)
    delay = POLICY.delay_for(1, error=ThrottledError("slow"), rng=rng)
    assert POLICY.base_seconds * JITTER_MIN <= delay <= POLICY.base_seconds * JITTER_MAX


# -- error labelling ------------------------------------------------------


def test_errors_are_labelled_by_class_not_message() -> None:
    """Grouping metrics by a free-text message gives one bucket per occurrence."""
    assert classify_error(TransientError("github 503 on /repos/a/b")) == "TransientError"
    assert classify_error(ValueError("x")) == "ValueError"
