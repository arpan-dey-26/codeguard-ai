"""Response models for the health endpoints.

These are Pydantic models rather than free-form dicts so that the shape is
part of the OpenAPI schema the frontend generates its types from, and so that
adding a field is a visible contract change rather than an accident.

Nothing here carries a secret.  Credential state is reported as a boolean
*about* a secret — ``configured: true`` — never as the value.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HealthStatus = Literal["ok", "degraded", "error"]


class DependencyHealth(BaseModel):
    """Health of one external dependency."""

    name: str
    status: HealthStatus
    detail: str | None = Field(
        default=None,
        description="Short, non-sensitive explanation. Never contains a connection string.",
    )


class AIProviderHealth(BaseModel):
    """Configured AI provider state."""

    name: str = Field(description="Selected provider, e.g. 'gemini'.")
    configured: bool = Field(description="True when credentials and a model are set.")
    implemented: bool = Field(
        description="False in Phase 0 — the provider seam exists, the integration does not."
    )
    model: str | None = Field(default=None, description="Configured model identifier, if any.")
    available: tuple[str, ...] = Field(
        default=(), description="All providers the registry can construct."
    )


class GitHubIntegrationHealth(BaseModel):
    """GitHub App configuration state.

    Booleans *about* credentials, never the credentials. This is what makes
    "the webhook fired but nothing happened" diagnosable without shell access:
    an unconfigured webhook secret is the most common cause and it is visible
    here.
    """

    app_configured: bool = Field(
        description="True only when app id, private key AND webhook secret are all set."
    )
    app_id_configured: bool
    private_key_configured: bool
    webhook_secret_configured: bool
    webhook_path: str = Field(description="Where the GitHub App should send deliveries.")


IntegrationState = Literal[
    "not_configured", "configured", "reachable", "authenticated", "operational"
]


class IntegrationHealth(BaseModel):
    """How far an integration has actually been proven to work.

    **The distinction this model exists to make**, in increasing strength:

    ``not_configured``  no credentials are present
    ``configured``      credentials are present. Nothing has been tried.
    ``reachable``       the endpoint answered. It may have answered *401*.
    ``authenticated``   our credentials were accepted by the remote side.
    ``operational``     a real operation of the kind we actually perform
                        succeeded — the only level that means "this works".

    Phase 5's rule is that a configured credential is **not** a working
    integration, and this type is how the rule is enforced rather than merely
    stated. ``last_verified_at`` is null until something has actually been
    tried, and a UI reading this must not render "Connected" for anything below
    ``authenticated``.
    """

    name: str
    state: IntegrationState
    detail: str | None = Field(default=None, description="Short, non-sensitive explanation.")
    last_verified_at: str | None = Field(
        default=None,
        description=(
            "When a live call last proved this state. Null means nothing has "
            "been verified — the state is a statement about configuration only."
        ),
    )


class AuthHealth(BaseModel):
    """Whether a person can sign in, and whether anything is exposed without it."""

    oauth_configured: bool = Field(
        description="Client id, client secret and session secret are all present."
    )
    session_secret_configured: bool = Field(
        description=(
            "False means sessions are signed with an ephemeral per-process key "
            "and will not survive a restart. Refused outright in production."
        )
    )
    unauthenticated_reads_enabled: bool = Field(
        description=(
            "True means the dashboard API serves review data with no authentication at all."
        )
    )
    cookie_secure: bool
    cookie_samesite: str


class HealthResponse(BaseModel):
    """Body of ``GET /health``."""

    status: HealthStatus
    app: str
    version: str
    environment: str
    uptime_seconds: float
    dependencies: list[DependencyHealth]
    ai_provider: AIProviderHealth
    github: GitHubIntegrationHealth
    auth: AuthHealth
    integrations: list[IntegrationHealth] = Field(
        default_factory=list,
        description=(
            "Per-integration verification state. Read `state`, not the presence of a credential."
        ),
    )


class LivenessResponse(BaseModel):
    """Body of ``GET /healthz`` — process is running. No dependency checks."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Body of ``GET /readyz`` — safe to receive traffic."""

    status: HealthStatus
    database: bool
    migration_revision: str | None = Field(
        default=None,
        description="Applied Alembic revision, or null when the database has never been migrated.",
    )
