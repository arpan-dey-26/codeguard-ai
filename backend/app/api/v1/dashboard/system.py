"""Integration status and settings (architecture.md A20, A23).

**Read-only, and secret-free by construction.** The settings response is
assembled from literals and from *booleans about* secrets — never from a secret
— and there is no write endpoint at all. Editing credentials from a browser
needs secure secret management, which this build does not have; offering the
form anyway would be the wrong feature.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.ai.prompt import PROMPT_VERSION
from app.api.deps import AIProviderDep, SessionDep, SettingsDep
from app.api.v1.dashboard.common import ScopeDep
from app.db import read
from app.db.repositories import dashboard as queries
from app.schemas.dashboard import (
    InstallationSummary,
    IntegrationStatusResponse,
    SettingsResponse,
    as_int,
)

router = APIRouter(tags=["system"])

WEBHOOK_PATH = "/api/v1/webhooks/github"


@router.get(
    "/integration/status",
    response_model=IntegrationStatusResponse,
    summary="GitHub App installations",
)
async def integration_status(
    session: SessionDep, scope: ScopeDep, settings: SettingsDep
) -> IntegrationStatusResponse:
    rows = await read.fetch_all(session, queries.integration_status(scope=scope))
    return IntegrationStatusResponse(
        github_app_configured=settings.github_app_configured,
        webhook_path=WEBHOOK_PATH,
        installations=[
            InstallationSummary(
                id=as_int(row["id"]),
                github_installation_id=as_int(row["github_installation_id"]),
                account_login=row["account_login"],
                account_type=row["account_type"],
                repository_selection=row["repository_selection"],
                suspended=row["suspended_at"] is not None,
                uninstalled=row["uninstalled_at"] is not None,
                repository_count=as_int(row["repository_count"]),
                active_repository_count=as_int(row["active_repository_count"]),
                last_event_at=row["last_event_at"],
                failed_deliveries=as_int(row["failed_deliveries"]),
            )
            for row in rows
        ],
    )


@router.get("/settings", response_model=SettingsResponse, summary="Non-secret configuration")
async def get_settings_view(
    settings: SettingsDep, provider: AIProviderDep, scope: ScopeDep
) -> SettingsResponse:
    """What this deployment is configured to do.

    Every field is a literal or a boolean about a secret. ``ai_model`` is a
    model identifier, which is a non-sensitive configuration value; the key
    that authorises its use appears nowhere in this response, this schema, or
    this module.
    """
    status = provider.status()
    return SettingsResponse(
        environment=settings.app_env.value,
        app_version=settings.app_version,
        github_app_configured=settings.github_app_configured,
        github_webhook_secret_configured=settings.github_webhook_secret is not None,
        ai_provider=settings.ai_provider.value,
        ai_model=status.model,
        ai_provider_configured=status.configured,
        ai_provider_implemented=status.implemented,
        prompt_version=PROMPT_VERSION,
        review_publishing_enabled=settings.review_publish_enabled,
        dashboard_authenticated=not settings.dashboard_allow_unauthenticated_reads,
        dashboard_unauthenticated_reads_enabled=(settings.dashboard_allow_unauthenticated_reads),
        max_inline_comments=settings.review_max_inline_comments,
        min_confidence=settings.review_min_confidence,
        min_confidence_high_severity=settings.review_min_confidence_high_severity,
        max_files_per_review=settings.ai_max_files_per_review,
        max_findings_per_review=settings.ai_max_findings,
    )
