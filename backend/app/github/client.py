"""GitHub REST client.

Every GitHub quirk lives here — pagination, rate-limit headers, the difference
between a primary and a secondary limit, retry classification — rather than
smeared across services and route handlers. A raw ``httpx`` call anywhere else
in the codebase is a bug.

Note the split from ``app_auth``: **authentication** (private key → JWT →
installation token) and **API operations** are separate concerns with
different lifetimes and different secrets. The client receives a
``TokenProvider`` protocol and never sees a private key, which means its test
suite needs no cryptographic material at all.

What this client deliberately does **not** do: clone a repository or execute
anything. From Phase 2 it also serves individual file blobs at an exact commit
(see ``app.analysis.source.provider``) — one file at a time, only for files the
pull request touched.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import (
    AmbiguousWriteError,
    GitHubValidationError,
    PermanentAccessError,
    PermanentInputError,
    ResponseTooLargeError,
    ThrottledError,
    TransientError,
)
from app.core.logging import get_logger
from app.github.app_auth import GITHUB_ACCEPT, GITHUB_API_VERSION, TokenProvider
from app.github.models import (
    GitHubChangedFile,
    GitHubPullRequest,
    GitHubRepository,
    RateLimitSnapshot,
)

logger = get_logger(__name__)

_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_MAX_PER_PAGE = 100


class GitHubClient:
    """Typed, resilient access to the handful of endpoints Phase 1 needs."""

    def __init__(
        self,
        settings: Settings,
        token_provider: TokenProvider,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._tokens = token_provider
        self._base = settings.github_api_base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.github_request_timeout_seconds),
        )
        self._owns_client = client is None
        self.rate_limit = RateLimitSnapshot()
        # Set per request so `_classify` can invalidate the right cache entry.
        self._installation_id: int = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- public operations -------------------------------------------------
    async def get_repository(self, installation_id: int, owner: str, repo: str) -> GitHubRepository:
        data = await self._get_json(installation_id, f"/repos/{owner}/{repo}")
        return GitHubRepository.model_validate(data)

    async def get_pull_request(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> GitHubPullRequest:
        data = await self._get_json(installation_id, f"/repos/{owner}/{repo}/pulls/{number}")
        return GitHubPullRequest.model_validate(data)

    async def list_pull_request_files(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> tuple[list[GitHubChangedFile], bool]:
        """Return every changed file, and whether GitHub truncated the list.

        Never assume one response holds every file: the endpoint paginates at
        100 per page and caps the total (3000 at the time of writing). Hitting
        the cap means **GitHub** truncated the list, not us — that is recorded
        and surfaced, because a caller who does not know the list was cut short
        will over-trust a later "clean" result.
        """
        files: list[GitHubChangedFile] = []
        path: str | None = f"/repos/{owner}/{repo}/pulls/{number}/files?per_page={_MAX_PER_PAGE}"
        truncated = False

        while path:
            response = await self._request(installation_id, "GET", path)
            page = response.json()
            if not isinstance(page, list):
                raise PermanentInputError("Unexpected shape from the pull request files endpoint.")
            files.extend(GitHubChangedFile.model_validate(item) for item in page)

            if len(files) >= self._settings.github_max_files_per_pr:
                files = files[: self._settings.github_max_files_per_pr]
                truncated = True
                break

            path = _next_page(response)

        logger.info(
            "github.pull_request_files.fetched",
            repository=f"{owner}/{repo}",
            pull_request_number=number,
            file_count=len(files),
            truncated=truncated,
        )
        return files, truncated

    async def list_installation_repositories(
        self, installation_id: int, *, max_repositories: int = 2000
    ) -> list[GitHubRepository]:
        """Every repository this installation currently grants access to.

        Uses the installation token's own endpoint rather than the App JWT's,
        so the answer is exactly "what this token can reach" — which is the
        question that matters for authorization. A repository removed from the
        installation simply stops appearing here.

        Bounded by ``max_repositories``: an organisation-wide installation on a
        very large org would otherwise page indefinitely into memory.
        """
        repositories: list[GitHubRepository] = []
        path: str | None = f"/installation/repositories?per_page={_MAX_PER_PAGE}"

        while path:
            response = await self._request(installation_id, "GET", path)
            body = response.json()
            if not isinstance(body, dict):
                raise PermanentInputError(
                    "Unexpected shape from the installation repositories endpoint."
                )
            page = body.get("repositories")
            if not isinstance(page, list):
                raise PermanentInputError(
                    "Installation repositories response has no repositories array."
                )
            repositories.extend(GitHubRepository.model_validate(item) for item in page)
            if len(repositories) >= max_repositories:
                repositories = repositories[:max_repositories]
                logger.warning(
                    "github.installation_repositories.truncated",
                    installation_id=installation_id,
                    limit=max_repositories,
                )
                break
            path = _next_page(response)

        logger.info(
            "github.installation_repositories.fetched",
            installation_id=installation_id,
            repository_count=len(repositories),
        )
        return repositories

    async def post_json(
        self, installation_id: int, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Authenticated POST returning a JSON object.

        Phase 1 was read-only; publishing a review needs a body. Routing it
        through the same `_request` means retries, rate-limit handling and
        error classification apply identically to writes — with one critical
        difference handled in `_classify`: a write is **not** blindly retried,
        because a timed-out POST may already have taken effect.
        """
        response = await self._request(installation_id, "POST", path, body=body)
        data = response.json()
        if not isinstance(data, dict):
            raise PermanentInputError(f"Expected a JSON object from {path}.")
        return data

    async def create_pull_request_review(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create one PR review, optionally with inline comments.

        `event` is hard-coded to COMMENT and is not a parameter. The App must
        never issue APPROVE or REQUEST_CHANGES (architecture.md FR-1.10), and a
        constant cannot be got wrong by a caller.

        GitHub applies this atomically: one invalid comment rejects the whole
        request with 422. The publisher's salvage path depends on that, so it
        is stated here rather than discovered.
        """
        payload: dict[str, Any] = {
            "commit_id": commit_id,
            "event": "COMMENT",
            "body": body,
        }
        if comments:
            payload["comments"] = comments
        return await self.post_json(
            installation_id, f"/repos/{owner}/{repo}/pulls/{number}/reviews", payload
        )

    async def list_review_comments(
        self, installation_id: int, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        """Existing review comments on a PR, paginated.

        Read before publishing so that a retry after an ambiguous failure can
        tell "the comment was never created" from "the response was lost on the
        way back". Without this, a retried publication duplicates comments.
        """
        out: list[dict[str, Any]] = []
        path: str | None = f"/repos/{owner}/{repo}/pulls/{number}/comments?per_page={_MAX_PER_PAGE}"
        while path:
            response = await self._request(installation_id, "GET", path)
            page = response.json()
            if not isinstance(page, list):
                raise PermanentInputError("Unexpected shape from the review comments endpoint.")
            out.extend(item for item in page if isinstance(item, dict))
            path = _next_page(response)
        return out

    async def get_json(self, installation_id: int, path: str) -> dict[str, Any]:
        """Authenticated GET returning a JSON object.

        Public because Phase 2's source provider needs blob and contents
        endpoints. It stays inside this class so that retries, rate-limit
        handling and error classification apply to those calls too — a raw
        httpx call in the analysis layer would bypass all three.
        """
        return await self._get_json(installation_id, path)

    # -- transport ---------------------------------------------------------
    async def _get_json(self, installation_id: int, path: str) -> dict[str, Any]:
        response = await self._request(installation_id, "GET", path)
        data = response.json()
        if not isinstance(data, dict):
            raise PermanentInputError(f"Expected a JSON object from {path}.")
        return data

    async def _request(
        self,
        installation_id: int,
        method: str,
        path_or_url: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform one authenticated request, with retries and classification.

        The retry loop covers transient failures only. Classification happens
        first, so a permanent error (404, 401, 422) fails immediately instead
        of burning the retry budget on an outcome that cannot change.
        """
        url = path_or_url if path_or_url.startswith("http") else f"{self._base}{path_or_url}"
        # Phase 5: an absolute URL here comes from an upstream ``Link`` header,
        # and every request carries an installation token. Pinning the host to
        # the configured API base means a rewritten or spoofed pagination link
        # cannot make us hand that token to somewhere else. A relative path is
        # ours by construction and needs no check.
        self._require_trusted_host(url)
        self._installation_id = installation_id
        attempts = self._settings.github_max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            token = await self._tokens.get_installation_token(installation_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": GITHUB_ACCEPT,
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "CodeGuard-AI",
            }
            try:
                response = await self._send_bounded(method, url, headers=headers, body=body)
            except ResponseTooLargeError:
                # Permanent: the same blob will be the same size next time.
                raise
            except httpx.TimeoutException:
                last_error = TransientError(f"Timed out calling GitHub: {method} {url}")
                logger.warning("github.request.timeout", attempt=attempt, method=method)
                if not _is_safe_to_retry(method):
                    # A timed-out write may already have taken effect on the
                    # server. Retrying it blindly is how duplicate review
                    # comments appear. The caller reconciles instead.
                    raise AmbiguousWriteError(
                        f"{method} {url} timed out; the write may have succeeded."
                    ) from None
            except httpx.HTTPError as exc:
                last_error = TransientError(f"Network error calling GitHub: {type(exc).__name__}")
                logger.warning(
                    "github.request.network_error",
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                if not _is_safe_to_retry(method):
                    raise AmbiguousWriteError(
                        f"{method} {url} failed mid-flight; the write may have succeeded."
                    ) from exc
            else:
                self._record_rate_limit(response)
                classified = self._classify(response, method, url)
                if classified is None:
                    return response
                last_error = classified
                if not getattr(classified, "retryable", False):
                    raise classified

            if attempt < attempts:
                await asyncio.sleep(self._retry_delay(attempt, last_error))

        assert last_error is not None  # loop always sets it before exiting
        raise last_error

    async def _send_bounded(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> httpx.Response:
        """Send a request and read at most ``github_max_response_bytes``.

        **Phase 5 fix.** ``AsyncClient.request`` buffers the entire response
        before returning, with no ceiling. The blob endpoint is deliberately
        used because it has no 1 MB limit — GitHub will return inline base64 up
        to 100 MB — so one oversized file cost roughly 100 MB of response plus
        a 133 MB string plus the decoded bytes, all resident in the worker. The
        ``max_file_bytes`` guard downstream ran *after* all of that.

        Streaming and counting as we go means an oversized body is abandoned
        after one chunk over the limit, and the connection is closed rather
        than drained.
        """
        limit = self._settings.github_max_response_bytes
        request = self._client.build_request(method, url, headers=headers, json=body)
        response = await self._client.send(request, stream=True)
        try:
            declared = response.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > limit:
                raise ResponseTooLargeError(
                    f"GitHub response declares {declared} bytes, over the {limit}-byte ceiling."
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > limit:
                    raise ResponseTooLargeError(
                        f"GitHub response exceeded the {limit}-byte ceiling."
                    )
                chunks.append(chunk)
        finally:
            await response.aclose()
        # Hand the retry/classification path a normal, fully-read response.
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=b"".join(chunks),
            request=request,
        )

    def _require_trusted_host(self, url: str) -> None:
        """Refuse to send an installation token anywhere but the configured API.

        Permanent, not transient: a URL pointing at another host will point
        there on every retry, and retrying would mean sending the token again.
        """
        target = httpx.URL(url)
        base = httpx.URL(self._base)
        if (target.scheme, target.host, target.port) != (base.scheme, base.host, base.port):
            logger.error(
                "github.request.untrusted_host",
                # The host only — never the full URL, which may carry a token
                # in a query string placed there by whoever rewrote the link.
                host=target.host,
                expected_host=base.host,
            )
            raise PermanentAccessError(
                "Refusing to send an installation token to a host other than "
                "the configured GitHub API."
            )

    def _classify(self, response: httpx.Response, method: str, url: str) -> Exception | None:
        """Map a response to an exception, or ``None`` when it succeeded."""
        status = response.status_code
        if 200 <= status < 300:
            return None

        if status in (401, 403):
            # 403 is overloaded on GitHub: it is both "forbidden" and the
            # secondary rate limit. The headers are what distinguish them, so
            # they are checked rather than guessed at.
            if _is_rate_limited(response):
                return ThrottledError(
                    "GitHub rate limit reached.", retry_after=_retry_after(response)
                )
            if status == 401:
                # The cached token is no longer accepted — revoked, rotated, or
                # the installation's permissions changed. Drop it, so a later
                # job mints a fresh one instead of replaying a dead credential
                # until its natural expiry.
                invalidate = getattr(self._tokens, "invalidate", None)
                if callable(invalidate):
                    invalidate(self._installation_id)
                return PermanentAccessError("GitHub rejected the installation token.")
            return PermanentAccessError(f"Forbidden: {method} {url}")

        if status == 404:
            # For a resource we were told about by a webhook, 404 means access
            # was lost — uninstalled, made private, or deleted. Never retried.
            return PermanentAccessError(f"Not found or no longer accessible: {method} {url}")

        if status == 429:
            return ThrottledError("GitHub rate limit reached.", retry_after=_retry_after(response))

        if status in (408, 502, 503, 504) or status >= 500:
            return TransientError(f"GitHub returned {status} for {method} {url}")

        if status == 422:
            # Distinguished from other 4xx because review creation is atomic:
            # one unpositionable comment rejects the whole review, and the
            # publisher salvages rather than discards. See GitHubValidationError.
            return GitHubValidationError(f"GitHub rejected the request body: {method} {url}")

        return PermanentInputError(f"GitHub returned {status} for {method} {url}")

    def _retry_delay(self, attempt: int, error: Exception | None) -> float:
        """Exponential backoff with full jitter, honouring server instructions.

        Jitter is not cosmetic: without it, N requests that failed together
        retry together, reproducing the load spike that caused the failure.
        """
        if isinstance(error, ThrottledError) and error.retry_after is not None:
            return float(min(error.retry_after, self._settings.job_backoff_cap_seconds))
        base = min(2.0 ** (attempt - 1), 30.0)
        return base * random.uniform(0.5, 1.5)  # noqa: S311 — jitter, not crypto

    def _record_rate_limit(self, response: httpx.Response) -> None:
        def _int(name: str) -> int | None:
            raw = response.headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except ValueError:
                return None

        self.rate_limit = RateLimitSnapshot(
            limit=_int("x-ratelimit-limit"),
            remaining=_int("x-ratelimit-remaining"),
            reset_epoch=_int("x-ratelimit-reset"),
            used=_int("x-ratelimit-used"),
        )
        if self.rate_limit.remaining is not None and self.rate_limit.remaining < 100:
            logger.warning(
                "github.rate_limit.low",
                remaining=self.rate_limit.remaining,
                reset_epoch=self.rate_limit.reset_epoch,
            )


_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_safe_to_retry(method: str) -> bool:
    """Only idempotent methods may be retried after an ambiguous failure."""
    return method.upper() in _IDEMPOTENT_METHODS


def _next_page(response: httpx.Response) -> str | None:
    """Follow GitHub's ``Link`` header rather than incrementing a page number.

    Constructing the next URL ourselves means guessing at parameters GitHub
    may add; the server already tells us the exact next URL.
    """
    link = response.headers.get("link")
    if not link:
        return None
    match = _LINK_NEXT.search(link)
    return match.group(1) if match else None


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    if response.headers.get("retry-after"):
        return True
    body = response.text[:500].lower() if response.content else ""
    return "rate limit" in body or "secondary rate" in body


def _retry_after(response: httpx.Response) -> int | None:
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
