"""Provider selection.

The registry is the single place in the application that knows which concrete
providers exist.  Everything else asks for "the configured provider" and
receives something satisfying ``AIReviewProvider``.

Adding a provider is therefore a two-line change — one enum member in
``app.core.config.AIProviderName``, one registry entry here — with no edit to
orchestration, API, worker or tests (architecture.md NFR-M3).
"""

from __future__ import annotations

from app.ai.provider import AIReviewProvider, BaseAIReviewProvider
from app.ai.providers.claude import ClaudeProvider
from app.ai.providers.gemini import GeminiProvider
from app.core.config import AIProviderName, Settings, get_settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER_REGISTRY: dict[AIProviderName, type[BaseAIReviewProvider]] = {
    AIProviderName.GEMINI: GeminiProvider,
    AIProviderName.CLAUDE: ClaudeProvider,
}


def build_provider(settings: Settings | None = None) -> AIReviewProvider:
    """Construct the provider named by ``AI_PROVIDER``.

    Construction is deliberately cheap and side-effect free: no credential
    check, no network, no client object.  That is what allows the application
    to boot, report status and run its tests with no API key present, while
    ``review_code`` still refuses to run unconfigured.
    """
    settings = settings or get_settings()
    provider_cls = PROVIDER_REGISTRY.get(settings.ai_provider)
    if provider_cls is None:  # pragma: no cover — enum membership prevents this
        raise ConfigurationError(f"Unknown AI provider: {settings.ai_provider!r}")
    provider = provider_cls(settings)
    logger.debug(
        "ai.provider.selected",
        provider=provider.name.value,
        configured=provider.is_configured,
        implemented=provider.is_implemented,
    )
    return provider


def available_providers() -> tuple[str, ...]:
    """Names of every provider the registry can construct."""
    return tuple(name.value for name in PROVIDER_REGISTRY)
