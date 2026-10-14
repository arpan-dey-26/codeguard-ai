"""AI provider abstraction.

Every test here runs with no API key and makes no network call.  That is the
property being asserted as much as it is a convenience: if any of these tests
could reach Gemini or Anthropic, the abstraction would be leaking.
"""

from __future__ import annotations

import pytest
from app.ai.contracts import (
    FindingCategory,
    FindingSeverity,
    ProviderCapabilities,
    ProviderStatus,
    RawFinding,
    ReviewRequest,
    ReviewResult,
)
from app.ai.provider import AIReviewProvider
from app.ai.providers.claude import ClaudeProvider
from app.ai.providers.gemini import GeminiProvider
from app.ai.registry import PROVIDER_REGISTRY, available_providers, build_provider
from app.core.config import AIProviderName, AppEnv, Settings
from app.core.errors import ConfigurationError, ProviderNotImplementedError


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        # Provider scenarios must not inherit a developer or CI model/key.
        "gemini_model": None,
        "gemini_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


REQUEST = ReviewRequest(
    repository_full_name="acme/widgets",
    pull_request_number=1,
    head_sha="a" * 40,
    context_document="<review_task>…</review_task>",
)


# -- selection ------------------------------------------------------------


def test_default_provider_is_gemini() -> None:
    assert isinstance(build_provider(_settings()), GeminiProvider)


def test_provider_is_selected_by_configuration_alone() -> None:
    provider = build_provider(_settings(ai_provider=AIProviderName.CLAUDE))
    assert isinstance(provider, ClaudeProvider)
    assert provider.name is AIProviderName.CLAUDE


def test_selection_requires_no_credentials() -> None:
    """Building a provider must be free of side effects and credential checks."""
    provider = build_provider(_settings())
    assert provider.status().configured is False


def test_registry_exposes_both_providers() -> None:
    assert set(available_providers()) == {"gemini", "claude"}
    assert set(PROVIDER_REGISTRY) == set(AIProviderName)


@pytest.mark.parametrize("name", list(AIProviderName))
def test_every_registered_provider_satisfies_the_protocol(name: AIProviderName) -> None:
    provider = build_provider(_settings(ai_provider=name))
    assert isinstance(provider, AIReviewProvider)
    assert isinstance(provider.capabilities, ProviderCapabilities)
    assert isinstance(provider.status(), ProviderStatus)


# -- configuration reporting ---------------------------------------------


def test_provider_reports_unconfigured_without_key_or_model() -> None:
    """Updated in Phase 3: Gemini is now *implemented* but still unconfigured
    without a key. The two flags are deliberately independent — "we wrote the
    integration" and "this deployment can use it" are different facts, and
    collapsing them is how a missing key gets reported as a missing feature."""
    status = build_provider(_settings()).status()
    assert status.name == "gemini"
    assert status.configured is False
    assert status.implemented is True
    assert status.model is None


def test_provider_reports_configured_when_key_and_model_present() -> None:
    settings = _settings(gemini_api_key="not-a-real-key", gemini_model="some-model")
    status = build_provider(settings).status()
    assert status.configured is True
    assert status.model == "some-model"


def test_status_never_contains_the_api_key() -> None:
    settings = _settings(gemini_api_key="leak-me-if-you-can", gemini_model="m")
    assert "leak-me-if-you-can" not in str(build_provider(settings).status())


def test_api_key_alone_is_not_enough() -> None:
    """A key with no model cannot make a call, so it is not 'configured'."""
    settings = _settings(gemini_api_key="k")
    assert build_provider(settings).status().configured is False


# -- behaviour: no real calls, honest failures ---------------------------


@pytest.mark.parametrize("name", list(AIProviderName))
async def test_unconfigured_provider_raises_configuration_error(
    name: AIProviderName,
) -> None:
    provider = build_provider(_settings(ai_provider=name))
    with pytest.raises(ConfigurationError):
        await provider.review_code(REQUEST)


async def test_gemini_is_implemented_from_phase_3() -> None:
    """Replaces the Phase 0 assertion that it raised ProviderNotImplementedError.

    The provider's own behaviour against a mocked transport is covered in
    tests/review/test_gemini_provider.py; here we only assert the seam now
    reports a real implementation. No network call is made.
    """
    settings = _settings(gemini_api_key="k", gemini_model="m")
    provider = build_provider(settings)
    assert provider.is_implemented is True
    assert provider.status().implemented is True


async def test_configured_claude_provider_is_explicitly_not_implemented() -> None:
    settings = _settings(
        ai_provider=AIProviderName.CLAUDE,
        claude_api_key="k",
        claude_model="m",
    )
    with pytest.raises(ProviderNotImplementedError):
        await build_provider(settings).review_code(REQUEST)


def test_only_gemini_claims_to_be_implemented() -> None:
    """Claude remains a seam, not an implementation. ADR-0001 keeps the
    abstraction honest by refusing to pretend a provider works before it does."""
    implemented = {
        name: build_provider(_settings(ai_provider=name)).is_implemented for name in AIProviderName
    }
    assert implemented == {AIProviderName.GEMINI: True, AIProviderName.CLAUDE: False}


# -- the seam actually works ---------------------------------------------


class _StubProvider:
    """A test double that satisfies the protocol structurally.

    It inherits from nothing and imports no production class.  If this passes
    ``isinstance(..., AIReviewProvider)``, the orchestration layer built in
    Phase 3 can be tested without any vendor code at all — which is the
    concrete payoff of the abstraction.
    """

    @property
    def name(self) -> AIProviderName:
        return AIProviderName.GEMINI

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_structured_output=True, supports_system_instructions=True
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus(name="stub", configured=True, implemented=True, model="stub-1")

    async def review_code(self, request: ReviewRequest) -> ReviewResult:
        return ReviewResult(
            model="stub-1",
            summary=f"Reviewed PR #{request.pull_request_number}",
            findings=(
                RawFinding(
                    finding_id="F001",
                    category=FindingCategory.BUG,
                    severity=FindingSeverity.HIGH,
                    title="Off-by-one in loop bound",
                    description="…",
                    file_path="src/app.py",
                    line_start=42,
                    anchor_snippet="for i in range(n + 1):",
                    confidence=0.8,
                    impact="…",
                    rationale="…",
                ),
            ),
        )


def test_a_stub_satisfies_the_protocol_without_inheritance() -> None:
    assert isinstance(_StubProvider(), AIReviewProvider)


async def test_orchestration_can_depend_on_the_protocol_only() -> None:
    async def orchestrate(provider: AIReviewProvider) -> int:
        result = await provider.review_code(REQUEST)
        return result.finding_count

    assert await orchestrate(_StubProvider()) == 1
