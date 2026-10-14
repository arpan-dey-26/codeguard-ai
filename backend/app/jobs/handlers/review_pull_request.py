"""Handler for ``review_pull_request`` jobs.

Thin, like every handler: decode the payload, build the collaborators,
delegate. It raises rather than swallowing, because the exception *class* is
what tells the runner whether to retry (architecture.md §22.1).

Two decisions specific to this handler:

**It re-syncs before reviewing.** The job payload carries the head SHA the
webhook reported, but a job can sit in the queue behind a debounce and a
backlog. Re-reading the pull request means the review is anchored to what
GitHub says is current *now*, not to what was current when the event arrived.

**The AI provider is constructed here, not at import.** A missing API key is a
``ConfigurationError`` raised at the moment a review is actually attempted —
which is what lets the whole test suite, the API process and CI run with no
credentials at all (ADR-0002).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import build_provider
from app.core.config import Settings
from app.core.errors import PermanentInputError
from app.core.logging import get_logger
from app.github.app_auth import GitHubAppAuthenticator
from app.github.client import GitHubClient
from app.review.service import ReviewService
from app.services.github_sync_service import GitHubSyncService

logger = get_logger(__name__)


async def handle_review_pull_request(
    payload: dict[str, Any],
    session: AsyncSession,
    settings: Settings,
    *,
    client: GitHubClient | None = None,
    provider: Any | None = None,
) -> dict[str, Any]:
    """Review one pull request end to end.

    ``client`` and ``provider`` are injectable so the handler can be exercised
    against a fake transport and a fake model, with no network, no credentials
    and no cost.
    """
    installation_id = payload.get("installation_id")
    repository_full_name = payload.get("repository_full_name")
    pull_request_number = payload.get("pull_request_number")

    if not isinstance(installation_id, int) or not isinstance(pull_request_number, int):
        raise PermanentInputError("Job payload is missing installation_id or PR number.")
    if not isinstance(repository_full_name, str) or "/" not in repository_full_name:
        raise PermanentInputError("Job payload is missing a valid repository_full_name.")

    trigger = payload.get("trigger")
    webhook_event_id = payload.get("webhook_event_id")
    expected_head_sha = payload.get("head_sha")

    owns_client = client is None
    if client is None:
        client = GitHubClient(settings, GitHubAppAuthenticator(settings))

    try:
        sync = GitHubSyncService(session, client)
        result = await sync.sync_pull_request(
            installation_id=installation_id,
            repository_full_name=repository_full_name,
            pull_request_number=pull_request_number,
            expected_head_sha=expected_head_sha if isinstance(expected_head_sha, str) else None,
        )
        await session.commit()

        if result.superseded:
            logger.info(
                "job.review_pull_request.superseded",
                repository=repository_full_name,
                pull_request_number=pull_request_number,
            )
            return {"superseded": True, "published": 0}

        if result.analysis is None:
            # Phase 2 could not produce a structural analysis. Reviewing
            # without one would mean anchoring findings against nothing, so the
            # review does not run. The sync itself succeeded and its rows stand.
            logger.warning(
                "job.review_pull_request.no_analysis",
                repository=repository_full_name,
                pull_request_number=pull_request_number,
            )
            return {"skipped": "no_analysis", "published": 0}

        ids = await _entity_ids(session, repository_full_name, pull_request_number)
        if ids is None:
            raise PermanentInputError("Pull request row is missing after a successful sync.")
        repository_id, pull_request_id = ids

        active_provider = provider if provider is not None else build_provider(settings)

        service = ReviewService(
            session,
            client=client,
            provider=active_provider,
            settings=settings,
        )
        outcome = await service.review_pull_request(
            installation_id=installation_id,
            repository_id=repository_id,
            repository_full_name=repository_full_name,
            pull_request_id=pull_request_id,
            pull_request_number=pull_request_number,
            head_sha=result.head_sha,
            analysis=result.analysis,
            trigger=trigger if isinstance(trigger, str) else "manual",
            webhook_event_id=webhook_event_id if isinstance(webhook_event_id, int) else None,
            changed_file_ids=await _changed_file_ids(session, pull_request_id, result.head_sha),
        )
    finally:
        if owns_client:
            await client.aclose()

    logger.info("job.review_pull_request.finished", **outcome.summary())
    return outcome.summary()


async def _entity_ids(
    session: AsyncSession, repository_full_name: str, number: int
) -> tuple[int, int] | None:
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            SELECT r.id AS repository_id, p.id AS pull_request_id
            FROM pull_requests p
            JOIN repositories r ON r.id = p.repository_id
            WHERE r.full_name = :full_name AND p.number = :number
            """
        ),
        {"full_name": repository_full_name, "number": number},
    )
    row = result.first()
    return (int(row[0]), int(row[1])) if row else None


async def _changed_file_ids(
    session: AsyncSession, pull_request_id: int, head_sha: str
) -> dict[str, int]:
    """``file_path -> changed_files.id`` for this commit.

    Lets a finding reference the exact changed-file row it belongs to, which is
    what makes the dashboard's per-file drill-down a join rather than a string
    match on a path.
    """
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            SELECT file_path, id FROM changed_files
            WHERE pull_request_id = :pr_id AND head_sha = :head_sha
            """
        ),
        {"pr_id": pull_request_id, "head_sha": head_sha},
    )
    return {row[0]: int(row[1]) for row in result.all()}
