"""End-to-end pull-request synchronisation, against a real PostgreSQL.

GitHub is served by ``httpx.MockTransport`` — a real client over a fake
transport — so the full path is exercised with **no network, no GitHub App and
no credentials**: token provider → client → sync service → PostgreSQL.

This is the offline half of the Phase 1 demonstration.
"""

from __future__ import annotations

import httpx
import pytest
from app.core.config import AppEnv, Settings
from app.github.client import GitHubClient
from app.services.github_sync_service import GitHubSyncService
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import TEST_DATABASE_URL, requires_database

pytestmark = [pytest.mark.integration, requires_database]

INSTALLATION_ID = 77000001
OWNER, REPO, NUMBER = "acme-demo", "widget-service", 42
HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

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
    "head": {"ref": "feature/session-expiry", "sha": HEAD_SHA},
    "base": {"ref": "main", "sha": "b" * 40},
    "created_at": "2026-09-15T09:12:00Z",
    "updated_at": "2026-09-15T09:12:00Z",
}

FILES_JSON = [
    {
        "filename": "src/auth/session.py",
        "status": "modified",
        "additions": 9,
        "deletions": 3,
        "changes": 12,
        "sha": "c" * 40,
        "patch": "@@ -41,7 +41,9 @@",
    },
    {
        "filename": "src/auth/renamed.py",
        "status": "renamed",
        "previous_filename": "src/auth/old.py",
        "additions": 0,
        "deletions": 0,
        "changes": 0,
        "sha": "d" * 40,
        "patch": "@@ -1 +1 @@",
    },
    {
        "filename": "src/auth/deleted.py",
        "status": "removed",
        "additions": 0,
        "deletions": 20,
        "changes": 20,
        "sha": "e" * 40,
    },
    {
        # Binary: GitHub omits the patch entirely.
        "filename": "assets/logo.png",
        "status": "modified",
        "additions": 0,
        "deletions": 0,
        "changes": 0,
        "sha": "f" * 40,
    },
]


class StubTokenProvider:
    async def get_installation_token(self, installation_id: int) -> str:
        return f"ghs_STUB_{installation_id}"


def _handler(pr_json: dict | None = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/repos/{OWNER}/{REPO}"):
            return httpx.Response(200, json=REPO_JSON)
        if path.endswith(f"/pulls/{NUMBER}/files"):
            return httpx.Response(200, json=FILES_JSON)
        if path.endswith(f"/pulls/{NUMBER}"):
            return httpx.Response(200, json=pr_json or PR_JSON)
        return httpx.Response(404, json={"message": "Not Found"})

    return handler


def _settings() -> Settings:
    return Settings(app_env=AppEnv.TEST, database_url=TEST_DATABASE_URL, log_level="WARNING")


@pytest.fixture
async def engine():  # type: ignore[no-untyped-def]
    eng = create_async_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):  # type: ignore[no-untyped-def]
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean(engine):  # type: ignore[no-untyped-def]
    async def wipe() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM changed_files"))
            await conn.execute(text("DELETE FROM pull_requests"))
            await conn.execute(text("DELETE FROM repositories"))
            await conn.execute(text("DELETE FROM installations"))

    await wipe()
    yield
    await wipe()


async def _sync(session_factory, *, pr_json=None, expected_head_sha=None):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(pr_json))) as http:
        client = GitHubClient(_settings(), StubTokenProvider(), client=http)
        async with session_factory() as session:
            result = await GitHubSyncService(session, client).sync_pull_request(
                installation_id=INSTALLATION_ID,
                repository_full_name=f"{OWNER}/{REPO}",
                pull_request_number=NUMBER,
                expected_head_sha=expected_head_sha,
            )
            await session.commit()
            return result


# -- the Phase 1 demonstration -------------------------------------------


