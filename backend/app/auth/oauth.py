"""GitHub OAuth: the authorization URL, the code exchange, and the two
follow-up calls that establish *who* the user is and *what* they may see.

This is the only place in the application that authenticates a **person**. The
GitHub App's private key, used everywhere else, authenticates the *application*
to act on repositories and says nothing at all about who is holding the
browser. Conflating them is how a dashboard ends up "authenticated" as nobody.

Four calls, in order:

1. ``POST https://github.com/login/oauth/access_token`` — trade the code for a
   user access token.
2. ``GET /user`` — the user's numeric id, which is their identity here.
3. ``GET /user/installations`` — which App installations this user can reach.
   **This is the authorization answer**, and it comes from GitHub rather than
   from anything the client sent us.
4. Nothing else. No repository listing, no organisation membership: the
   installation list is sufficient and each extra scope is a permission the
   user has to grant for no reason.

The user access token is used for those calls and then **discarded**. It is
never written to the database, a log, a cookie or a response. The application
has no offline use for it — all repository work uses installation tokens — so
retaining it would be storing a credential to enable exactly nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.core.errors import ConfigurationError, PermanentAccessError, TransientError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _optional_str(value: object) -> str | None:
    """Accept a string from GitHub's JSON, or nothing.

    GitHub sends ``null`` for an unset profile field and, for a field we have
    not seen before, could send a number or an object. Anything that is not a
    string becomes ``None`` rather than being coerced — a display name that is
    secretly an integer would surface much later, somewhere less obvious.
    """
    return value if isinstance(value, str) else None


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 - a public endpoint, not a token

# Empty. A GitHub App's user-to-server token already carries exactly the access
# the *installation* grants; `read:user` would add the profile beyond the public
# fields and `user:email` an address we have no use for. Asking for neither is
# both the smaller blast radius and the easier consent screen to justify.
OAUTH_SCOPES = ""

_TOKEN_TIMEOUT = 15.0
_API_TIMEOUT = 15.0
_MAX_INSTALLATION_PAGES = 20


@dataclass(frozen=True, slots=True)
class GitHubIdentity:
    github_user_id: int
    login: str
    name: str | None
    avatar_url: str | None
    email: str | None
    installation_ids: tuple[int, ...]
    """GitHub's **numeric installation ids**, not our internal ones."""


