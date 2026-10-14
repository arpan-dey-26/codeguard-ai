"""Typed models for the GitHub REST responses the application reads.

Separate from ``payloads.py``: webhook payloads and REST responses are
different shapes from different GitHub surfaces, and conflating them means one
model that is wrong about both. As with payloads, only the fields the
application uses are declared, and unknown fields are ignored so a GitHub
release cannot break parsing.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class GitHubAccount(_Base):
    id: int | None = None
    login: str = ""
    type: str | None = None


class GitHubRepository(_Base):
    id: int
    name: str
    full_name: str
    private: bool = False
    default_branch: str = "main"
    language: str | None = None
    owner: GitHubAccount | None = None

    @property
    def owner_login(self) -> str:
        if self.owner and self.owner.login:
            return self.owner.login
        return self.full_name.split("/", 1)[0]


class GitHubBranch(_Base):
    ref: str = ""
    sha: str = ""


class GitHubPullRequest(_Base):
    id: int
    number: int
    state: str = "open"
    title: str = ""
    draft: bool = False
    html_url: str = ""
    user: GitHubAccount | None = None
    head: GitHubBranch = Field(default_factory=GitHubBranch)
    base: GitHubBranch = Field(default_factory=GitHubBranch)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    merged: bool = False
    changed_files: int | None = None

    @property
    def effective_state(self) -> str:
        if self.merged or self.merged_at is not None:
            return "merged"
        return "closed" if self.state == "closed" else "open"


class GitHubChangedFile(_Base):
    """One entry from ``GET /repos/{owner}/{repo}/pulls/{n}/files``.

    ``patch`` is **optional and frequently absent** — GitHub omits it for
    binary files and for diffs that are too large. That absence is a
    classification signal, not an error.

    Phase 1 records only whether a patch existed (``has_patch``); the patch
    text itself is neither stored nor parsed. Diff parsing is Phase 2.
    """

    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    sha: str | None = None
    previous_filename: str | None = None
    patch: str | None = None

    @property
    def has_patch(self) -> bool:
        return self.patch is not None


class GitHubInstallation(_Base):
    id: int
    account: GitHubAccount | None = None
    repository_selection: str | None = None
    permissions: dict[str, str] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    suspended_at: datetime | None = None


class RateLimitSnapshot(_Base):
    """What the rate-limit headers said on the last response.

    Recorded so that budget exhaustion is an early warning rather than a
    sudden wall of 403s (architecture.md §24.4).
    """

    limit: int | None = None
    remaining: int | None = None
    reset_epoch: int | None = None
    used: int | None = None
