"""The ``AIReviewProvider`` seam.

                        AIReviewProvider          (Protocol — the contract)
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
          GeminiProvider               ClaudeProvider
      (initial provider,            (future optional provider,
       integration in the            integration deferred)
       AI integration phase)

Why a Protocol *and* an abstract base class, rather than one or the other:

*   ``AIReviewProvider`` (Protocol) is what the rest of the application
    depends on.  Structural typing means a test double satisfies it by having
    the right shape — no inheritance, no import of production code, no mock
    framework.  This is what keeps Phase 0's tests free of any network call.
*   ``BaseAIReviewProvider`` (ABC) is a convenience for real providers.  It
    supplies the credential-checking and status logic that every provider
    needs identically, so a new provider writes only the parts that genuinely
    differ.

The contract is deliberately narrow: one method.  A provider sends a rendered
context and returns structured claims.  It does not fetch code, does not post
comments, does not touch the database, and is given no tools — so however the
model behaves, a provider cannot take an action (architecture.md §11.5).
"""

from __future__ import annotations

import abc
from typing import Protocol, runtime_checkable

from app.ai.contracts import (
    ProviderCapabilities,
    ProviderStatus,
    ReviewRequest,
    ReviewResult,
)
from app.core.config import AIProviderName, Settings
from app.core.errors import ConfigurationError


@runtime_checkable
class AIReviewProvider(Protocol):
    """The interface the review orchestration layer depends on."""

    @property
    def name(self) -> AIProviderName:
        """Identifier of this provider, matching ``AI_PROVIDER``."""
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this provider supports, for orchestration to adapt to."""
        ...

    def status(self) -> ProviderStatus:
        """Non-secret readiness report, safe for health endpoints."""
        ...

    async def review_code(self, request: ReviewRequest) -> ReviewResult:
        """Produce structured review findings for one pull request context.

        Implementations are responsible for their own structured-output
        mechanism, their own retry and timeout policy, and for fencing the
        untrusted ``context_document`` away from their instructions.

        Raises:
            ConfigurationError: the provider has no usable credentials.
            ProviderNotImplementedError: the integration is not written yet.
            TransientError / ThrottledError: upstream failure classification.
        """
        ...


class BaseAIReviewProvider(abc.ABC):
    """Shared behaviour for concrete providers.

    Subclasses declare their identity, their capabilities and how to locate
    their credentials; everything else is inherited.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -- identity ---------------------------------------------------------
    @property
    @abc.abstractmethod
    def name(self) -> AIProviderName: ...

    @property
    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @property
    @abc.abstractmethod
    def model(self) -> str | None:
        """Configured model identifier, or ``None`` if unset.

        No provider hard-codes a default model.  Model identifiers change and
        are deprecated on the vendor's schedule; baking one into the codebase
        guarantees it is stale by the time someone reads it.  The value comes
        from configuration and is chosen, against current vendor
        documentation, in the AI integration phase.
        """

    @abc.abstractmethod
    def _has_credentials(self) -> bool:
        """True when this provider's API key is present in configuration."""

    # -- shared -----------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        """Credentials **and** a model identifier are both present."""
        return self._has_credentials() and bool(self.model)

    def ensure_configured(self) -> None:
        """Raise if this provider cannot possibly perform real work.

        Called at the start of ``review_code`` rather than in ``__init__``.
        Constructing a provider must stay side-effect-free and credential-free
        so that the application can start, report its status and run its test
        suite without any API key — while a genuine attempt to use an
        unconfigured provider still fails immediately and loudly.
        """
        if not self._has_credentials():
            raise ConfigurationError(
                f"AI provider '{self.name.value}' is selected but its API key is not set."
            )
        if not self.model:
            raise ConfigurationError(
                f"AI provider '{self.name.value}' is selected but no model is configured."
            )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name.value,
            configured=self.is_configured,
            implemented=self.is_implemented,
            model=self.model,
        )

    @property
    def is_implemented(self) -> bool:
        """Whether a real integration exists behind this seam.

        ``False`` for every provider in Phase 0.  Reporting it honestly is the
        alternative to a stub that pretends an external API responded.
        """
        return False

    @abc.abstractmethod
    async def review_code(self, request: ReviewRequest) -> ReviewResult: ...