async def test_a_pull_request_is_fully_synchronised(session_factory) -> None:  # type: ignore[no-untyped-def]
    result = await _sync(session_factory)

    assert result.changed_files == 4
    assert result.truncated is False
    assert result.head_sha == HEAD_SHA

    async with session_factory() as session:
        install = (
            (
                await session.execute(
                    text(
                        "SELECT account_login, account_type FROM installations "
                        "WHERE github_installation_id = :i"
                    ),
                    {"i": INSTALLATION_ID},
                )
            )
            .mappings()
            .one()
        )
        repo = (
            (
                await session.execute(
                    text(
                        "SELECT full_name, default_branch, primary_language, is_private "
                        "FROM repositories WHERE github_repo_id = 550001"
                    )
                )
            )
            .mappings()
            .one()
        )
        pr = (
            (
                await session.execute(
                    text(
                        "SELECT number, title, state, head_sha, base_ref, author_login, "
                        "changed_files_count, last_synced_sha FROM pull_requests "
                        "WHERE github_pr_id = 1700000042"
                    )
                )
            )
            .mappings()
            .one()
        )

    assert install["account_login"] == OWNER
    assert install["account_type"] == "Organization"
    assert repo["full_name"] == f"{OWNER}/{REPO}"
    assert repo["primary_language"] == "Python"
    assert pr["number"] == NUMBER and pr["state"] == "open"
    assert pr["head_sha"] == HEAD_SHA
    assert pr["author_login"] == "dev-alice"
    assert pr["changed_files_count"] == 4
    assert pr["last_synced_sha"] == HEAD_SHA


async def test_file_shapes_are_classified_correctly(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Renames, deletions and binaries all land with the right metadata.

    Phase 2 consumes these flags; getting them wrong here would be invisible
    until then.
    """
    await _sync(session_factory)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT file_path, change_status, previous_path, has_patch "
                        "FROM changed_files ORDER BY file_path"
                    )
                )
            )
            .mappings()
            .all()
        )

    by_path = {r["file_path"]: r for r in rows}
    assert by_path["src/auth/renamed.py"]["previous_path"] == "src/auth/old.py"
    assert by_path["src/auth/deleted.py"]["change_status"] == "removed"
    # No patch for a binary: it can never receive an inline comment.
    assert by_path["assets/logo.png"]["has_patch"] is False
    assert by_path["src/auth/session.py"]["has_patch"] is True


async def test_no_patch_text_is_stored(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Metadata about the diff, never the diff — it is third-party source."""
    await _sync(session_factory)
    async with session_factory() as session:
        columns = {
            r[0]
            for r in (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'changed_files'"
                    )
                )
            ).fetchall()
        }
    assert "patch" not in columns
    assert "content" not in columns
    assert "has_patch" in columns


# -- idempotency ----------------------------------------------------------


async def test_re_syncing_updates_rather_than_duplicating(session_factory) -> None:  # type: ignore[no-untyped-def]
    await _sync(session_factory)
    await _sync(session_factory)

    async with session_factory() as session:
        counts = {
            table: int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
            for table in ("installations", "repositories", "pull_requests", "changed_files")
        }
    assert counts == {
        "installations": 1,
        "repositories": 1,
        "pull_requests": 1,
        "changed_files": 4,
    }


async def test_a_pull_request_update_moves_the_head_sha(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A new push updates the row; it does not create a second one."""
    await _sync(session_factory)
    new_sha = "9" * 40
    updated = {**PR_JSON, "head": {"ref": "feature/session-expiry", "sha": new_sha}}
    await _sync(session_factory, pr_json=updated)

    async with session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT head_sha FROM pull_requests WHERE number = :n"), {"n": NUMBER}
            )
        ).fetchall()
        file_shas = {
            r[0]
            for r in (
                await session.execute(text("SELECT DISTINCT head_sha FROM changed_files"))
            ).fetchall()
        }

    assert len(rows) == 1 and rows[0][0] == new_sha
    # Keyed on the commit, so both commits' file sets coexist.
    assert file_shas == {HEAD_SHA, new_sha}


async def test_a_superseded_head_sha_aborts_the_sync(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A newer push landed while this job was queued or running."""
    result = await _sync(session_factory, expected_head_sha="0" * 40)
    assert result.superseded is True
    assert result.changed_files == 0

    async with session_factory() as session:
        count = int(
            (await session.execute(text("SELECT count(*) FROM changed_files"))).scalar_one()
        )
    assert count == 0


async def test_a_merged_pull_request_state_is_collapsed(session_factory) -> None:  # type: ignore[no-untyped-def]
    merged = {
        **PR_JSON,
        "state": "closed",
        "merged": True,
        "merged_at": "2026-09-15T12:00:00Z",
    }
    await _sync(session_factory, pr_json=merged)
    async with session_factory() as session:
        state = (
            await session.execute(
                text("SELECT state FROM pull_requests WHERE number = :n"), {"n": NUMBER}
            )
        ).scalar_one()
    assert state == "merged"
