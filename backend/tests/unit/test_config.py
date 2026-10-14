"""Configuration loading and safety."""

from __future__ import annotations

import pytest
from app.core.config import AIProviderName, AppEnv, Settings
from pydantic import ValidationError


def _base(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_defaults_select_gemini_as_initial_provider() -> None:
    assert _base().ai_provider is AIProviderName.GEMINI


@pytest.mark.parametrize("blank", ["", " \t\n"])
def test_compose_blank_credentials_are_unconfigured(blank: str) -> None:
    fields = [
        "github_app_id",
        "github_private_key",
        "github_webhook_secret",
        "github_oauth_client_id",
        "github_oauth_client_secret",
        "session_secret",
        "gemini_api_key",
        "gemini_model",
        "claude_api_key",
        "claude_model",
    ]
    settings = _base(**dict.fromkeys(fields, blank))
    assert not settings.oauth_configured
    assert not settings.github_app_configured
    assert settings.session_secret is None
    assert settings.gemini_api_key is None
    assert settings.gemini_model is None
    assert not settings.safe_summary()["session_secret_configured"]
    # Missing development secrets must use a random key, never the empty key.
    assert settings.session_signing_key()


def test_nonblank_secret_formatting_is_preserved() -> None:
    value = "  fixture-key\n"
    settings = _base(github_private_key=value)
    assert settings.github_private_key is not None
    assert settings.github_private_key.get_secret_value() == value


def test_settings_load_without_any_api_key() -> None:
    """The whole test suite and CI depend on this being true."""
    settings = _base()
    assert settings.gemini_api_key is None
    assert settings.claude_api_key is None


def test_gemini_model_has_no_hardcoded_default() -> None:
    """Model identifiers are chosen against current vendor docs, not guessed."""
    assert _base().gemini_model is None


def test_sync_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        _base(database_url="postgresql://u:p@localhost:5432/db")


def test_debug_is_refused_outside_development() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        _base(app_env=AppEnv.PRODUCTION, debug=True)


def test_cors_origins_is_a_single_normalised_origin() -> None:
    assert _base(frontend_url="http://localhost:5173/").cors_origins == ["http://localhost:5173"]


def test_secret_values_are_not_exposed_by_repr() -> None:
    settings = _base(
        gemini_api_key="super-secret-value",
        github_webhook_secret="another-secret",
    )
    rendered = f"{settings!r} {settings.gemini_api_key!r}"
    assert "super-secret-value" not in rendered
    assert "another-secret" not in rendered


# Assembled at runtime rather than written as a literal: a repository-wide
# secret scanner cannot distinguish a test fixture from a real leak, and
# weakening the scanner to accommodate a fixture is the wrong trade.
FAKE_PEM_MARKER = "-----BEGIN " + "PRIVATE KEY-----"


def test_safe_summary_contains_no_secrets() -> None:
    settings = _base(
        gemini_api_key="leak-me",
        github_app_id="12345",
        github_private_key=FAKE_PEM_MARKER,
        github_webhook_secret="whsec-fixture",
    )
    summary = settings.safe_summary()
    flattened = str(summary)
    assert "leak-me" not in flattened
    assert FAKE_PEM_MARKER not in flattened
    assert "whsec-fixture" not in flattened
    # Booleans *about* secrets are fine and useful.
    assert summary["github_app_configured"] is True
    assert summary["ai_provider"] == "gemini"


def test_github_app_is_not_configured_until_all_three_credentials_are_present() -> None:
    """Phase 1 tightened this: an App that can mint tokens but cannot verify
    webhook signatures is not usable, so "configured" means all three.
    """
    assert _base(github_app_id="12345").github_app_configured is False
    assert (
        _base(github_app_id="12345", github_private_key=FAKE_PEM_MARKER).github_app_configured
        is False
    )
    assert (
        _base(
            github_app_id="12345",
            github_private_key=FAKE_PEM_MARKER,
            github_webhook_secret="whsec-fixture",
        ).github_app_configured
        is True
    )