class GitHubOAuthClient:
    """Talks to GitHub's OAuth and user endpoints. Holds no state."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(_API_TIMEOUT))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> GitHubOAuthClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- step 1: where to send the browser ---------------------------------
    def authorization_url(self, *, state: str) -> str:
        """Build the URL the user is redirected to.

        ``redirect_uri`` comes from ``PUBLIC_BASE_URL``, never from the inbound
        request. GitHub will only redirect to a URI registered on the App, but
        registering a wildcard or a second environment's URI is common — and at
        that point a forged ``Host`` header is enough to steer the code
        somewhere else. Building it from configuration removes the question.
        """
        client_id = self._require_client_id()
        params = {
            "client_id": client_id,
            "redirect_uri": self._settings.oauth_redirect_uri,
            "state": state,
        }
        if OAUTH_SCOPES:
            params["scope"] = OAUTH_SCOPES
        query = httpx.QueryParams(params)
        return f"{GITHUB_AUTHORIZE_URL}?{query}"

    # -- steps 2–4: everything after the browser comes back ----------------
    async def exchange_and_identify(self, *, code: str) -> GitHubIdentity:
        """Trade the code for a token, then use it once and drop it."""
        access_token = await self._exchange_code(code)
        try:
            user = await self._get("/user", access_token)
            github_user_id = user.get("id")
            login = user.get("login")
            if not isinstance(github_user_id, int) or not isinstance(login, str):
                raise PermanentAccessError("GitHub returned an unrecognisable user.")

            installation_ids = await self._list_installations(access_token)
            return GitHubIdentity(
                github_user_id=github_user_id,
                login=login,
                name=_optional_str(user.get("name")),
                avatar_url=_optional_str(user.get("avatar_url")),
                email=_optional_str(user.get("email")),
                installation_ids=installation_ids,
            )
        finally:
            # Nothing to revoke — the token was never persisted — but say so in
            # the log, because "where did the user token go?" should have an
            # answer that is not "we assume it expired".
            del access_token

    # -- internals ---------------------------------------------------------
    def _require_client_id(self) -> str:
        client_id = self._settings.github_oauth_client_id
        if not client_id:
            raise ConfigurationError("GITHUB_OAUTH_CLIENT_ID is not set; sign-in is unavailable.")
        return client_id

    def _require_client_secret(self) -> str:
        secret = self._settings.github_oauth_client_secret
        if secret is None or not secret.get_secret_value():
            raise ConfigurationError(
                "GITHUB_OAUTH_CLIENT_SECRET is not set; sign-in is unavailable."
            )
        return secret.get_secret_value()

    async def _exchange_code(self, code: str) -> str:
        client_id = self._require_client_id()
        client_secret = self._require_client_secret()
        try:
            response = await self._client.post(
                GITHUB_TOKEN_URL,
                # A form body, and the secret is in it rather than in the URL:
                # a query string is logged by proxies, ends up in browser
                # history and is echoed in ``Referer``.
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": self._settings.oauth_redirect_uri,
                },
                headers={"Accept": "application/json"},
                timeout=httpx.Timeout(_TOKEN_TIMEOUT),
            )
        except httpx.HTTPError as exc:
            # 502 to the user: GitHub is unreachable, which is not their fault
            # and not ours (architecture.md §17.4).
            raise TransientError(f"GitHub OAuth is unreachable: {type(exc).__name__}") from exc

        if response.status_code >= 500:
            raise TransientError(f"GitHub OAuth returned {response.status_code}.")
        if response.status_code >= 400:
            # No body in the message. GitHub's error body sometimes echoes the
            # request, and the request contains the client secret.
            raise PermanentAccessError(
                f"GitHub rejected the authorization exchange ({response.status_code})."
            )

        payload = response.json()
        if not isinstance(payload, dict):
            raise PermanentAccessError("GitHub OAuth returned an unexpected body.")
        if payload.get("error"):
            # ``error`` is a short machine code (``bad_verification_code``);
            # ``error_description`` is prose. Log the code only.
            logger.warning("auth.oauth.exchange_rejected", error=str(payload["error"])[:64])
            raise PermanentAccessError("The authorization code was rejected.")

        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise PermanentAccessError("GitHub OAuth returned no access token.")
        return token

    async def _get(self, path: str, access_token: str) -> dict[str, object]:
        response = await self._request(path, access_token)
        body = response.json()
        if not isinstance(body, dict):
            raise PermanentAccessError(f"Unexpected shape from GitHub {path}.")
        return body

    async def _request(self, path: str, access_token: str) -> httpx.Response:
        try:
            response = await self._client.get(
                f"{self._settings.github_api_base_url.rstrip('/')}{path}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "CodeGuard-AI",
                },
            )
        except httpx.HTTPError as exc:
            raise TransientError(f"GitHub is unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 500:
            raise TransientError(f"GitHub returned {response.status_code} for {path}.")
        if response.status_code >= 400:
            raise PermanentAccessError(f"GitHub returned {response.status_code} for {path}.")
        return response

    async def _list_installations(self, access_token: str) -> tuple[int, ...]:
        """Every App installation this user can access, as GitHub sees it.

        The authorization answer. It is asked of GitHub with the *user's* token,
        so it reflects the user's own permissions — not the App's, and not
        anything the browser claimed.
        """
        ids: list[int] = []
        page = 1
        while page <= _MAX_INSTALLATION_PAGES:
            response = await self._request(
                f"/user/installations?per_page=100&page={page}", access_token
            )
            body = response.json()
            if not isinstance(body, dict):
                raise PermanentAccessError("Unexpected shape from /user/installations.")
            items = body.get("installations")
            if not isinstance(items, list):
                raise PermanentAccessError("/user/installations returned no list.")
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), int):
                    ids.append(int(item["id"]))
            if len(items) < 100:
                break
            page += 1
        return tuple(sorted(set(ids)))


__all__ = ["OAUTH_SCOPES", "GitHubIdentity", "GitHubOAuthClient"]
