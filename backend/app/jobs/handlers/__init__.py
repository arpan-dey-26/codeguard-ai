"""Job handlers, one per job type.

A handler receives a decoded payload and a session, does its work, and either
returns or raises. It never decides its own retry policy: that is the runner's
job, driven by the exception class the handler raises
(architecture.md §22.1).
"""

from app.jobs.handlers.review_pull_request import handle_review_pull_request
from app.jobs.handlers.sync_installation import (
    handle_sync_installation,
    handle_sync_repositories,
)
from app.jobs.handlers.sync_pull_request import handle_sync_pull_request

HANDLERS = {
    "sync_pull_request": handle_sync_pull_request,
    "review_pull_request": handle_review_pull_request,
    # Added in Phase 5. ``app.github.events`` routed to both of these from
    # Phase 1 and neither had a handler, so every installation lifecycle event
    # failed permanently. A test now asserts that every job type the router can
    # produce has an entry here — see tests/unit/test_job_handler_coverage.py.
    "sync_installation": handle_sync_installation,
    "sync_repositories": handle_sync_repositories,
}

__all__ = [
    "HANDLERS",
    "handle_review_pull_request",
    "handle_sync_installation",
    "handle_sync_pull_request",
    "handle_sync_repositories",
]
