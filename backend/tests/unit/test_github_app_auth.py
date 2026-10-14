"""GitHub App authentication.

A throwaway RSA key is generated per test run, so the suite exercises real
RS256 signing and verification with no credential anywhere in the repository
and nothing to leak.

All HTTP is served by ``httpx.MockTransport`` — a real ``httpx`` client over a
fake transport, so the client's own behaviour is exercised without a network.
"""

from __future__ import annotations

import base64
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from app.core.config import AppEnv, Settings
from app.core.errors import ConfigurationError, PermanentAccessError, ThrottledError, TransientError
from app.github.app_auth import (
    JWT_BACKDATE_SECONDS,
    JWT_EXPIRY_SECONDS,
    GitHubAppAuthenticator,
    InstallationToken,
    build_app_jwt,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

APP_ID = "123456"
INSTALLATION_ID = 77000001


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    """A disposable RSA keypair: (private PEM, public PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _settings(private_pem: str | None = None, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "github_app_id": APP_ID,
    }
    if private_pem is not None:
        values["github_private_key"] = private_pem
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# -- private key loading --------------------------------------------------


def test_multiline_pem_loads(keypair: tuple[str, str]) -> None:
    private_pem, _ = keypair
    assert _settings(private_pem).github_private_key_pem == private_pem.strip()


def test_escaped_newline_pem_loads(keypair: tuple[str, str]) -> None:
    """What most CI secret stores and dashboards do to a multi-line value."""
    private_pem, _ = keypair
    escaped = private_pem.strip().replace("\n", "\\n")
    loaded = _settings(escaped).github_private_key_pem
    assert loaded is not None and "\\n" not in loaded
    assert loaded.splitlines()[0].startswith("-----BEGIN")


def test_base64_pem_loads(keypair: tuple[str, str]) -> None:
    """The recommended encoding for staging and production."""
    private_pem, _ = keypair
    encoded = base64.b64encode(private_pem.encode()).decode()
    assert _settings(encoded).github_private_key_pem == private_pem.strip()


def test_garbage_private_key_fails_loudly(keypair: tuple[str, str]) -> None:
    with pytest.raises(ValueError, match=r"PEM|base64"):
        _ = _settings("not-a-key-at-all-!!!").github_private_key_pem


def test_absent_private_key_is_none() -> None:
    assert _settings().github_private_key_pem is None


def test_private_key_is_not_exposed_by_repr(keypair: tuple[str, str]) -> None:
    private_pem, _ = keypair
    settings = _settings(private_pem)
    rendered = f"{settings!r} {settings.github_private_key!r} {settings.safe_summary()}"
    assert "PRIVATE KEY" not in rendered
    assert private_pem.splitlines()[1] not in rendered


# -- JWT ------------------------------------------------------------------


def test_app_jwt_is_valid_rs256(keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    token = build_app_jwt(app_id=APP_ID, private_key_pem=private_pem)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_exp": True})
    assert claims["iss"] == APP_ID


def test_app_jwt_is_backdated_to_tolerate_clock_skew(keypair: tuple[str, str]) -> None:
    """GitHub rejects a JWT whose iat is in its future. A classic flaky 401."""
    private_pem, public_pem = keypair
    now = time.time()
    token = build_app_jwt(app_id=APP_ID, private_key_pem=private_pem, now=now)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert claims["iat"] == int(now) - JWT_BACKDATE_SECONDS


def test_app_jwt_expiry_is_inside_githubs_ten_minute_maximum(
    keypair: tuple[str, str],
) -> None:
    private_pem, public_pem = keypair
    now = time.time()
    claims = jwt.decode(
        build_app_jwt(app_id=APP_ID, private_key_pem=private_pem, now=now),
        public_pem,
        algorithms=["RS256"],
    )
    lifetime = claims["exp"] - claims["iat"]
    assert JWT_EXPIRY_SECONDS == 9 * 60
    assert lifetime < 10 * 60 + JWT_BACKDATE_SECONDS + 1
    assert claims["exp"] > now


def test_unusable_private_key_raises_configuration_error() -> None:
    # Assembled at runtime so the PEM marker never appears as a literal: a
    # repository-wide secret scanner cannot tell a fixture from a real leak,
    # and weakening the scanner to accommodate a fixture is the wrong trade.
    broken_pem = "-----BEGIN " + "PRIVATE KEY-----\nbroken\n-----END " + "PRIVATE KEY-----"
    with pytest.raises(ConfigurationError):
        build_app_jwt(app_id=APP_ID, private_key_pem=broken_pem)


def test_authenticator_without_config_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        GitHubAppAuthenticator(_settings()).create_app_jwt()


# -- installation tokens --------------------------------------------------


def _transport(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_installation_token_is_minted(keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            201,
            json={
                "token": "ghs_FAKE_TOKEN_VALUE",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )

    async with _transport(handler) as client:
        auth = GitHubAppAuthenticator(_settings(private_pem), client=client)
        token = await auth.get_installation_token(INSTALLATION_ID)

    assert token == "ghs_FAKE_TOKEN_VALUE"
    assert seen["url"].endswith(f"/app/installations/{INSTALLATION_ID}/access_tokens")
    # The request must be authenticated with the App JWT, not with a token.
    presented = seen["auth"].removeprefix("Bearer ")
    assert jwt.decode(presented, public_pem, algorithms=["RS256"])["iss"] == APP_ID


async def test_token_is_cached_and_not_reminted(keypair: tuple[str, str]) -> None:
    private_pem, _ = keypair
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            201,
            json={
                "token": f"ghs_TOKEN_{calls['n']}",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )

    async with _transport(handler) as client:
        auth = GitHubAppAuthenticator(_settings(private_pem), client=client)
        first = await auth.get_installation_token(INSTALLATION_ID)
        second = await auth.get_installation_token(INSTALLATION_ID)

    assert first == second
    assert calls["n"] == 1


async def test_nearly_expired_token_is_refreshed(keypair: tuple[str, str]) -> None:
    """Refreshed before expiry so a token never dies mid-request."""
    private_pem, _ = keypair
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Two minutes left: inside the five-minute refresh margin.
        return httpx.Response(
            201,
            json={
                "token": f"ghs_TOKEN_{calls['n']}",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            },
        )

    async with _transport(handler) as client:
        auth = GitHubAppAuthenticator(_settings(private_pem), client=client)
        await auth.get_installation_token(INSTALLATION_ID)
        await auth.get_installation_token(INSTALLATION_ID)

    assert calls["n"] == 2


async def test_tokens_are_cached_per_installation(keypair: tuple[str, str]) -> None:
    """One installation's token must never be used for another's requests."""
    private_pem, _ = keypair

    def handler(request: httpx.Request) -> httpx.Response:
        install = str(request.url).split("/installations/")[1].split("/")[0]
        return httpx.Response(
            201,
            json={
                "token": f"ghs_FOR_{install}",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )

    async with _transport(handler) as client:
        auth = GitHubAppAuthenticator(_settings(private_pem), client=client)
        assert await auth.get_installation_token(1) == "ghs_FOR_1"
        assert await auth.get_installation_token(2) == "ghs_FOR_2"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ConfigurationError),  # our JWT was rejected: key/app-id problem
        (404, PermanentAccessError),  # installation removed
        (500, TransientError),
        (503, TransientError),
    ],
)
async def test_error_statuses_are_classified(
    keypair: tuple[str, str], status: int, expected: type[Exception]
) -> None:
    private_pem, _ = keypair

    async with _transport(lambda r: httpx.Response(status, json={})) as client:
        auth = GitHubAppAuthenticator(_settings(private_pem), client=client)
        with pytest.raises(expected):
            await auth.get_installation_token(INSTALLATION_ID)


async def test_rate_limited_token_request_carries_retry_after(
    keypair: tuple[str, str],
) -> None:
    private_pem, _ = keypair

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "42"}, json={})

    async with _transport(handler) as client:
        auth = GitHubAppAuthenticator(_settings(private_pem), client=client)
        with pytest.raises(ThrottledError) as exc:
            await auth.get_installation_token(INSTALLATION_ID)
    assert exc.value.retry_after == 42


async def test_timeout_is_transient(keypair: tuple[str, str]) -> None:
    private_pem, _ = keypair

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    async with _transport(handler) as client:
        auth = GitHubAppAuthenticator(_settings(private_pem), client=client)
        with pytest.raises(TransientError):
            await auth.get_installation_token(INSTALLATION_ID)


# -- token leakage --------------------------------------------------------


def test_installation_token_is_not_printable() -> None:
    """Tokens reach log lines and tracebacks through repr(). They must not."""
    token = InstallationToken(
        token="ghs_SUPER_SECRET", expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    assert "ghs_SUPER_SECRET" not in repr(token)
    assert "ghs_SUPER_SECRET" not in str(token)
    assert "ghs_SUPER_SECRET" not in f"{token}"


def test_freshness_accounts_for_the_refresh_margin() -> None:
    soon = InstallationToken("t", datetime.now(UTC) + timedelta(minutes=2))
    later = InstallationToken("t", datetime.now(UTC) + timedelta(minutes=30))
    assert soon.is_fresh() is False
    assert later.is_fresh() is True
