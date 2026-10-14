"""GitHub REST client.

All HTTP is served by ``httpx.MockTransport``: a real ``httpx`` client over a
fake transport, so retries, header handling, pagination and error
classification are genuinely exercised without a network, a token or a
GitHub account.

**No test here makes a real GitHub API call.** CI needs no credentials.
"""

from __future__ import annotations

import random
import time

import httpx
import pytest
from app.core.config import AppEnv, Settings
from app.core.errors import (
    PermanentAccessError,
    PermanentInputError,
    ThrottledError,
    TransientError,
)
from app.github.client import GitHubClient

INSTALLATION_ID = 77000001
OWNER, REPO, NUMBER = "acme-demo", "widget-service", 42


class StubTokenProvider:
    """Satisfies ``TokenProvider`` structurally — no key, no crypto, no network.

    This is the payoff of splitting authentication from API operations: the
    client's test suite needs no cryptographic material at all.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def get_installation_token(self, installation_id: int) -> str:
        self.calls += 1
        return f"ghs_STUB_{installation_id}"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "github_max_retries": 2,
        "github_request_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _client(handler, **overrides: object) -> GitHubClient:  # type: ignore[no-untyped-def]
    return GitHubClient(
        _settings(**overrides),
        StubTokenProvider(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


REPO_JSON = {
    "id": 550001,
    "name": REPO,
    "full_name": f"{OWNER}/{REPO}",
    "private": False,
    "default_branch": "main",
    "language": "Python",
    "owner": {"id": 440001, "login": OWNER, "type": "Organization"},
}

PR_JSON = {
    "id": 1700000042,
    "number": NUMBER,
    "state": "open",
    "title": "Add session expiry handling",
    "draft": False,
    "merged": False,
    "html_url": f"https://github.com/{OWNER}/{REPO}/pull/{NUMBER}",
    "user": {"id": 900001, "login": "dev-alice", "type": "User"},
    "head": {"ref": "feature/x", "sha": "a" * 40},
    "base": {"ref": "main", "sha": "b" * 40},
}


# -- happy paths ----------------------------------------------------------


async def test_get_repository() -> None:
    async with _client(lambda r: httpx.Response(200, json=REPO_JSON)) as client:
        repo = await client.get_repository(INSTALLATION_ID, OWNER, REPO)
    assert repo.full_name == f"{OWNER}/{REPO}"
    assert repo.owner_login == OWNER


async def test_get_pull_request() -> None:
    async with _client(lambda r: httpx.Response(200, json=PR_JSON)) as client:
        pr = await client.get_pull_request(INSTALLATION_ID, OWNER, REPO, NUMBER)
    assert pr.number == NUMBER
    assert pr.head.sha == "a" * 40
    assert pr.effective_state == "open"


async def test_requests_carry_the_installation_token_and_api_version() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json=REPO_JSON)

    async with _client(handler) as client:
        await client.get_repository(INSTALLATION_ID, OWNER, REPO)

    assert seen["authorization"] == f"Bearer ghs_STUB_{INSTALLATION_ID}"
    assert seen["accept"] == "application/vnd.github+json"
    assert seen["x-github-api-version"] == "2022-11-28"


# -- pagination -----------------------------------------------------------


def _file(name: str, *, status: str = "modified", patch: str | None = "@@ -1 +1 @@") -> dict:
    return {
        "filename": name,
        "status": status,
        "additions": 1,
        "deletions": 0,
        "changes": 1,
        "sha": "c" * 40,
        **({"patch": patch} if patch is not None else {}),
    }


async def test_pagination_follows_the_link_header() -> None:
    """Never assume one response holds every file."""
    pages = {
        1: ([_file(f"src/a{i}.py") for i in range(100)], True),
        2: ([_file(f"src/b{i}.py") for i in range(50)], False),
    }
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        page = int(request.url.params.get("page", 1))
        body, has_next = pages[page]
        headers = {}
        if has_next:
            nxt = str(request.url.copy_set_param("page", page + 1))
            headers["link"] = f'<{nxt}>; rel="next"'
        return httpx.Response(200, json=body, headers=headers)

    async with _client(handler) as client:
        files, truncated = await client.list_pull_request_files(
            INSTALLATION_ID, OWNER, REPO, NUMBER
        )

    assert len(files) == 150
    assert truncated is False
    assert len(seen_urls) == 2
    assert "per_page=100" in seen_urls[0]


async def test_single_page_without_a_link_header_terminates() -> None:
    async with _client(lambda r: httpx.Response(200, json=[_file("a.py")])) as client:
        files, truncated = await client.list_pull_request_files(
            INSTALLATION_ID, OWNER, REPO, NUMBER
        )
    assert len(files) == 1 and truncated is False


async def test_githubs_file_cap_is_detected_and_reported() -> None:
    """Hitting the ceiling means GitHub truncated the list, and we must say so."""

    def handler(request: httpx.Request) -> httpx.Response:
        nxt = str(request.url.copy_set_param("page", 99))
        return httpx.Response(
            200,
            json=[_file(f"f{i}.py") for i in range(100)],
            headers={"link": f'<{nxt}>; rel="next"'},
        )

    async with _client(handler, github_max_files_per_pr=250) as client:
        files, truncated = await client.list_pull_request_files(
            INSTALLATION_ID, OWNER, REPO, NUMBER
        )

    assert len(files) == 250
    assert truncated is True


async def test_empty_file_list_is_valid() -> None:
    async with _client(lambda r: httpx.Response(200, json=[])) as client:
        files, truncated = await client.list_pull_request_files(
            INSTALLATION_ID, OWNER, REPO, NUMBER
        )
    assert files == [] and truncated is False


# -- file shapes ----------------------------------------------------------


async def test_renamed_deleted_and_binary_files_are_handled() -> None:
    """The shapes Phase 2 depends on being classified correctly."""
    body = [
        {**_file("src/new_name.py", status="renamed"), "previous_filename": "src/old_name.py"},
        _file("src/gone.py", status="removed"),
        _file("assets/logo.png", status="modified", patch=None),  # binary: no patch
        _file("src/kept.py"),
    ]

    async with _client(lambda r: httpx.Response(200, json=body)) as client:
        files, _ = await client.list_pull_request_files(INSTALLATION_ID, OWNER, REPO, NUMBER)

    by_path = {f.filename: f for f in files}
    assert by_path["src/new_name.py"].previous_filename == "src/old_name.py"
    assert by_path["src/gone.py"].status == "removed"
    # Absence of a patch is a classification signal, not an error.
    assert by_path["assets/logo.png"].has_patch is False
    assert by_path["src/kept.py"].has_patch is True


# -- error classification -------------------------------------------------


async def test_404_is_permanent_and_not_retried() -> None:
    """Access lost. Retrying cannot bring a deleted repository back."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"message": "Not Found"})

    async with _client(handler) as client:
        with pytest.raises(PermanentAccessError):
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)
    assert calls["n"] == 1


