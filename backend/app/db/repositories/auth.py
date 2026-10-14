"""Data access for identity, sessions and installation access.

Raw SQL for the same reason as every other repository in this codebase: the
statements are the security boundary, and a statement written out in full can
be read, reviewed and executed against a real PostgreSQL without an ORM.

The one statement worth reading twice is ``load_session``. It resolves a cookie
to a user **and** applies both expiry rules **and** collects the installation
ids in a single round trip, because splitting it would mean a window in which
the session is valid but the scope is not yet known — and code that handles
that window tends to handle it by falling back to "no restriction".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Everything a request needs to know about its caller.

    ``installation_ids`` is always a tuple, never ``None``. An empty tuple is a
    real and important state — a signed-in user whose App access was removed —
    and it must produce empty lists, not everything. The type makes the "no
    restriction" case unrepresentable here, which is the point: unrestricted
    access is a property of an *unauthenticated development* deployment, and
    it is expressed elsewhere, in ``ViewerScope``.
    """

    session_id: int
    user_id: int
    github_user_id: int
    login: str
    name: str | None
    avatar_url: str | None
    session_token: str
    expires_at: datetime
    last_seen_at: datetime
    installation_ids: tuple[int, ...]
    access_synced_at: datetime | None


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- users -------------------------------------------------------------
    async def upsert_user(
        self,
        *,
        github_user_id: int,
        login: str,
        name: str | None,
        avatar_url: str | None,
        email: str | None,
    ) -> int:
        """Create or refresh the user, returning the internal id.

        Conflict on ``github_user_id`` — never on ``login``. A user who renames
        themselves is the same user; a different user who later takes the freed
        handle is not.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO users (
                    github_user_id, login, name, avatar_url, email,
                    last_login_at, created_at, updated_at
                ) VALUES (
                    :github_user_id, :login, :name, :avatar_url, :email,
                    now(), now(), now()
                )
                ON CONFLICT (github_user_id) DO UPDATE SET
                    login = EXCLUDED.login,
                    name = EXCLUDED.name,
                    avatar_url = EXCLUDED.avatar_url,
                    email = COALESCE(EXCLUDED.email, users.email),
                    last_login_at = now(),
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "github_user_id": github_user_id,
                "login": login[:255],
                "name": name[:255] if name else None,
                "avatar_url": avatar_url[:2000] if avatar_url else None,
                "email": email[:320] if email else None,
            },
        )
        return int(result.scalar_one())

    # -- installation access ------------------------------------------------
    async def replace_installation_access(
        self, user_id: int, *, github_installation_ids: list[int]
    ) -> list[int]:
        """Set this user's access to exactly the installations GitHub named.

        **Replace, never merge.** An installation the user can no longer reach
        must disappear; a merge would leave it behind for ever, and the row
        that grants access would outlive the access itself.

        Only installations already known locally are granted. An installation
        GitHub reports but that we have never received a webhook for has no
        rows to read anyway, and inserting a placeholder would create a foreign
        key to a row the rest of the schema expects to be real.

        Returns the internal installation ids now granted.
        """
        result = await self._session.execute(
            text(
                """
                WITH granted AS (
                    SELECT id
                      FROM installations
                     WHERE github_installation_id = ANY(CAST(:gids AS BIGINT[]))
                       AND uninstalled_at IS NULL
                ),
                removed AS (
                    DELETE FROM user_installation_access
                     WHERE user_id = :user_id
                       AND installation_id NOT IN (SELECT id FROM granted)
                ),
                upserted AS (
                    INSERT INTO user_installation_access (
                        user_id, installation_id, role, last_synced_at
                    )
                    SELECT :user_id, id, 'member', now() FROM granted
                    ON CONFLICT (user_id, installation_id) DO UPDATE SET
                        last_synced_at = now()
                    RETURNING installation_id
                )
                SELECT installation_id FROM upserted
                """
            ),
            {"user_id": user_id, "gids": list(github_installation_ids)},
        )
        ids = sorted(int(row[0]) for row in result.fetchall())
        logger.info(
            "auth.access.synced",
            user_id=user_id,
            reported_by_github=len(github_installation_ids),
            granted_locally=len(ids),
        )
        return ids

    # -- sessions ----------------------------------------------------------
    async def create_session(
        self,
        *,
        user_id: int,
        token_hash: str,
        ttl_seconds: int,
        user_agent: str | None,
        ip_hash: str | None,
    ) -> int:
        result = await self._session.execute(
            text(
                """
                INSERT INTO user_sessions (
                    user_id, token_hash, issued_at, expires_at, last_seen_at,
                    user_agent, ip_hash, created_at, updated_at
                ) VALUES (
                    :user_id, :token_hash, now(),
                    now() + make_interval(secs => :ttl), now(),
                    :user_agent, :ip_hash, now(), now()
                )
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "token_hash": token_hash,
                "ttl": int(ttl_seconds),
                "user_agent": user_agent[:255] if user_agent else None,
                "ip_hash": ip_hash,
            },
        )
        return int(result.scalar_one())

    async def load_session(
        self, *, token_hash: str, idle_timeout_seconds: int
    ) -> SessionContext | None:
        """Resolve a cookie to a caller, or return ``None``.

        ``None`` covers every failure identically — unknown token, revoked,
        expired, idle too long. The caller turns all of them into one 401 with
        no detail, because distinguishing "no such session" from "expired
        session" tells someone probing which of their guesses was once real.

        The installation ids come back from the same statement as the session,
        by design: a second query would leave a window where the caller is
        authenticated but unscoped.
        """
        result = await self._session.execute(
            text(
                """
                SELECT s.id              AS session_id,
                       s.user_id         AS user_id,
                       s.expires_at      AS expires_at,
                       s.last_seen_at    AS last_seen_at,
                       u.github_user_id  AS github_user_id,
                       u.login           AS login,
                       u.name            AS name,
                       u.avatar_url      AS avatar_url,
                       COALESCE(
                           ARRAY(
                               SELECT a.installation_id
                                 FROM user_installation_access a
                                 JOIN installations i ON i.id = a.installation_id
                                WHERE a.user_id = s.user_id
                                  AND i.uninstalled_at IS NULL
                                ORDER BY a.installation_id
                           ),
                           ARRAY[]::BIGINT[]
                       )                 AS installation_ids,
                       (
                           SELECT MAX(a.last_synced_at)
                             FROM user_installation_access a
                            WHERE a.user_id = s.user_id
                       )                 AS access_synced_at
                  FROM user_sessions s
                  JOIN users u ON u.id = s.user_id
                 WHERE s.token_hash = :token_hash
                   AND s.revoked_at IS NULL
                   AND s.expires_at > now()
                   AND s.last_seen_at > now() - make_interval(secs => :idle)
                """
            ),
            {"token_hash": token_hash, "idle": int(idle_timeout_seconds)},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return SessionContext(
            session_id=int(row["session_id"]),
            user_id=int(row["user_id"]),
            github_user_id=int(row["github_user_id"]),
            login=str(row["login"]),
            name=row["name"],
            avatar_url=row["avatar_url"],
            session_token="",  # filled in by the caller, which holds the plaintext
            expires_at=row["expires_at"],
            last_seen_at=row["last_seen_at"],
            installation_ids=tuple(int(i) for i in (row["installation_ids"] or ())),
            access_synced_at=row["access_synced_at"],
        )

    async def touch_session(self, session_id: int) -> None:
        """Advance the idle clock.

        Deliberately does **not** extend ``expires_at``: activity postpones the
        idle timeout, never the absolute one. A session that renewed itself for
        ever would make "sessions last eight hours" untrue for exactly the
        users who matter most — the ones who are always logged in.
        """
        await self._session.execute(
            text(
                """
                UPDATE user_sessions
                   SET last_seen_at = now(), updated_at = now()
                 WHERE id = :id AND revoked_at IS NULL
                """
            ),
            {"id": session_id},
        )

    async def rotate_session_token(self, session_id: int, *, new_token_hash: str) -> None:
        """Swap the token a live session answers to, keeping the session row.

        Called immediately after login completes. Session fixation works by
        getting a victim to adopt a session identifier the attacker already
        knows and then waiting for it to become authenticated; changing the
        identifier at the moment authentication happens breaks that, because
        the value the attacker holds stops resolving.
        """
        await self._session.execute(
            text(
                """
                UPDATE user_sessions
                   SET token_hash = :new_hash, updated_at = now()
                 WHERE id = :id
                """
            ),
            {"id": session_id, "new_hash": new_token_hash},
        )

    async def revoke_session(self, session_id: int, *, reason: str = "logout") -> None:
        await self._session.execute(
            text(
                """
                UPDATE user_sessions
                   SET revoked_at = now(), revoked_reason = :reason, updated_at = now()
                 WHERE id = :id AND revoked_at IS NULL
                """
            ),
            {"id": session_id, "reason": reason[:32]},
        )

    async def revoke_all_sessions(self, user_id: int, *, reason: str = "logout_all") -> int:
        result = await self._session.execute(
            text(
                """
                UPDATE user_sessions
                   SET revoked_at = now(), revoked_reason = :reason, updated_at = now()
                 WHERE user_id = :user_id AND revoked_at IS NULL
                RETURNING id
                """
            ),
            {"user_id": user_id, "reason": reason[:32]},
        )
        return len(result.fetchall())

    async def purge_expired_sessions(self, *, retain_days: int = 30) -> int:
        """Delete long-dead session rows.

        Revoked and expired sessions are kept for a while — they are the audit
        trail for "when did this person sign in?" — and then removed, because
        indefinitely retained records of a person's activity are a liability
        rather than an asset.
        """
        result = await self._session.execute(
            text(
                """
                DELETE FROM user_sessions
                 WHERE (revoked_at IS NOT NULL OR expires_at < now())
                   AND updated_at < now() - make_interval(days => :days)
                RETURNING id
                """
            ),
            {"days": int(retain_days)},
        )
        return len(result.fetchall())

    # -- OAuth state -------------------------------------------------------
    async def create_oauth_state(
        self, *, state_hash: str, redirect_path: str | None, ttl_seconds: int
    ) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO oauth_states (
                    state_hash, redirect_path, created_at, expires_at
                ) VALUES (
                    :state_hash, :redirect_path, now(),
                    now() + make_interval(secs => :ttl)
                )
                """
            ),
            {
                "state_hash": state_hash,
                "redirect_path": redirect_path[:512] if redirect_path else None,
                "ttl": int(ttl_seconds),
            },
        )

    async def consume_oauth_state(self, *, state_hash: str) -> tuple[bool, str | None]:
        """Atomically spend a state value exactly once.

        ``DELETE ... RETURNING`` rather than select-then-delete: the read-then-
        write form is a race two concurrent callbacks can both win, which is
        precisely the replay this table exists to prevent. Deleting is the
        check.

        Returns ``(was_valid, redirect_path)``.
        """
        result = await self._session.execute(
            text(
                """
                DELETE FROM oauth_states
                 WHERE state_hash = :state_hash
                   AND expires_at > now()
                RETURNING redirect_path
                """
            ),
            {"state_hash": state_hash},
        )
        row = result.first()
        if row is None:
            return False, None
        return True, row[0]

    async def purge_expired_oauth_states(self) -> int:
        result = await self._session.execute(
            text("DELETE FROM oauth_states WHERE expires_at < now() RETURNING id")
        )
        return len(result.fetchall())


__all__ = ["AuthRepository", "SessionContext"]
