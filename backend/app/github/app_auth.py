"""GitHub App authentication.

    private key (PEM, RS256)
        │ sign
        ▼
    App JWT        iss = App ID, iat = now - 60s, exp = now + 540s
        │ POST /app/installations/{id}/access_tokens
        ▼
    installation access token   (~1 hour, scoped to that installation)
        │
        ▼
    Authorization: Bearer <installation token>

This is the only module that touches the private key, which is what keeps the
security review of key handling small. The key is read from configuration into
process memory and is never written to the database, a log, a response or a
temp file.

Tokens are cached **in memory only**, keyed by installation. Not in Redis, not
in PostgreSQL: a cached credential in shared storage is a credential at rest
with a second attack surface, in exchange for avoiding a cheap API call. The
cache dies with the process, which is the correct lifetime for a bearer token.

Two details that cause intermittent, hard-to-diagnose 401s if missed:

*   ``iat`` is backdated 60 seconds. GitHub rejects a JWT whose ``iat`` is in
    its future, and small clock skew between our host and GitHub is normal.
*   ``exp`` is 9 minutes, under GitHub's 10-minute maximum, leaving headroom
    for skew in the other direction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
import jwt

from app.core.config import Settings
from app.core.errors import (
    ConfigurationError,
    PermanentAccessError,
    ThrottledError,
    TransientError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

JWT_EXPIRY_SECONDS = 9 * 60
JWT_BACKDATE_SECONDS = 60
# Refresh this long before expiry so a token never expires mid-request.
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60

GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_API_VERSION = "2022-11-28"


@dataclass(frozen=True, slots=True)
class InstallationToken:
    """A short-lived installation access token.

    ``__repr__`` is suppressed so the token cannot reach a log line, a
    traceback or an f-string by accident.
    """

    token: str
    expires_at: datetime

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"InstallationToken(expires_at={self.expires_at.isoformat()}, token=***)"

    __str__ = __repr__

    def is_fresh(self, *, margin_seconds: int = TOKEN_REFRESH_MARGIN_SECONDS) -> bool:
        return datetime.now(UTC) < self.expires_at - timedelta(seconds=margin_seconds)


class TokenProvider(Protocol):
    """What the API client needs in order to authenticate.

    A protocol, so the client can be tested with a trivial stub and never
    needs a private key in its test suite.
    """

    async def get_installation_token(self, installation_id: int) -> str: ...


def build_app_jwt(*, app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """Mint a short-lived App JWT signed with RS256.

    Pure apart from the clock, which is injectable, so expiry and backdating
    are directly testable without waiting or monkey-patching time.
    """
    issued = int(now if now is not None else time.time())
    payload = {
        "iat": issued - JWT_BACKDATE_SECONDS,
        "exp": issued + JWT_EXPIRY_SECONDS,
        "iss": app_id,
    }
    try:
        return jwt.encode(payload, private_key_pem, algorithm="RS256")
    except Exception as exc:
        raise ConfigurationError(
            "Unable to sign the GitHub App JWT. Check GITHUB_APP_ID and GITHUB_PRIVATE_KEY."
        ) from exc


class GitHubAppAuthenticator:
    """Mints App JWTs and exchanges them for installation access tokens."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._cache: dict[int, InstallationToken] = {}

    # -- configuration ----------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return (
            self._settings.github_app_id is not None
            and self._settings.github_private_key is not None
        )

    def _require_config(self) -> tuple[str, str]:
        app_id = self._settings.github_app_id
        pem = self._settings.github_private_key_pem
        if not app_id or not pem:
            raise ConfigurationError(
                "GitHub App is not configured: set GITHUB_APP_ID and GITHUB_PRIVATE_KEY."
            )
        return app_id, pem

    def create_app_jwt(self) -> str:
        app_id, pem = self._require_config()
        return build_app_jwt(app_id=app_id, private_key_pem=pem)

    # -- installation tokens ----------------------------------------------
    async def get_installation_token(self, installation_id: int) -> str:
        """Return a valid token for an installation, from cache when possible."""
        cached = self._cache.get(installation_id)
        if cached is not None and cached.is_fresh():
            return cached.token
        minted = await self._mint_installation_token(installation_id)
        self._cache[installation_id] = minted
        return minted.token

    def invalidate(self, installation_id: int) -> None:
        """Drop a cached token, e.g. after an unexpected 401."""
        self._cache.pop(installation_id, None)

    async def _mint_installation_token(self, installation_id: int) -> InstallationToken:
        app_jwt = self.create_app_jwt()
        url = (
            f"{self._settings.github_api_base_url.rstrip('/')}"
            f"/app/installations/{installation_id}/access_tokens"
        )
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": GITHUB_ACCEPT,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._settings.github_request_timeout_seconds)
        try:
            response = await client.post(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise TransientError("Timed out requesting an installation token.") from exc
        except httpx.HTTPError as exc:
            raise TransientError("Network failure requesting an installation token.") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 201:
            data = response.json()
            expires_at = _parse_expiry(data.get("expires_at"))
            logger.info(
                "github.installation_token.minted",
                installation_id=installation_id,
                expires_at=expires_at.isoformat(),
            )
            return InstallationToken(token=str(data["token"]), expires_at=expires_at)

        # 401 here means our own JWT was rejected — a key/app-id problem, not a
        # transient one. 404 means the installation is gone (uninstalled).
        if response.status_code == 401:
            raise ConfigurationError(
                "GitHub rejected the App JWT. Check GITHUB_APP_ID and the private key."
            )
        if response.status_code == 404:
            raise PermanentAccessError(
                f"Installation {installation_id} is not accessible; it may have been removed."
            )
        if response.status_code in (403, 429):
            retry_after = _retry_after_seconds(response)
            raise ThrottledError(
                "Rate limited while requesting an installation token.",
                retry_after=retry_after,
            )
        if response.status_code >= 500:
            raise TransientError(
                f"GitHub returned {response.status_code} for an installation token."
            )
        raise PermanentAccessError(
            f"Unexpected status {response.status_code} requesting an installation token."
        )


def _parse_expiry(raw: str | None) -> datetime:
    """Parse GitHub's ``expires_at``, falling back to a conservative hour.

    A missing or unparseable expiry must not produce a token treated as valid
    forever, so the fallback is shorter than GitHub's real lifetime, never
    longer.
    """
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:  # pragma: no cover — GitHub sends ISO-8601
            pass
    return datetime.now(UTC) + timedelta(minutes=55)


def _retry_after_seconds(response: httpx.Response) -> int | None:
    """Extract a server-directed delay from a throttling response.

    Prefers ``Retry-After``; falls back to the time until the rate-limit
    window resets. Choosing our own backoff instead of honouring the server's
    instruction is how a client earns a longer ban.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(0, int(header))
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        try:
            return max(0, int(reset) - int(time.time()))
        except ValueError:
            pass
    return None
