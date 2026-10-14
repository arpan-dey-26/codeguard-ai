"""A hard ceiling on provider calls for one review.

Lives beside the AI contracts rather than in ``app.review.service`` for two
reasons. It is a property of talking to a provider, not of orchestrating a
review; and this module imports nothing but the contracts, so the ceiling can
be exercised by a test in an environment that has no database driver — which is
exactly the environment the release was verified in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.contracts import ReviewRequest, ReviewResult
from app.core.errors import ProviderBudgetExceededError


@dataclass(slots=True)
class ProviderCallBudget:
    """Wraps a provider and refuses more than ``limit`` calls.

    **Added in Phase 6.** ``AI_MAX_REQUESTS_PER_REVIEW`` is described to
    operators in ``docs/deployment.md`` as a free-tier guard, and nothing read
    it: the ceiling held only because the orchestrator happened to make exactly
    one call. A limit satisfied by accident is not a limit, and the failure it
    exists to prevent — a large pull request quietly becoming dozens of billed
    requests — would have arrived with the first change that added a second call
    site. A documented safety limit that no code enforces is worse than no
    limit, because an operator who sets it believes they are protected.

    Enforced by wrapping rather than by counting at the call site, so a call
    site added later is covered without anyone remembering to cover it. One
    instance per review: the counter is per pull request, which is what the
    setting's name promises.
    """

    provider: Any
    limit: int
    used: int = 0

    async def review(self, request: ReviewRequest) -> ReviewResult:
        if self.used >= self.limit:
            raise ProviderBudgetExceededError(
                f"this review already made {self.used} provider call(s); "
                f"AI_MAX_REQUESTS_PER_REVIEW is {self.limit}"
            )
        # Incremented *before* the call, so a call that raises still counts.
        # Counting only successes would let a provider that fails repeatedly
        # produce unbounded requests, which is the case the ceiling is for.
        self.used += 1
        result: ReviewResult = await self.provider.review(request)
        return result

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


__all__ = ["ProviderCallBudget"]
