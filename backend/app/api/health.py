"""Health and probe endpoints.

Three endpoints, because they answer three different questions and conflating
them causes real outages:

``GET /health``   — the meaningful, human-readable report.  Checks the
                    database and reports AI provider configuration.  This is
                    what a developer or the dashboard's integration page
                    calls.

``GET /healthz``  — liveness.  Returns 200 if the process is running and does
                    **no** dependency checks at all.  A liveness probe that
                    touches the database will restart healthy application
                    containers during a database blip, turning a partial
                    outage into a total one (architecture.md §17.6).

``GET /readyz``   — readiness.  Checks the database and the applied migration
                    revision.  A failure removes the instance from the load
                    balancer without killing it.

None of them expose a connection string, credential, host name or stack
trace.  Failure is reported as a status and a short, non-sensitive detail.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response, status

from app.ai.registry import available_providers
from app.api.deps import AIProviderDep, SettingsDep
from app.core.config import Settings
from app.db.session import check_database_connection, get_migration_revision
from app.schemas.health import (
    AIProviderHealth,
    AuthHealth,
    DependencyHealth,
    GitHubIntegrationHealth,
    HealthResponse,
    HealthStatus,
    IntegrationHealth,
    IntegrationState,
    LivenessResponse,
    ReadinessResponse,
)

WEBHOOK_PATH = "/api/v1/webhooks/github"

router = APIRouter(tags=["health"])

_STARTED_AT = time.monotonic()


def _uptime_seconds() -> float:
    return round(time.monotonic() - _STARTED_AT, 3)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health report",
)
async def health(
    settings: SettingsDep,
    ai_provider: AIProviderDep,
    response: Response,
) -> HealthResponse:
    db_ok = await check_database_connection()

    dependencies = [
        DependencyHealth(
            name="postgresql",
            status="ok" if db_ok else "error",
            detail=None if db_ok else "Database is not reachable.",
        )
    ]

    provider_status = ai_provider.status()
    # A provider that is not yet implemented does not make the *service*
    # unhealthy in Phase 0 — there is nothing for it to do yet.  The database
    # does: without it, nothing this application will ever do can work.
    # Annotated rather than inferred. A ternary over two string literals infers
    # `str`, which silently widens past the Literal the response model declares
    # — so the closed vocabulary was enforced only by Pydantic at runtime, and
    # a typo would have reached a 500 in production instead of a type error
    # here. Found by mypy in Phase 6.
    overall: HealthStatus = "ok" if db_ok else "degraded"
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall,
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env.value,
        uptime_seconds=_uptime_seconds(),
        dependencies=dependencies,
        ai_provider=AIProviderHealth(
            name=provider_status.name,
            configured=provider_status.configured,
            implemented=provider_status.implemented,
            model=provider_status.model,
            available=available_providers(),
        ),
        github=GitHubIntegrationHealth(
            app_configured=settings.github_app_configured,
            app_id_configured=settings.github_app_id is not None,
            private_key_configured=settings.github_private_key is not None,
            webhook_secret_configured=settings.github_webhook_secret is not None,
            webhook_path=WEBHOOK_PATH,
        ),
        auth=AuthHealth(
            oauth_configured=settings.oauth_configured,
            session_secret_configured=settings.session_secret is not None,
            unauthenticated_reads_enabled=(settings.dashboard_allow_unauthenticated_reads),
            cookie_secure=settings.cookie_secure,
            cookie_samesite=settings.session_cookie_samesite,
        ),
        integrations=_integration_states(settings, provider_status),
    )


def _integration_states(settings: Settings, provider_status: object) -> list[IntegrationHealth]:
    """Report how far each integration has actually been proven.

    **Every entry here stops at ``configured``**, and that is not an oversight.
    This endpoint does no live calls: a health check that authenticates against
    GitHub and Gemini on every poll would burn rate limit on a fixed schedule
    and would make an upstream outage look like *our* outage. ``state`` is a
    statement about configuration, ``last_verified_at`` is null, and both say so
    plainly rather than letting a UI infer "connected" from a present
    credential.

    Reaching ``authenticated`` or ``operational`` requires a real operation —
    a webhook delivery that verified, an installation token that minted, a
    review that published. **No such operation has ever been performed by this
    codebase**: no GitHub App credentials and no AI provider key were available
    in any environment it was built or verified in, through Phase 6 inclusive.
    So ``configured`` is not a placeholder for a value this endpoint will
    compute later — it is the strongest claim the evidence supports, and it
    stays that way until someone runs the system against a real App.
    """
    github_state: IntegrationState = (
        "configured" if settings.github_app_configured else "not_configured"
    )
    ai_configured = bool(getattr(provider_status, "configured", False))
    ai_implemented = bool(getattr(provider_status, "implemented", False))

    return [
        IntegrationHealth(
            name="github_app",
            state=github_state,
            detail=(
                "App id, private key and webhook secret are set. No live call "
                "has been made from this process, so this is not evidence the "
                "credentials work."
                if github_state == "configured"
                else "One or more of app id, private key, webhook secret is missing."
            ),
            last_verified_at=None,
        ),
        IntegrationHealth(
            name="github_oauth",
            state="configured" if settings.oauth_configured else "not_configured",
            detail=(
                "Sign-in is available."
                if settings.oauth_configured
                else "Sign-in is unavailable: OAuth client or session secret missing."
            ),
            last_verified_at=None,
        ),
        IntegrationHealth(
            name="ai_provider",
            state=("configured" if ai_configured and ai_implemented else "not_configured"),
            detail=(
                "An API key and model are set. Whether the model answers is "
                "not known until a review runs."
                if ai_configured
                else "No API key or no model configured."
            ),
            last_verified_at=None,
        ),
    ]


@router.get(
    "/healthz",
    response_model=LivenessResponse,
    summary="Liveness probe (no dependency checks)",
)
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe (database and migration state)",
)
async def readiness(response: Response) -> ReadinessResponse:
    db_ok = await check_database_connection()
    revision = await get_migration_revision() if db_ok else None
    ready = db_ok and revision is not None
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if ready else "error",
        database=db_ok,
        migration_revision=revision,
    )
