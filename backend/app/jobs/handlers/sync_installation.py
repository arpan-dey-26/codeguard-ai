"""Handlers for ``sync_installation`` and ``sync_repositories`` jobs.

**These did not exist before Phase 5, and their absence was a real defect.**
``app.github.events`` routed ``installation`` and ``installation_repositories``
deliveries to these two job types, ``app.db.models.job`` listed both as valid,
and ``HANDLERS`` contained neither — so every App install, uninstall, suspend,
unsuspend, permission change and repository add/remove was enqueued only to be
marked ``failed`` with "No handler registered". ``mark_uninstalled`` and
``set_suspended`` existed in the repository layer with no caller anywhere.

That was survivable while nothing depended on the installation tables. It stops
being survivable in Phase 5, because ``user_installation_access`` is what
decides who may read what: an installation the App was removed from must stop
granting access, and it only stops if something writes that down.

Both handlers re-read the current state from GitHub rather than trusting the
webhook body. The delivery says *what changed*; the API says *what is true*. A
delivery that arrives out of order — GitHub does not guarantee ordering — would
otherwise leave the database describing a state that no longer holds.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import PermanentAccessError, PermanentInputError
from app.core.logging import get_logger
from app.db.repositories.github_entities import (
    InstallationRepository,
    RepositoryRepository,
)
from app.github.app_auth import GitHubAppAuthenticator
from app.github.client import GitHubClient

logger = get_logger(__name__)

# Actions that mean the App can no longer act for this installation. After one
# of these there is no usable token, so nothing is fetched — the local rows are
# simply marked and the job ends successfully.
_TERMINAL_ACTIONS = {"deleted"}
_SUSPEND_ACTIONS = {"suspend": True, "unsuspend": False}


def _installation_id(payload: dict[str, Any]) -> int:
    installation_id = payload.get("installation_id")
    if not isinstance(installation_id, int):
        raise PermanentInputError("Job payload is missing installation_id.")
    return installation_id


async def handle_sync_installation(
    payload: dict[str, Any],
    session: AsyncSession,
    settings: Settings,
    *,
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    """Reconcile one installation's lifecycle state and repository list."""
    installation_id = _installation_id(payload)
    action = payload.get("action")
    installations = InstallationRepository(session)

    if action in _TERMINAL_ACTIONS:
        await installations.mark_uninstalled(installation_id)
        await session.commit()
        logger.info("job.sync_installation.uninstalled", installation_id=installation_id)
        return {"installation_id": installation_id, "action": action, "repositories": 0}

    if action in _SUSPEND_ACTIONS:
        await installations.set_suspended(installation_id, suspended=_SUSPEND_ACTIONS[str(action)])
        await session.commit()
        logger.info(
            "job.sync_installation.suspension_changed",
            installation_id=installation_id,
            suspended=_SUSPEND_ACTIONS[str(action)],
        )
        # A suspended installation has no working token, so there is nothing
        # further to fetch. Not an error: suspension is a normal state.
        if _SUSPEND_ACTIONS[str(action)]:
            return {
                "installation_id": installation_id,
                "action": action,
                "repositories": 0,
            }

    count = await _refresh_repositories(installation_id, session, settings, client=client)
    logger.info(
        "job.sync_installation.succeeded",
        installation_id=installation_id,
        action=action,
        repositories=count,
    )
    return {"installation_id": installation_id, "action": action, "repositories": count}


async def handle_sync_repositories(
    payload: dict[str, Any],
    session: AsyncSession,
    settings: Settings,
    *,
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    """Reconcile the repository list after an add or remove.

    Deliberately identical in effect to a full refresh rather than applying the
    delta in the payload. Applying a delta requires the delta to be both
    complete and in order, and GitHub guarantees neither.
    """
    installation_id = _installation_id(payload)
    count = await _refresh_repositories(installation_id, session, settings, client=client)
    logger.info(
        "job.sync_repositories.succeeded",
        installation_id=installation_id,
        action=payload.get("action"),
        repositories=count,
    )
    return {
        "installation_id": installation_id,
        "action": payload.get("action"),
        "repositories": count,
    }


async def _refresh_repositories(
    installation_id: int,
    session: AsyncSession,
    settings: Settings,
    *,
    client: GitHubClient | None,
) -> int:
    """Fetch the installation's repositories and upsert them.

    Repositories that have disappeared from the installation are deactivated
    rather than deleted: their pull requests, review runs and findings are
    history, and history should survive a repository being removed from the
    App's scope.
    """
    owns_client = client is None
    if client is None:
        client = GitHubClient(settings, GitHubAppAuthenticator(settings))

    try:
        try:
            repositories = await client.list_installation_repositories(installation_id)
        except PermanentAccessError:
            # The token no longer works: the installation was removed or
            # suspended between the delivery and this job. Record that rather
            # than failing — the outcome is knowledge, not an error.
            await InstallationRepository(session).mark_uninstalled(installation_id)
            await session.commit()
            logger.info("job.sync_installation.access_lost", installation_id=installation_id)
            return 0
    finally:
        if owns_client:
            await client.aclose()

    # Network work is finished; only now is a transaction opened.
    installations = InstallationRepository(session)
    repository_repo = RepositoryRepository(session)

    internal_id = await installations.upsert(
        github_installation_id=installation_id,
        account_login=repositories[0].owner_login if repositories else "",
        account_github_id=(
            repositories[0].owner.id if repositories and repositories[0].owner else None
        ),
        account_type=(
            repositories[0].owner.type
            if repositories and repositories[0].owner and repositories[0].owner.type
            else "Organization"
        ),
    )

    seen: list[int] = []
    for repo in repositories:
        await repository_repo.upsert_from_api(internal_id, repo)
        seen.append(repo.id)

    await repository_repo.deactivate_missing(internal_id, keep_github_repo_ids=seen)
    await session.commit()
    return len(repositories)
