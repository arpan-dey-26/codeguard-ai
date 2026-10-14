"""Identity: who is using the dashboard, and what they are allowed to see.

Three tables, and the separation between them is the design.

``users``
    A person, keyed on GitHub's **numeric** id. GitHub logins can be changed
    and, once released, reused by somebody else — so a login is a display
    string here and never a key. That is not a hypothetical: handle recycling
    is exactly how an account-takeover-by-rename works in systems that key on
    the name.

``user_sessions``
    A live browser session. **Server-side, not a self-contained token.** A
    signed JWT in a cookie cannot be revoked without building the very
    server-side list this table already is, so the extra round trip buys real
    logout, real idle timeout and a real "sign out everywhere". The cookie
    carries an opaque random value; the table stores only its SHA-256, so a
    database read — a dump, a backup, a log of a slow query — does not yield a
    usable session.

``user_installation_access``
    Which GitHub App installations this person may read. Populated at login by
    asking GitHub, refreshed on a TTL (architecture.md §16.3). **This table is
    the authorization boundary for the entire dashboard**: every dashboard
    query is scoped to the installation ids found here for the session's user,
    and the caller never supplies an installation id themselves (NFR-S8).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ACCESS_ROLES = ("admin", "member")

SESSION_REVOKED_REASONS = (
    "logout",
    "logout_all",
    "rotated",
    "access_revoked",
    "expired",
    "admin",
)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    """GitHub's immutable numeric id. The only stable identity GitHub offers."""

    login: Mapped[str] = mapped_column(String(255), nullable=False)
    """Current GitHub handle. Display only — mutable, therefore never a key."""

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    """Only present if the user's GitHub profile exposes one.

    The ``user:email`` scope is deliberately **not** requested: the application
    sends no email, so asking for the address would be collecting personal data
    with no purpose, and a consent screen asking for it invites a reasonable
    "why?" with no good answer.
    """

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSession(Base, TimestampMixin):
    """One browser session.

    Not defined in architecture.md §16, which stops at ``users`` and
    ``user_installation_access``. It is added here because §17.4 requires
    ``POST /auth/logout`` to *invalidate* a session, and invalidation is not
    possible without server-side state. The alternative — a stateless signed
    token — would make logout a lie: the token would keep working until it
    expired on its own.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_at IS NOT NULL",
            name="revoked_reason_requires_revoked_at",
        ),
        # Sweeping expired sessions, and "sign out everywhere".
        Index("ix_user_sessions_user_id_expires_at", "user_id", "expires_at"),
        Index(
            "ix_user_sessions_expires_at_active",
            "expires_at",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    """SHA-256 of the opaque cookie value, hex encoded.

    The plaintext is generated once, put in the cookie, and never stored. A
    plain SHA-256 rather than a password hash is correct *here specifically*:
    the input is 256 bits of ``secrets.token_urlsafe`` entropy, so there is no
    dictionary to attack and a slow KDF would only add latency to every
    request. That reasoning does **not** transfer to passwords.
    """

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """Absolute expiry. Never extended — a session has a maximum life."""

    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """Drives the idle timeout, which is separate from absolute expiry."""

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Truncated, for the user's own "your sessions" view.

    Recorded but **not** used as an authentication factor: pinning a session to
    a user agent locks out anyone whose browser updates mid-session, and does
    not stop an attacker who can copy the cookie from also copying the header.
    """

    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """SHA-256 of the client IP, keyed by the session secret.

    Hashed rather than stored: the operational question is "did this session
    move?", which a hash answers, and storing raw addresses is personal data
    retained for no additional benefit.
    """


class UserInstallationAccess(Base):
    """Which installations a user may read. The authorization boundary.

    A cache of GitHub's answer, refreshed on a TTL. The trade-off is stated in
    architecture.md §16.3 and is deliberate: checking GitHub on every request
    would add a network round trip to every page load and burn rate limit, so
    revocation takes effect within one TTL rather than instantly. ``last_synced_at``
    is what makes that bound observable rather than assumed.
    """

    __tablename__ = "user_installation_access"
    __table_args__ = (
        CheckConstraint("role IN ('admin','member')", name="role_valid"),
        Index("ix_user_installation_access_installation_id", "installation_id"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    installation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("installations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthState(Base):
    """One in-flight OAuth authorization.

    Server-side rather than only a cookie, for one reason: the ``state``
    parameter must be **single-use**. A cookie-only scheme cannot detect replay
    — the same signed value verifies every time it is presented — so an
    authorization code captured from a redirect could be resubmitted. A row
    that is deleted on consumption makes the second attempt fail.

    Rows are short-lived and swept; the table is never large.
    """

    __tablename__ = "oauth_states"
    __table_args__ = (Index("ix_oauth_states_expires_at", "expires_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    """SHA-256 of the state value. Same reasoning as ``token_hash``."""

    pkce_verifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    """Reserved. GitHub's OAuth App flow does not currently support PKCE; the
    column exists so enabling it later is a code change, not a migration."""

    redirect_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """Where in the SPA to land after login.

    A **path**, never a URL, and validated as one on the way in: accepting a
    full URL here is an open-redirect vulnerability, and it is the single most
    common flaw in a login flow that supports "return to where I was".
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
