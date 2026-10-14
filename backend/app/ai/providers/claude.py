"""Anthropic Claude provider — seam only, future/optional.

Claude was the provider named in the original architecture baseline
(architecture.md §11).  It is retained here as a second implementation of the
same contract for one specific reason: **an abstraction validated by a single
implementation is not validated.**  Keeping a second provider in the registry
from day one is what proves the seam is real rather than decorative, and it
makes the "swap the AI vendor" claim demonstrable in a viva.

No Anthropic API call is made here, and no SDK is installed.
"""

from __future__ import annotations

from app.ai.contracts import ProviderCapabilities, ReviewRequest, ReviewResult
from app.ai.provider import BaseAIReviewProvider
from app.core.config import AIProviderName
from app.core.errors import ProviderNotImplementedError


class ClaudeProvider(BaseAIReviewProvider):
    """Optional future AI provider. Integration deferred."""

    @property
    def name(self) -> AIProviderName:
        return AIProviderName.CLAUDE

    @property
    def capabilities(self) -> ProviderCapabilities:
        # Structure is enforced through a forced tool call rather than a
        # response schema — a different mechanism, the same guarantee, and
        # precisely the kind of difference this abstraction exists to absorb.
        return ProviderCapabilities(
            supports_structured_output=True,
            supports_system_instructions=True,
            max_input_tokens=None,
        )

    @property
    def model(self) -> str | None:
        return self._settings.claude_model

    def _has_credentials(self) -> bool:
        return self._settings.claude_api_key is not None

    async def review_code(self, request: ReviewRequest) -> ReviewResult:
        self.ensure_configured()
        raise ProviderNotImplementedError(
            "The Claude integration is optional and not scheduled for the "
            "initial implementation. Phase 0 ships the provider abstraction only."
        )
