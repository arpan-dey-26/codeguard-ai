"""Application configuration.

All configuration enters the process through environment variables and is
validated once, at import time, by pydantic-settings.

Two deliberate rules (architecture.md §21.4, and the Phase 0 refinement in
docs/decisions/ADR-0003):

1.  *Infrastructure* secrets fail fast.  A missing ``DATABASE_URL`` in a
    non-development environment stops the process immediately, rather than
    surfacing as a confusing error on the first request.

2.  *AI provider* credentials are NOT required to construct ``Settings``.
    They are validated when a provider is actually constructed
    (``app.ai.registry.build_provider``).  This is what lets the test suite
    and CI run with no API key at all, while still failing loudly the moment
    a misconfigured provider is used for real work.

Nothing in this module is ever logged or serialised into an API response.
Secret-bearing fields use ``pydantic.SecretStr`` so that an accidental
``repr()`` or f-string prints ``**********`` instead of the value.
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    """Deployment environment.

    ``development`` relaxes a small number of guardrails (permissive defaults,
    reload).  Everything else is treated as production-grade.
    """

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class AIProviderName(StrEnum):
    """Identifiers accepted by ``AI_PROVIDER``.

    Adding a provider means adding a member here and registering an
    implementation in ``app.ai.registry`` — nothing else in the application
    changes (architecture.md NFR-M3).
    """

    GEMINI = "gemini"
    CLAUDE = "claude"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# The development default, named so the production validator can recognise it.
# A production process must never run on this URL: the credentials in it are
# published in docker-compose.yml and in this repository.
_DEVELOPMENT_DATABASE_URL = "postgresql+asyncpg://codeguard:codeguard@localhost:5432/codeguard"

# 32 bytes of entropy is the floor for an HMAC key that guards a session.
_MIN_SESSION_SECRET_LENGTH = 32


@lru_cache(maxsize=1)
def _ephemeral_session_key() -> bytes:
    """A random signing key for this process only.

    Used in development and tests when no ``SESSION_SECRET`` is set. Cached so
    that every session in one process shares one key; not persisted, so
    restarting invalidates every session — which is the honest behaviour for a
    deployment that never configured a secret.
    """
    return secrets.token_bytes(64)


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application ------------------------------------------------------
    app_env: AppEnv = AppEnv.DEVELOPMENT
    debug: bool = False
    app_name: str = "CodeGuard AI"
    app_version: str = "0.6.2"
    log_level: LogLevel = "INFO"

    # -- HTTP -------------------------------------------------------------
    frontend_url: str = "http://localhost:5173"
    """Single allowed browser origin.  Used to build the CORS allow-list.

    A list is deliberately not accepted here: one origin per deployment keeps
    the CORS surface auditable.  ``allow_origins=["*"]`` with credentials is
    both invalid and a textbook misconfiguration (architecture.md §21.6).
    """

    public_base_url: str | None = None
    """The API's own externally-visible origin, e.g. ``https://api.example.com``.

    Required outside development because the OAuth redirect URI is built from
    it. Deriving that URI from the inbound request instead would let a forged
    ``Host`` header redirect the authorization code somewhere else, which is
    the classic way an OAuth flow is stolen.
    """

    trusted_hosts: str = "*"
    """Comma-separated ``Host`` values this deployment will answer to.

    ``*`` is refused outside development by the validator below. A wildcard
    host allow-list means a request with any ``Host`` header is served, which
    enables cache poisoning and password-reset-style link forgery on any
    absolute URL the application builds.
    """

    trusted_proxy_count: int = Field(default=0, ge=0, le=8)
    """How many reverse proxies sit in front of this process.

    Zero means ``X-Forwarded-*`` is ignored entirely — correct when nothing is
    in front, and the safe default, because a client can send those headers
    itself. Behind one load balancer this is 1, and only the *last* hop's value
    is trusted: the earlier entries in ``X-Forwarded-For`` are client-supplied
    and forging them is how rate limiting by IP gets bypassed.
    """

    # -- Database ---------------------------------------------------------
    database_url: str = _DEVELOPMENT_DATABASE_URL
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)
    db_echo: bool = False

    # -- GitHub App -------------------------------------------------------
    github_app_id: str | None = None
    github_private_key: SecretStr | None = None
    """The App's RSA private key, in PEM form.

    Three encodings are accepted because environment systems mangle multi-line
    values differently (see ``github_private_key_pem``).  The key is held only
    in process memory: it is never written to the database, a log, a response
    or a temp file.
    """

    github_webhook_secret: SecretStr | None = None
    github_api_base_url: str = "https://api.github.com"

    # Hard ceiling on a webhook body, enforced *before* any HMAC computation
    # so an oversized request is rejected without spending CPU on it.
    github_webhook_max_body_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)

    # -- Background queue / worker ----------------------------------------
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0, le=60)
    job_visibility_timeout_seconds: int = Field(default=900, ge=30)
    """How long a claimed job may stay ``running`` before the sweeper reclaims it.

    Deliberately generous (15 minutes).  Reclaiming a job that is still running
    means doing the work twice; waiting a few extra minutes for a genuinely
    crashed worker costs only latency.  The asymmetry favours waiting.
    """

    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_backoff_base_seconds: float = Field(default=30.0, gt=0)
    job_backoff_cap_seconds: float = Field(default=900.0, gt=0)
    job_sweep_interval_seconds: int = Field(default=60, ge=10)

    # -- GitHub ingest tuning ---------------------------------------------
    synchronize_debounce_seconds: int = Field(default=20, ge=0, le=300)
    """Delay applied to ``synchronize`` jobs only.

    Three pushes in thirty seconds produce three events.  A short delay lets
    the later push supersede the earlier ones before any work starts
    (architecture.md §8.5).  ``opened`` is not debounced — the first review
    should feel immediate.
    """

    github_max_files_per_pr: int = Field(default=3000, ge=1)
    """Mirrors GitHub's documented ceiling on the PR files endpoint.

    Reaching it means GitHub truncated the list, which is recorded on the
    pull request rather than silently ignored.
    """

    github_request_timeout_seconds: float = Field(default=20.0, gt=0)
    github_max_retries: int = Field(default=3, ge=0, le=10)

    # -- AI provider ------------------------------------------------------
    ai_provider: AIProviderName = AIProviderName.GEMINI
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    """Model identifier for the Gemini provider.

    Intentionally has **no default**.  The exact supported model is decided in
    the AI integration phase against Google's documentation at that time;
    guessing one here would bake a stale identifier into the foundation.
    """

    gemini_api_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    gemini_max_output_tokens: int = Field(default=8192, ge=256, le=65536)
    gemini_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    """Zero by default: two runs over the same commit should produce the same
    findings. Review is not a creative task, and non-determinism here would
    make deduplication, regression testing and user trust all harder."""

    claude_api_key: SecretStr | None = None
    claude_model: str | None = None

    # -- AI review budgets (free-tier safety) -----------------------------
    ai_max_requests_per_review: int = Field(default=1, ge=1, le=10)
    """Hard ceiling on provider calls for one pull request.

    Deliberately 1. The free tier has a small per-minute quota, and the failure
    mode being prevented is a large PR silently becoming dozens of requests.
    Raising it is a conscious decision, not an emergent one."""

    # ``ai_max_retries`` and ``worker_batch_size`` were removed in Phase 6. Both
    # were declared, documented as knobs, and read by nothing: the provider has
    # no retry loop of its own (the job runner retries the whole job, with
    # ``JOB_MAX_ATTEMPTS``) and the claim loop takes exactly one job per poll.
    # An operator who set either one would have changed nothing while believing
    # they had. Restoring them is a change to the worker or the provider, not a
    # change to this file.
    ai_max_context_bytes: int = Field(default=180_000, ge=1000)
    ai_max_estimated_input_tokens: int = Field(default=60_000, ge=1000)
    """Mirrors architecture.md NFR-P5."""

    ai_max_files_per_review: int = Field(default=40, ge=1)
    ai_max_changed_lines_per_review: int = Field(default=3_000, ge=1)
    ai_max_findings: int = Field(default=25, ge=1, le=100)
    ai_context_lines_around_declaration: int = Field(default=8, ge=0, le=100)

    # -- Review publication ------------------------------------------------
    review_max_inline_comments: int = Field(default=20, ge=1, le=50)
    """A review with sixty inline comments is not read; it is dismissed."""

    review_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    review_min_confidence_high_severity: float = Field(default=0.5, ge=0.0, le=1.0)
    """Lower bar for critical/high: the cost matrix is asymmetric. A missed
    critical bug costs far more than a false positive a developer dismisses."""

    review_publish_enabled: bool = True
    """Kill switch. Analysis and persistence continue; nothing reaches GitHub."""

    # -- Authentication (Phase 5) -------------------------------------------
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: SecretStr | None = None
    """The **OAuth** credentials of the GitHub App.

    Distinct from ``github_app_id`` / ``github_private_key``, which authenticate
    the App *as itself* to act on repositories. These authenticate a **person**,
    and the two must not be confused: the App's private key can never be used to
    establish who is sitting at the browser.
    """

    session_secret: SecretStr | None = None
    """HMAC key for session cookie signing and CSRF token derivation.

    **Production fails to start without it.** There is deliberately no
    development fallback constant: a fallback secret is a secret an attacker
    also has, and the one time it matters is the one time somebody forgot to set
    the real one. Development generates a random key per process instead, so
    forgetting it costs a re-login and never a silent downgrade.

    Rotate by generating a new value; every existing session is invalidated,
    which is the intended effect of a rotation.
    """

    session_ttl_seconds: int = Field(default=8 * 3600, ge=300, le=30 * 86400)
    """Absolute session lifetime. Not extended by activity."""

    session_idle_timeout_seconds: int = Field(default=2 * 3600, ge=300)
    """Idle timeout. A session unused for this long is dead even if it has not
    hit its absolute expiry."""

    session_cookie_name: str = "codeguard_session"
    session_cookie_domain: str | None = None
    session_cookie_secure: bool | None = None
    """``None`` means "decide from the environment": ``Secure`` everywhere
    except development, where there is no TLS on localhost. An explicit value
    overrides, which is what a staging deployment behind a TLS-terminating
    proxy needs."""

    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    """``lax`` is the right default here.

    ``strict`` breaks the OAuth return: the browser arrives at our callback as a
    cross-site navigation from github.com and would not send the cookie holding
    the ``state`` we need to verify. ``none`` would send the cookie on every
    cross-site request including form posts, which is precisely the CSRF
    exposure ``lax`` removes. ``lax`` is not by itself CSRF protection — see
    ``app.auth.csrf`` — it is one of two independent layers.
    """

    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    """How long an in-flight OAuth authorization may take to come back."""

    # -- Rate limiting -------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = Field(default=10, ge=1, le=600)
    """Login and callback attempts per client per minute."""

    rate_limit_api_per_minute: int = Field(default=120, ge=1, le=10_000)
    """Authenticated dashboard reads per session per minute."""

    rate_limit_webhook_per_minute: int = Field(default=600, ge=1, le=100_000)
    """Webhook deliveries per minute. Generous: GitHub is the only caller and a
    busy organisation legitimately bursts. It exists to bound the damage from a
    misconfigured or hostile sender, not to shape GitHub's traffic."""

    # -- Resource ceilings ----------------------------------------------------
    max_request_body_bytes: int = Field(default=1 * 1024 * 1024, ge=1024)
    """Ceiling on any non-webhook request body, enforced before buffering."""

    github_max_response_bytes: int = Field(default=40 * 1024 * 1024, ge=4096)
    """Ceiling on an upstream GitHub response body, enforced while streaming.

    The blob endpoint returns inline base64 with no 1 MB limit, so without this
    one oversized file could put ~100 MB of response plus a ~133 MB string plus
    the decoded bytes into the worker before any size guard ran.
    """

    worker_shutdown_drain_seconds: float = Field(default=120.0, gt=0, le=3600)
    """How long a worker may spend finishing its in-flight job after SIGTERM.

    Must be smaller than the orchestrator's stop grace period, or the process
    is killed while it is still draining and nothing is gained.
    """

    review_run_abandon_timeout_multiplier: int = Field(default=3, ge=1, le=20)
    """Multiple of the job visibility timeout after which a ``running`` review
    run is treated as abandoned. See ``ReviewRunRepository.abandon_stale``."""

    # -- Dashboard API ------------------------------------------------------
    dashboard_allow_unauthenticated_reads: bool = False
    """Serve the read-only dashboard API with **no authentication at all**.

    Phase 4 ships the dashboard API before Phase 5 ships GitHub OAuth and
    sessions. Rather than invent a placeholder session — which the next phase
    would have to find and remove, and which would *look* like security — the
    API fails closed outside development and this flag is the only way to open
    it.

    The name is deliberately alarming and the default is deliberately
    ``False``. An operator who sets it is choosing to expose every monitored
    repository's review data to anyone who can reach the port, and
    ``safe_summary()`` reports the state so it is visible rather than assumed.
    """

    dashboard_max_page_size: int = Field(default=100, ge=1, le=500)
    """Hard server-side ceiling on any list endpoint's page size."""

    # -- Validators -------------------------------------------------------
    @field_validator(
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
        mode="before",
    )
    @classmethod
    def _empty_optional_configuration(cls, value: object) -> object:
        """Compose passes unset variables as empty strings; these are absent.

        Preserve nonblank secrets exactly, including PEM formatting. Normalising
        here keeps health, sign-in availability and runtime checks consistent.
        """
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str) and not raw.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """The application uses SQLAlchemy's asyncio engine end to end.

        A synchronous URL (``postgresql://``) silently produces a driver the
        async engine cannot use, and the failure appears much later as an
        unhelpful ``MissingGreenlet``.  Catching it here turns a confusing
        runtime error into a startup error with an obvious fix.
        """
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver, "
                "e.g. postgresql+asyncpg://user:pass@host:5432/db"
            )
        return value

    @model_validator(mode="after")
    def _fail_fast_on_production_gaps(self) -> Settings:
        """Refuse to start a production-grade deployment that is not secure.

        **Rewritten in Phase 5.** The previous version's docstring said
        "infrastructure secrets must be present outside development" and then
        checked ``debug`` and nothing else — so a production process inherited
        the development ``DATABASE_URL`` default (with ``codeguard:codeguard``
        embedded in it), no session secret, no trusted-host list and no public
        base URL, and started anyway.

        Every check below is a *start-up* failure on purpose. A misconfiguration
        that stops the deploy is an inconvenience; the same misconfiguration
        that starts and serves is an incident.
        """
        if self.app_env in (AppEnv.DEVELOPMENT, AppEnv.TEST):
            return self

        problems: list[str] = []

        if self.debug:
            problems.append("DEBUG must be false outside development.")

        if self.database_url == _DEVELOPMENT_DATABASE_URL:
            problems.append(
                "DATABASE_URL is still the development default, which carries "
                "a well-known username and password. Set a real one."
            )

        if self.session_secret is None or not self.session_secret.get_secret_value():
            problems.append(
                "SESSION_SECRET is required. Sessions cannot be signed without "
                "it, and there is no fallback value by design."
            )
        elif len(self.session_secret.get_secret_value()) < _MIN_SESSION_SECRET_LENGTH:
            problems.append(
                f"SESSION_SECRET must be at least {_MIN_SESSION_SECRET_LENGTH} "
                "characters. Generate one with `openssl rand -base64 48`."
            )

        if self.trusted_hosts.strip() in ("", "*"):
            problems.append(
                "TRUSTED_HOSTS must name the host(s) this deployment serves. "
                "A wildcard accepts any Host header."
            )

        if not self.public_base_url:
            problems.append(
                "PUBLIC_BASE_URL is required: the OAuth redirect URI is built "
                "from it, and deriving it from the request would trust a "
                "client-supplied Host header."
            )
        elif not self.public_base_url.startswith("https://"):
            problems.append("PUBLIC_BASE_URL must use https outside development.")

        if not self.frontend_url.startswith("https://"):
            problems.append("FRONTEND_URL must use https outside development.")

        if self.dashboard_allow_unauthenticated_reads:
            problems.append(
                "DASHBOARD_ALLOW_UNAUTHENTICATED_READS is enabled. That serves "
                "every monitored repository's review data to anyone who can "
                "reach the port, and Phase 5 ships real authentication — there "
                "is no longer a reason to set it."
            )

        if self.session_cookie_samesite == "none" and not self._secure_cookies:
            problems.append(
                "SESSION_COOKIE_SAMESITE=none requires a Secure cookie; "
                "browsers reject the combination without it."
            )

        if problems:
            raise ValueError(
                "Refusing to start: this deployment is not safe to serve.\n  - "
                + "\n  - ".join(problems)
            )
        return self

    # -- Derived helpers --------------------------------------------------
    @property
    def is_development(self) -> bool:
        return self.app_env is AppEnv.DEVELOPMENT

    @property
    def github_private_key_pem(self) -> str | None:
        """Return the App private key as a normalised PEM string.

        Three input encodings are accepted, because each one is what some
        environment system actually produces:

        1.  A real multi-line PEM (``.env`` files, Docker secrets).
        2.  A single line with literal ``\\n`` escapes — what most dashboards
            and CI secret stores do to a multi-line value.
        3.  Base64 of the whole PEM — the encoding recommended for staging and
            production precisely because it survives every transport.

        Detection is by content, not by a separate "which encoding?" setting:
        an extra flag is one more thing to get wrong, and the three forms are
        unambiguously distinguishable.
        """
        if self.github_private_key is None:
            return None
        raw = self.github_private_key.get_secret_value().strip()
        if not raw:
            return None
        if "-----BEGIN" not in raw:
            # Must be base64. A decode failure here is a configuration error,
            # surfaced as one rather than as a confusing signing failure later.
            import base64
            import binascii

            try:
                decoded = base64.b64decode(raw, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
                raise ValueError(
                    "GITHUB_PRIVATE_KEY is neither a PEM block nor valid base64."
                ) from exc
            raw = decoded.strip()
        if "-----BEGIN" not in raw:
            raise ValueError("GITHUB_PRIVATE_KEY did not decode to a PEM block.")
        # Normalise literal escape sequences produced by single-line env vars.
        normalised: str = raw.replace("\\n", "\n")
        return normalised

    @property
    def github_app_configured(self) -> bool:
        """True when the App can authenticate *and* verify webhooks."""
        return (
            self.github_app_id is not None
            and self.github_private_key is not None
            and self.github_webhook_secret is not None
        )

    @property
    def cors_origins(self) -> list[str]:
        return [self.frontend_url.rstrip("/")]

    @property
    def trusted_host_list(self) -> list[str]:
        """``TRUSTED_HOSTS`` split into the list Starlette's middleware wants."""
        raw = [h.strip() for h in self.trusted_hosts.split(",")]
        return [h for h in raw if h] or ["*"]

    @property
    def _secure_cookies(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.app_env is not AppEnv.DEVELOPMENT

    @property
    def cookie_secure(self) -> bool:
        """Whether session cookies carry the ``Secure`` attribute.

        On by default everywhere except development, where localhost has no
        TLS and a ``Secure`` cookie would simply never be stored — making the
        application look broken for a reason nobody would guess.
        """
        return self._secure_cookies

    @property
    def oauth_configured(self) -> bool:
        """True when a *person* can be authenticated.

        Deliberately independent of ``github_app_configured``: the App's private
        key authorises actions on repositories and says nothing about who is
        holding the browser. Confusing the two is how an "authenticated"
        dashboard ends up authenticating nobody.
        """
        return (
            self.github_oauth_client_id is not None
            and self.github_oauth_client_secret is not None
            and self.session_secret is not None
        )

    @property
    def oauth_redirect_uri(self) -> str:
        """The exact redirect URI registered with the GitHub App.

        Built from ``PUBLIC_BASE_URL`` — configuration — never from the inbound
        request. A redirect URI derived from a ``Host`` header can be pointed at
        another origin by forging that header, and GitHub will happily send the
        authorization code to whatever it is told, subject only to its own
        registered-URI check.
        """
        base = (self.public_base_url or self.frontend_url).rstrip("/")
        return f"{base}/api/v1/auth/github/callback"

    def safe_summary(self) -> dict[str, object]:
        """Non-secret configuration summary, safe to log or expose.

        Every value here is either a literal from the environment that carries
        no confidentiality (environment name, version) or a *boolean about*
        a secret rather than the secret itself.  This is the only sanctioned
        way configuration leaves the process.
        """
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "app_env": self.app_env.value,
            "debug": self.debug,
            "log_level": self.log_level,
            "ai_provider": self.ai_provider.value,
            "review_publish_enabled": self.review_publish_enabled,
            # Phase 4 hard-coded this to False. It now reports the actual
            # state: authentication is enforced whenever the escape hatch is
            # off, which outside development is always.
            "dashboard_authenticated": not self.dashboard_allow_unauthenticated_reads,
            "dashboard_unauthenticated_reads_enabled": (self.dashboard_allow_unauthenticated_reads),
            # "configured" only. Whether the credentials WORK is a separate
            # question that only an actual call can answer, and the health
            # endpoint keeps the two apart — see app/api/health.py.
            "oauth_configured": self.oauth_configured,
            "session_secret_configured": self.session_secret is not None,
            "github_app_configured": self.github_app_configured,
            "github_webhook_secret_configured": self.github_webhook_secret is not None,
            "database_configured": bool(self.database_url),
            "trusted_hosts_restricted": self.trusted_hosts.strip() not in ("", "*"),
            "trusted_proxy_count": self.trusted_proxy_count,
            "cookie_secure": self.cookie_secure,
            "cookie_samesite": self.session_cookie_samesite,
            "rate_limit_enabled": self.rate_limit_enabled,
        }

    def session_signing_key(self) -> bytes:
        """The HMAC key, or a per-process random one in development.

        The ephemeral development key is the point, not a shortcut: a shared
        constant in the source would be a real signing key that every reader of
        this repository holds. A key that dies with the process means a
        forgotten ``SESSION_SECRET`` costs one re-login and can never be
        mistaken for a configured deployment.
        """
        if self.session_secret is not None:
            secret: str = self.session_secret.get_secret_value()
            return secret.encode("utf-8")
        if self.app_env in (AppEnv.DEVELOPMENT, AppEnv.TEST):
            return _ephemeral_session_key()
        # Unreachable: the production validator rejects a missing secret.
        raise ValueError("SESSION_SECRET is not configured.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that configuration is parsed and validated exactly once.  Tests
    clear the cache (``get_settings.cache_clear()``) when they need to build
    settings from a different environment.
    """
    return Settings()
