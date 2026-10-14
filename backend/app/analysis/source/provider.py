"""Source retrieval at an exact commit.

    SourceProvider           (Protocol — the contract)
        │
        ├── GitHubSourceProvider    fetches blobs over the REST API
        └── InMemorySourceProvider  for tests; holds content given to it

``InMemorySourceProvider`` is not a fake GitHub. It does not pretend to call an
API, return canned HTTP, or simulate pagination. It is a second, honest
implementation of the contract that serves content a test handed it — which is
what lets the diff, mapping and pipeline layers be tested with no network at
all, as the brief requires.

**The repository is never cloned.** Only the files a pull request actually
touched are fetched, one blob at a time, at the exact head commit. Nothing is
executed, imported, built, or installed.

Why blobs rather than the contents endpoint, where a blob SHA is known:

*   It is exact. A blob SHA names immutable content; a path plus a ref is
    resolved by the server and can race a force-push.
*   No path interpolation. A repository controls its own file names, and a
    name containing ``../`` or a URL-significant character never reaches a URL
    at all on this path.
*   No 1 MB ceiling. The contents endpoint truncates; the blob endpoint does
    not.

The contents endpoint remains as a fallback for files whose blob SHA we do not
have, and there the path *is* interpolated — so it is percent-encoded, and a
path that escapes its own repository is rejected before the request is built.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from app.analysis.source.models import SourceFile
from app.core.errors import PermanentInputError
from app.core.logging import get_logger
from app.github.client import GitHubClient

logger = get_logger(__name__)

MAX_DECODE_BYTES = 8 * 1024 * 1024


class SourceRetrievalError(Exception):
    """Content could not be obtained or decoded. Never fatal to a run."""


@dataclass(frozen=True, slots=True)
class SourceRequest:
    """What to fetch, and at which snapshot.

    ``blob_sha`` is preferred when known; ``path`` + ``head_sha`` is the
    fallback. Both are carried so the provider can choose without the caller
    needing to know which endpoint exists.
    """

    path: str
    head_sha: str
    blob_sha: str | None = None


class SourceProvider(Protocol):
    """What the analysis pipeline needs in order to read a file."""

    async def get_source(self, request: SourceRequest) -> SourceFile:
        """Return the file's content at the requested snapshot.

        Raises:
            SourceRetrievalError: content is unavailable or undecodable.
        """
        ...


def _safe_repo_path(path: str) -> str:
    """Percent-encode a repository path, refusing anything that escapes it.

    Repository file names are attacker-controlled. This is the only place in
    the analysis layer where one reaches a URL, so traversal is rejected here
    rather than hoped away.
    """
    if not path or path.startswith("/"):
        raise PermanentInputError(f"Refusing absolute repository path: {path!r}")
    parts = path.split("/")
    if any(part in ("..", ".") or not part for part in parts):
        raise PermanentInputError(f"Refusing repository path with traversal: {path!r}")
    return "/".join(quote(part, safe="") for part in parts)


def decode_source(raw: bytes, *, path: str, head_sha: str) -> SourceFile:
    """Decode bytes to text, or fail with a reason.

    UTF-8 strictly first. A decode failure is reported, not papered over with
    ``errors="replace"``: replacement characters would corrupt the exact text
    that anchor validation depends on, and silently. Latin-1 is attempted
    second only because it never fails, and the result is marked as such by the
    caller's status rather than presented as trustworthy UTF-8.
    """
    if len(raw) > MAX_DECODE_BYTES:
        raise SourceRetrievalError(f"Blob exceeds {MAX_DECODE_BYTES} bytes.")
    if b"\x00" in raw[:8192]:
        # A NUL byte in the first 8 KiB is the standard binary heuristic; git
        # uses the same signal. Parsing such a file as source is meaningless.
        raise SourceRetrievalError("Content appears to be binary (NUL byte found).")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceRetrievalError(f"Content is not valid UTF-8: {exc.reason}") from exc
    return SourceFile.from_text(path=path, head_sha=head_sha, text=text, raw=raw)


class GitHubSourceProvider:
    """Retrieves file content from GitHub at an exact commit."""

    def __init__(
        self, client: GitHubClient, *, installation_id: int, owner: str, repo: str
    ) -> None:
        self._client = client
        self._installation_id = installation_id
        self._owner = owner
        self._repo = repo

    async def get_source(self, request: SourceRequest) -> SourceFile:
        try:
            if request.blob_sha:
                raw = await self._fetch_blob(request.blob_sha)
            else:
                raw = await self._fetch_contents(request.path, request.head_sha)
        except SourceRetrievalError:
            raise
        except PermanentInputError:
            raise
        # Deliberately broad: one unreadable file must not kill a whole run.
        except Exception as exc:
            raise SourceRetrievalError(
                f"Failed to retrieve {request.path}: {type(exc).__name__}"
            ) from exc
        return decode_source(raw, path=request.path, head_sha=request.head_sha)

    async def _fetch_blob(self, blob_sha: str) -> bytes:
        if not blob_sha.isalnum():
            raise PermanentInputError(f"Refusing non-hex blob sha: {blob_sha!r}")
        data = await self._client.get_json(
            self._installation_id,
            f"/repos/{self._owner}/{self._repo}/git/blobs/{blob_sha}",
        )
        return _decode_payload(data)

    async def _fetch_contents(self, path: str, ref: str) -> bytes:
        safe = _safe_repo_path(path)
        data = await self._client.get_json(
            self._installation_id,
            f"/repos/{self._owner}/{self._repo}/contents/{safe}?ref={quote(ref, safe='')}",
        )
        if data.get("type") != "file":
            raise SourceRetrievalError(f"{path} is not a regular file.")
        return _decode_payload(data)


def _decode_payload(data: dict[str, object]) -> bytes:
    encoding = data.get("encoding")
    content = data.get("content")
    if encoding == "none" or content is None:
        # GitHub returns encoding "none" for blobs above its inline limit.
        raise SourceRetrievalError("GitHub returned no inline content for this blob.")
    if encoding != "base64" or not isinstance(content, str):
        raise SourceRetrievalError(f"Unexpected content encoding: {encoding!r}")
    try:
        return base64.b64decode(content, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise SourceRetrievalError("Blob content is not valid base64.") from exc


class InMemorySourceProvider:
    """A provider over content supplied directly. No network, no pretending."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files: dict[str, str] = dict(files or {})

    def add(self, path: str, text: str) -> None:
        self._files[path] = text

    async def get_source(self, request: SourceRequest) -> SourceFile:
        if request.path not in self._files:
            raise SourceRetrievalError(f"No content provided for {request.path}.")
        return SourceFile.from_text(
            path=request.path,
            head_sha=request.head_sha,
            text=self._files[request.path],
            raw=self._files[request.path].encode("utf-8"),
        )


__all__ = [
    "GitHubSourceProvider",
    "InMemorySourceProvider",
    "SourceProvider",
    "SourceRequest",
    "SourceRetrievalError",
    "decode_source",
]
