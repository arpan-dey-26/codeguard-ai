"""Upserts for the GitHub domain entities.

Every operation here is an idempotent upsert keyed on a GitHub natural key
(``github_installation_id``, ``github_repo_id``, ``github_pr_id``,
``(pull_request_id, head_sha, file_path)``). That is what makes
synchronisation safe to repeat: a redelivered webhook, a manual re-sync and a
retried job all converge on the same rows rather than accumulating duplicates.

``ON CONFLICT ... DO UPDATE`` is used rather than "select, then insert or
update" for the same reason as in the webhook ledger: the read-then-write form
is a race that two concurrent workers can both lose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.github.models import GitHubChangedFile, GitHubPullRequest, GitHubRepository
from app.github.payloads import InstallationRef

logger = get_logger(__name__)

# Title is capped on write. It is untrusted third-party text and there is no
# reason to store an unbounded string.
_MAX_TITLE = 1000


class InstallationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        github_installation_id: int,
        account_login: str,
        account_github_id: int | None,
        account_type: str,
        repository_selection: str | None = None,
        permissions: dict[str, str] | None = None,
        events: list[str] | None = None,
        suspended_at: datetime | None = None,
    ) -> int:
        """Create or update an installation, returning its internal id.

        ``account_type`` defaults defensively to ``Organization`` only when
        GitHub gave us nothing; a GitHub App can be installed on a personal
        account, and assuming otherwise is a common wrong simplification.
        """
        normalised_type = (
            account_type if account_type in ("User", "Organization") else "Organization"
        )
        result = await self._session.execute(
            text(
                """
                INSERT INTO installations (
                    github_installation_id, account_login, account_github_id,
                    account_type, repository_selection, permissions, events,
                    suspended_at, uninstalled_at, created_at, updated_at
                ) VALUES (
                    :gid, :login, :account_github_id, :account_type,
                    :repository_selection,
                    CAST(:permissions AS jsonb), CAST(:events AS jsonb),
                    :suspended_at, NULL, now(), now()
                )
                ON CONFLICT (github_installation_id) DO UPDATE SET
                    account_login = EXCLUDED.account_login,
                    account_github_id = COALESCE(
                        EXCLUDED.account_github_id, installations.account_github_id
                    ),
                    account_type = EXCLUDED.account_type,
                    repository_selection = COALESCE(
                        EXCLUDED.repository_selection, installations.repository_selection
                    ),
                    permissions = EXCLUDED.permissions,
                    events = EXCLUDED.events,
                    suspended_at = EXCLUDED.suspended_at,
                    -- A new event for an installation proves it exists again.
                    uninstalled_at = NULL,
                    updated_at = now()
                RETURNING id;
                """
            ),
            {
                "gid": github_installation_id,
                "login": account_login[:255],
                "account_github_id": account_github_id,
                "account_type": normalised_type,
                "repository_selection": repository_selection,
                "permissions": _json(permissions or {}),
                "events": _json(events or []),
                "suspended_at": suspended_at,
            },
        )
        return int(result.scalar_one())

    async def upsert_from_payload(self, ref: InstallationRef) -> int:
        account = ref.account
        return await self.upsert(
            github_installation_id=ref.id,
            account_login=account.login if account else "",
            account_github_id=account.id if account else None,
            account_type=(account.type if account and account.type else "Organization"),
            repository_selection=ref.repository_selection,
            permissions=ref.permissions,
            events=ref.events,
            suspended_at=ref.suspended_at,
        )

    async def mark_uninstalled(self, github_installation_id: int) -> None:
        """Soft delete, so that history survives an uninstall."""
        await self._session.execute(
            text(
                """
                UPDATE installations
                   SET uninstalled_at = now(), updated_at = now()
                 WHERE github_installation_id = :gid
                """
            ),
            {"gid": github_installation_id},
        )

    async def set_suspended(self, github_installation_id: int, *, suspended: bool) -> None:
        await self._session.execute(
            text(
                """
                UPDATE installations
                   SET suspended_at = CASE WHEN :suspended THEN now() ELSE NULL END,
                       updated_at = now()
                 WHERE github_installation_id = :gid
                """
            ),
            {"gid": github_installation_id, "suspended": suspended},
        )


class RepositoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        installation_id: int,
        github_repo_id: int,
        owner_login: str,
        name: str,
        full_name: str,
        default_branch: str,
        is_private: bool,
        primary_language: str | None,
    ) -> int:
        """Create or update a repository, returning its internal id.

        Conflict resolution is on ``github_repo_id``, not ``full_name``:
        repositories get renamed and transferred, and the numeric id is the
        only stable identity. The name is updated to follow.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO repositories (
                    installation_id, github_repo_id, owner_login, name, full_name,
                    default_branch, is_private, primary_language, is_active,
                    settings, removed_at, created_at, updated_at
                ) VALUES (
                    :installation_id, :github_repo_id, :owner_login, :name, :full_name,
                    :default_branch, :is_private, :primary_language, TRUE,
                    '{}'::jsonb, NULL, now(), now()
                )
                ON CONFLICT (github_repo_id) DO UPDATE SET
                    installation_id = EXCLUDED.installation_id,
                    owner_login = EXCLUDED.owner_login,
                    name = EXCLUDED.name,
                    full_name = EXCLUDED.full_name,
                    default_branch = EXCLUDED.default_branch,
                    is_private = EXCLUDED.is_private,
                    primary_language = EXCLUDED.primary_language,
                    removed_at = NULL,
                    updated_at = now()
                RETURNING id;
                """
            ),
            {
                "installation_id": installation_id,
                "github_repo_id": github_repo_id,
                "owner_login": owner_login[:255],
                "name": name[:255],
                "full_name": full_name[:512],
                "default_branch": default_branch[:255],
                "is_private": is_private,
                "primary_language": primary_language[:64] if primary_language else None,
            },
        )
        return int(result.scalar_one())

    async def upsert_from_api(self, installation_id: int, repo: GitHubRepository) -> int:
        return await self.upsert(
            installation_id=installation_id,
            github_repo_id=repo.id,
            owner_login=repo.owner_login,
            name=repo.name,
            full_name=repo.full_name,
            default_branch=repo.default_branch,
            is_private=repo.private,
            primary_language=repo.language,
        )

    async def deactivate_missing(
        self, installation_id: int, *, keep_github_repo_ids: list[int]
    ) -> list[int]:
        """Mark repositories no longer covered by the installation as inactive.

        **Deactivated, never deleted.** Their pull requests, review runs and
        findings are history; removing a repository from the App's scope is not
        a reason to lose it. ``is_active = false`` is what the dashboard and the
        Phase 5 access checks read, so history stays visible to a user who still
        has access to the installation while no new work is scheduled for it.

        Returns the internal ids that were deactivated, so the caller can log a
        count rather than guess one.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE repositories
                   SET is_active = FALSE, removed_at = now(), updated_at = now()
                 WHERE installation_id = :installation_id
                   AND is_active = TRUE
                   AND NOT (github_repo_id = ANY(CAST(:keep AS BIGINT[])))
                RETURNING id;
                """
            ),
            # An empty list is a real case — an installation with every
            # repository removed — and `= ANY('{}')` is false for every row,
            # which deactivates all of them. That is the correct answer.
            {"installation_id": installation_id, "keep": list(keep_github_repo_ids)},
        )
        ids = [int(row[0]) for row in result.fetchall()]
        if ids:
            logger.info(
                "repositories.deactivated",
                installation_id=installation_id,
                count=len(ids),
            )
        return ids


class PullRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, *, repository_id: int, pr: GitHubPullRequest) -> int:
        """Create or update a pull request, returning its internal id.

        A PR event **updates the existing row**; a new push moves ``head_sha``
        rather than creating a second row. The ``(repository_id, number)``
        unique constraint is what guarantees that even under concurrent
        deliveries.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO pull_requests (
                    repository_id, github_pr_id, number, title,
                    author_login, author_github_id, state, is_draft,
                    base_ref, head_ref, base_sha, head_sha, html_url,
                    changed_files_count, changed_files_truncated,
                    opened_at, closed_at, merged_at, github_updated_at,
                    created_at, updated_at
                ) VALUES (
                    :repository_id, :github_pr_id, :number, :title,
                    :author_login, :author_github_id, :state, :is_draft,
                    :base_ref, :head_ref, :base_sha, :head_sha, :html_url,
                    0, FALSE,
                    :opened_at, :closed_at, :merged_at, :github_updated_at,
                    now(), now()
                )
                ON CONFLICT (repository_id, number) DO UPDATE SET
                    github_pr_id = EXCLUDED.github_pr_id,
                    title = EXCLUDED.title,
                    author_login = EXCLUDED.author_login,
                    author_github_id = COALESCE(
                        EXCLUDED.author_github_id, pull_requests.author_github_id
                    ),
                    state = EXCLUDED.state,
                    is_draft = EXCLUDED.is_draft,
                    base_ref = EXCLUDED.base_ref,
                    head_ref = EXCLUDED.head_ref,
                    base_sha = EXCLUDED.base_sha,
                    head_sha = EXCLUDED.head_sha,
                    html_url = EXCLUDED.html_url,
                    closed_at = EXCLUDED.closed_at,
                    merged_at = EXCLUDED.merged_at,
                    github_updated_at = EXCLUDED.github_updated_at,
                    updated_at = now()
                RETURNING id;
                """
            ),
            {
                "repository_id": repository_id,
                "github_pr_id": pr.id,
                "number": pr.number,
                "title": pr.title[:_MAX_TITLE],
                "author_login": (pr.user.login if pr.user else "")[:255],
                "author_github_id": pr.user.id if pr.user else None,
                "state": pr.effective_state,
                "is_draft": pr.draft,
                "base_ref": pr.base.ref[:255],
                "head_ref": pr.head.ref[:255],
                "base_sha": pr.base.sha[:40],
                "head_sha": pr.head.sha[:40],
                "html_url": pr.html_url,
                "opened_at": pr.created_at,
                "closed_at": pr.closed_at,
                "merged_at": pr.merged_at,
                "github_updated_at": pr.updated_at,
            },
        )
        return int(result.scalar_one())

    async def record_sync(
        self,
        *,
        pull_request_id: int,
        head_sha: str,
        changed_files_count: int,
        truncated: bool,
    ) -> None:
        await self._session.execute(
            text(
                """
                UPDATE pull_requests
                   SET changed_files_count = :count,
                       changed_files_truncated = :truncated,
                       last_synced_at = now(),
                       last_synced_sha = :head_sha,
                       updated_at = now()
                 WHERE id = :id
                """
            ),
            {
                "id": pull_request_id,
                "count": changed_files_count,
                "truncated": truncated,
                "head_sha": head_sha[:40],
            },
        )


class ChangedFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for_commit(
        self,
        *,
        pull_request_id: int,
        head_sha: str,
        files: list[GitHubChangedFile],
    ) -> int:
        """Store the changed-file set for one commit, replacing any prior set.

        Delete-then-insert within the caller's transaction, rather than a
        merge: the file list for a given commit is immutable, so if we are
        writing it again the previous attempt was incomplete and a partial old
        set must not survive. The unique constraint on
        ``(pull_request_id, head_sha, file_path)`` makes a duplicate
        impossible either way.

        No patch text is stored — only whether one existed.
        """
        await self._session.execute(
            text(
                """
                DELETE FROM changed_files
                 WHERE pull_request_id = :pr_id AND head_sha = :head_sha
                """
            ),
            {"pr_id": pull_request_id, "head_sha": head_sha[:40]},
        )
        if not files:
            return 0

        rows = [
            {
                "pull_request_id": pull_request_id,
                "head_sha": head_sha[:40],
                "file_path": f.filename,
                "previous_path": f.previous_filename,
                "change_status": f.status if f.status in _VALID_STATUSES else "modified",
                "additions": f.additions,
                "deletions": f.deletions,
                "changes": f.changes,
                "has_patch": f.has_patch,
                "blob_sha": (f.sha or None),
            }
            for f in files
        ]
        await self._session.execute(
            text(
                """
                INSERT INTO changed_files (
                    pull_request_id, head_sha, file_path, previous_path,
                    change_status, additions, deletions, changes, has_patch,
                    blob_sha, created_at, updated_at
                ) VALUES (
                    :pull_request_id, :head_sha, :file_path, :previous_path,
                    :change_status, :additions, :deletions, :changes, :has_patch,
                    :blob_sha, now(), now()
                )
                ON CONFLICT (pull_request_id, head_sha, file_path) DO NOTHING
                """
            ),
            rows,
        )
        return len(rows)


_VALID_STATUSES = frozenset(
    {"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"}
)


def _json(value: Any) -> str:
    import json

    return json.dumps(value)