async def test_401_is_permanent() -> None:
    async with _client(lambda r: httpx.Response(401, json={})) as client:
        with pytest.raises(PermanentAccessError):
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)


async def test_422_is_a_permanent_input_error() -> None:
    async with _client(lambda r: httpx.Response(422, json={})) as client:
        with pytest.raises(PermanentInputError):
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)


async def test_403_with_rate_limit_headers_is_throttling_not_forbidden() -> None:
    """403 is overloaded on GitHub: the headers are what disambiguate it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "retry-after": "17"},
            json={"message": "API rate limit exceeded"},
        )

    async with _client(handler, github_max_retries=0) as client:
        with pytest.raises(ThrottledError) as exc:
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)
    assert exc.value.retry_after == 17
    assert exc.value.retryable is True


async def test_403_without_rate_limit_headers_is_permanent() -> None:
    async with _client(lambda r: httpx.Response(403, json={"message": "Forbidden"})) as client:
        with pytest.raises(PermanentAccessError):
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)


async def test_429_uses_the_rate_limit_reset_when_retry_after_is_absent() -> None:
    reset = int(time.time()) + 30

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"x-ratelimit-reset": str(reset)}, json={})

    async with _client(handler, github_max_retries=0) as client:
        with pytest.raises(ThrottledError) as exc:
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)
    assert exc.value.retry_after is not None and 25 <= exc.value.retry_after <= 31


async def test_5xx_is_retried_then_surfaces_as_transient(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={})

    # Collapse the backoff so the test is fast; the delay logic has its own tests.
    monkeypatch.setattr(GitHubClient, "_retry_delay", lambda self, a, e: 0.0)

    async with _client(handler, github_max_retries=2) as client:
        with pytest.raises(TransientError):
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)

    assert calls["n"] == 3  # initial attempt + 2 retries


async def test_a_transient_failure_that_recovers_succeeds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, json={})
        return httpx.Response(200, json=REPO_JSON)

    monkeypatch.setattr(GitHubClient, "_retry_delay", lambda self, a, e: 0.0)

    async with _client(handler) as client:
        repo = await client.get_repository(INSTALLATION_ID, OWNER, REPO)
    assert repo.id == 550001
    assert calls["n"] == 2


async def test_timeout_is_retried_then_transient(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(GitHubClient, "_retry_delay", lambda self, a, e: 0.0)

    async with _client(handler, github_max_retries=1) as client:
        with pytest.raises(TransientError):
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)
    assert calls["n"] == 2


async def test_unexpected_body_shape_is_rejected() -> None:
    async with _client(lambda r: httpx.Response(200, json=["not", "an", "object"])) as client:
        with pytest.raises(PermanentInputError):
            await client.get_repository(INSTALLATION_ID, OWNER, REPO)


# -- rate-limit observability --------------------------------------------


async def test_rate_limit_headers_are_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=REPO_JSON,
            headers={
                "x-ratelimit-limit": "5000",
                "x-ratelimit-remaining": "4987",
                "x-ratelimit-used": "13",
                "x-ratelimit-reset": "1789000000",
            },
        )

    async with _client(handler) as client:
        await client.get_repository(INSTALLATION_ID, OWNER, REPO)
        assert client.rate_limit.remaining == 4987
        assert client.rate_limit.limit == 5000


async def test_malformed_rate_limit_headers_do_not_break_the_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=REPO_JSON, headers={"x-ratelimit-remaining": "lots"})

    async with _client(handler) as client:
        await client.get_repository(INSTALLATION_ID, OWNER, REPO)
        assert client.rate_limit.remaining is None


def test_retry_delay_honours_a_server_directed_delay() -> None:
    """Substituting our own backoff for a Retry-After earns a longer ban."""
    client = _client(lambda request: httpx.Response(500))
    delay = client._retry_delay(1, ThrottledError("slow down", retry_after=42))
    assert delay == 42.0


def test_retry_delay_is_jittered() -> None:
    """Without jitter, everything that failed together retries together."""
    client = _client(lambda request: httpx.Response(500))
    random.seed(1)
    delays = {round(client._retry_delay(2, None), 6) for _ in range(20)}
    assert len(delays) > 1
