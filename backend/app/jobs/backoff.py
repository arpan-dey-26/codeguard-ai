"""Retry scheduling.

Pure functions with an injectable random source, so every branch is testable
without waiting and without monkey-patching the clock.

    delay(n) = min(base * 2^(n-1), cap) * jitter,   jitter ∈ [0.5, 1.5]

**Why full jitter.** Without it, N jobs that failed together retry together,
reproducing the load spike that caused the failure in the first place. This is
the thundering-herd problem, and randomising the delay is the standard cure.

**Why the retry budget is also a cost control.** From Phase 3 a retry may
re-run a paid AI call. An unbounded retry loop against a persistent upstream
failure is an unbounded bill, so the attempt count is a hard ceiling, not a
suggestion.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.core.errors import CodeGuardError, ThrottledError

JITTER_MIN = 0.5
JITTER_MAX = 1.5


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_seconds: float
    cap_seconds: float

    def delay_for(
        self,
        attempt: int,
        *,
        error: BaseException | None = None,
        rng: random.Random | None = None,
    ) -> float:
        """Seconds to wait before attempt number ``attempt + 1``.

        A server-directed delay always wins. Substituting our own backoff for
        a ``Retry-After`` we were given is how a client earns a longer ban.
        """
        if isinstance(error, ThrottledError) and error.retry_after is not None:
            return float(min(max(error.retry_after, 0), self.cap_seconds))

        exponential = self.base_seconds * (2 ** max(0, attempt - 1))
        bounded = min(exponential, self.cap_seconds)
        source = rng or random
        return bounded * source.uniform(JITTER_MIN, JITTER_MAX)

    def should_retry(self, attempt: int, error: BaseException) -> bool:
        """Whether a failure at ``attempt`` warrants another try.

        Two independent conditions, and both must hold:

        1.  The error *class* says the outcome could differ next time.
            ``TransientError`` and ``ThrottledError`` qualify; everything else
            — invalid payload, lost access, misconfiguration, a bug — does not.
        2.  The attempt budget is not exhausted.

        An unknown exception type is treated as permanent. Retrying a bug
        three times just produces the same traceback three times while
        delaying the signal that something is broken.
        """
        if attempt >= self.max_attempts:
            return False
        if isinstance(error, CodeGuardError):
            return error.retryable
        return False


def classify_error(error: BaseException) -> str:
    """Short, stable label for the error, for storage and metrics.

    The class name rather than the message: messages contain paths, ids and
    third-party text, and grouping metrics by a free-text message produces one
    bucket per occurrence.
    """
    return type(error).__name__
