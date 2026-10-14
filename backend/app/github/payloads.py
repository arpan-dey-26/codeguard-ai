"""Narrow models for the webhook payloads we consume.

GitHub's pull-request payload has well over two hundred fields. Modelling all
of them would couple us to GitHub's release schedule for no benefit, so these
models declare **only the fields the application reads** and ignore the rest.
Unknown fields are dropped, not trusted, not stored.

Everything here is untrusted, attacker-influenceable input: titles, branch
names, logins and repository names all come from a third party. The models'
job is to answer "is this the shape we expect?" — length caps and escaping
belong at the points of use, and nothing in this module is ever executed,
interpolated into SQL or a path, or treated as an instruction.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    # `extra="ignore"` is the load-bearing setting: GitHub adds fields
    # regularly, and a payload that gains one must not start failing.
    model_config = ConfigDict(extra="ignore", frozen=True)


class AccountRef(_Base):
    """A user or organisation."""

    id: int | None = None
    login: str = ""
    type: str | None = None


class InstallationRef(_Base):
    id: int
    account: AccountRef | None = None
    repository_selection: str | None = None
    permissions: dict[str, str] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    suspended_at: datetime | None = None


class RepositoryRef(_Base):
    id: int
    name: str
    full_name: str
    private: bool = False
    default_branch: str = "main"
    language: str | None = None
    owner: AccountRef | None = None

    @property
    def owner_login(self) -> str:
        """Owner login, preferring the nested owner and falling back to the path.

        ``full_name`` is always ``owner/name``, so the fallback is reliable
        even on the sparser payload shapes where ``owner`` is absent.
        """
        if self.owner and self.owner.login:
            return self.owner.login
        return self.full_name.split("/", 1)[0]


class BranchRef(_Base):
    ref: str = ""
    sha: str = ""


class PullRequestRef(_Base):
    id: int
    number: int
    state: str = "open"
    title: str = ""
    draft: bool = False
    html_url: str = ""
    user: AccountRef | None = None
    head: BranchRef = Field(default_factory=BranchRef)
    base: BranchRef = Field(default_factory=BranchRef)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    merged: bool = False

    @property
    def effective_state(self) -> str:
        """Collapse GitHub's ``state`` + ``merged`` into our three-value state.

        GitHub reports a merged PR as ``state: "closed"`` with ``merged: true``.
        Storing that faithfully would make "how many PRs were merged?" a
        two-column question forever.
        """
        if self.merged or self.merged_at is not None:
            return "merged"
        return "closed" if self.state == "closed" else "open"


class PullRequestEvent(_Base):
    """The subset of a ``pull_request`` delivery the application uses."""

    action: str
    number: int | None = None
    pull_request: PullRequestRef
    repository: RepositoryRef
    installation: InstallationRef | None = None
    sender: AccountRef | None = None


class InstallationEvent(_Base):
    action: str
    installation: InstallationRef
    repositories: list[RepositoryRef] = Field(default_factory=list)


class InstallationRepositoriesEvent(_Base):
    action: str
    installation: InstallationRef
    repositories_added: list[RepositoryRef] = Field(default_factory=list)
    repositories_removed: list[RepositoryRef] = Field(default_factory=list)


class EnvelopeRef(_Base):
    """A first, permissive pass over any delivery.

    Parsed before the event-specific model so that the identifiers needed for
    the audit row (installation, repository, PR number) are available even for
    an event we ignore or cannot fully parse. Every field is optional: this
    model must never be the reason a delivery is rejected.
    """

    action: str | None = None
    installation: InstallationRef | None = None
    repository: RepositoryRef | None = None
    number: int | None = None
    pull_request: PullRequestRef | None = None

    @property
    def pull_request_number(self) -> int | None:
        if self.pull_request is not None:
            return self.pull_request.number
        return self.number
