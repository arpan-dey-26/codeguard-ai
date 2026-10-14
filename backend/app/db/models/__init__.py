"""ORM models.

Every model is imported here so that Alembic's autogenerate sees the full
metadata through ``app.db.base.Base.metadata``. A model that is defined but
not imported is invisible to migrations — a silent and very confusing failure.

Phase 1 introduced the GitHub ingress entities; Phase 3 added the review
entities (``review_runs``, ``findings``, ``review_comments``,
``finding_events``); Phase 5 adds identity (``users``, ``user_sessions``,
``user_installation_access``, ``oauth_states``). Still to come:
``processing_logs``, per architecture.md §16.
"""

from app.db.models.changed_file import ChangedFile
from app.db.models.finding import Finding
from app.db.models.finding_event import FindingEvent
from app.db.models.installation import Installation
from app.db.models.job import Job
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review_comment import ReviewComment
from app.db.models.review_run import ReviewRun
from app.db.models.user import (
    OAuthState,
    User,
    UserInstallationAccess,
    UserSession,
)
from app.db.models.webhook_event import WebhookEvent

__all__ = [
    "ChangedFile",
    "Finding",
    "FindingEvent",
    "Installation",
    "Job",
    "OAuthState",
    "PullRequest",
    "Repository",
    "ReviewComment",
    "ReviewRun",
    "User",
    "UserInstallationAccess",
    "UserSession",
    "WebhookEvent",
]
