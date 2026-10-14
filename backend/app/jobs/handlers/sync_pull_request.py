"""Handler for ``sync_pull_request`` jobs.

Thin by design: decode the payload, build the collaborators, delegate to the
service. Handlers that grow logic become untestable, because testing them then
requires the whole worker.

It raises rather than swallowing. The exception *class* is what tells the
runner whether to retry (architecture.md §22.1), so a handler that catches
everything and returns quietly destroys the retry classification.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import PermanentInputError
from app.core.logging import get_logger
from app.github.app_auth import GitHubAppAuthenticator
from app.github.client import GitHubClient
from app.services.github_sync_service import GitHubSyncService

logger = get_logger(__name__)


async def handle_sync_pull_request(
    payload: dict[str, Any],
    session: AsyncSession,
    settings: Settings,
    *,
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    """Synchronise one pull request's metadata from GitHub.

    ``client`` is injectable so the handler can be exercised against a fake
    transport with no network and no credentials.
    """
    installation_id = payload.get("installation_id")
    repository_full_name = payload.get("repository_full_name")
    pull_request_number = payload.get("pull_request_number")

    if not isinstance(installation_id, int) or not isinstance(pull_request_number, int):
        raise PermanentInputError("Job payload is missing installation_id or PR number.")
    if not isinstance(repository_full_name, str) or "/" not in repository_full_name:
        raise PermanentInputError("Job payload is missing a valid repository_full_name.")

    head_sha = payload.get("head_sha")
    owns_client = client is None
    if client is None:
        authenticator = GitHubAppAuthenticator(settings)
        client = GitHubClient(settings, authenticator)

    try:
        service = GitHubSyncService(session, client)
        result = await service.sync_pull_request(
            installation_id=installation_id,
            repository_full_name=repository_full_name,
            pull_request_number=pull_request_number,
            expected_head_sha=head_sha if isinstance(head_sha, str) else None,
        )
    finally:
        if owns_client:
            await client.aclose()

    if result.superseded:
        logger.info(
            "job.sync_pull_request.superseded",
            repository=repository_full_name,
            pull_request_number=pull_request_number,
        )
        return {"superseded": True, "changed_files": 0}

    # The Phase 1 demonstration line.
    logger.info(
        "job.sync_pull_request.succeeded",
        repository=repository_full_name,
        pull_request_number=pull_request_number,
        head_sha=result.head_sha[:12],
        changed_files=result.changed_files,
        truncated=result.truncated,
    )
    return {
        "repository": repository_full_name,
        "pull_request_number": pull_request_number,
        "head_sha": result.head_sha,
        "changed_files": result.changed_files,
        "truncated": result.truncated,
    }
