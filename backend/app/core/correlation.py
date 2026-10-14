"""Correlation-ID propagation.

A single identifier follows one unit of work — an HTTP request now, a queue
job from Phase 1 — through every log line it produces.  It is stored in a
``ContextVar`` rather than threaded through function signatures, because a
parameter that every function must remember to pass is a parameter that some
function will eventually forget (architecture.md §24.1).

Later phases add ``delivery_id``, ``job_id``, ``review_run_id``,
``repository_id`` and ``pull_request_number`` to the same context.  The
``bind_context`` helper already accepts them so that nothing needs
restructuring when those arrive.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# ``None``, not ``{}``. A ContextVar's default is evaluated **once**, at import,
# and every context that never calls ``set`` sees that same object — so a
# mutable default is process-wide shared state wearing context-local clothing.
#
# Today's readers happen to copy before mutating, so nothing leaks. That is a
# property of the current call sites, not of the design, and the next person to
# write ``get_context()["job_id"] = ...`` would silently bind that value into
# every request the process ever serves. Defaulting to ``None`` makes the
# mistake impossible rather than merely absent.
_extra_context: ContextVar[dict[str, Any] | None] = ContextVar("extra_context", default=None)

CORRELATION_HEADER = "X-Request-ID"


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str) -> Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


def bind_context(**fields: Any) -> Token[dict[str, Any] | None]:
    """Attach additional identifiers to the current logging context.

    Values that are ``None`` are dropped, so a caller can pass optional
    identifiers unconditionally without polluting log output.
    """
    current = dict(_extra_context.get() or {})
    current.update({k: v for k, v in fields.items() if v is not None})
    return _extra_context.set(current)


def reset_context(token: Token[dict[str, Any] | None]) -> None:
    _extra_context.reset(token)


@contextmanager
def scoped_context(**fields: Any) -> Iterator[None]:
    """Bind identifiers for the duration of one unit of work, then unbind.

    ``bind_context`` alone accumulates: it merges into the existing dict and
    the caller must reset the token. A long-lived loop (the worker) that binds
    without resetting carries one job's identifiers into the next job's log
    lines whenever the next payload happens not to overwrite them — so a job
    with no repository in its payload inherits the previous job's repository.
    Wrong identifiers on a log line are worse than none, because they are
    believed.

    This context manager makes the reset structural rather than remembered.
    """
    token = bind_context(**fields)
    try:
        yield
    finally:
        reset_context(token)


def get_context() -> dict[str, Any]:
    ctx: dict[str, Any] = dict(_extra_context.get() or {})
    cid = _correlation_id.get()
    if cid is not None:
        ctx["correlation_id"] = cid
    return ctx
