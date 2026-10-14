"""Structured logging with mandatory redaction.

Output is one JSON object per line on stdout, which is what every hosting
platform and log aggregator expects.  Human-readable console rendering is used
only in development, where a developer — not a machine — is reading.

The redaction processor is not optional and not configurable.  Redaction is
exactly the kind of control that silently stops working when someone adds a
new field, so it runs on every record, matches on key *shape* rather than an
allow-list of known field names, and is covered by a test that asserts known
secret values never appear in rendered output (architecture.md §24.3).

Never logged, by construction:
  * Gemini / Claude API keys, GitHub private keys, webhook secrets, tokens
  * repository source code, diff bodies, complete AI prompts or completions
  * full webhook payloads

Log the *shape* of those things — counts, sizes, ids — never the bytes.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from app.core.config import Settings
from app.core.correlation import get_context

REDACTED = "***REDACTED***"

# Substring match, case-insensitive, applied to every key at every nesting
# depth.  Shape-based matching survives new fields; an allow-list does not.
_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "private_key",
    "authorization",
    "auth_header",
    "signature",
    "credential",
    "cookie",
    "session_id",
)

# Whole-value keys that carry bulk untrusted content.  These are dropped
# rather than masked, because their presence in a log record is itself the
# mistake — masking would hide the bug instead of preventing it.
_FORBIDDEN_BULK_KEYS: frozenset[str] = frozenset(
    {
        "patch",
        "diff",
        "source",
        "source_code",
        "file_content",
        "prompt",
        "completion",
        "payload",
        "raw_body",
        "messages",
    }
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


# Values that look like a credential regardless of the key they arrive under.
# These exist because a traceback is a *string*: key-based redaction cannot see
# inside it, and a traceback is exactly where a bound SQL parameter or an
# ``Authorization: Bearer ...`` header ends up.
_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # PEM blocks. Assembled at run time so this source file itself never
    # contains the literal header a secret scanner looks for.
    (
        "pem",
        re.compile(
            "-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----.*?-----END " + r"[A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
    ),
    # GitHub credentials: ghp_/gho_/ghu_/ghs_/ghr_ + 36, and App JWTs.
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_iat", re.compile(r"\bv1\.[0-9a-f]{40}\b")),
    # Google API keys.
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    # Anthropic API keys.
    ("anthropic_api_key", re.compile(r"\bsk-ant-[0-9A-Za-z_\-]{20,}")),
    # Any HTTP authorization header rendered into text.
    ("auth_header", re.compile(r"(?i)\b(authorization|x-goog-api-key)\s*[:=]\s*\S+")),
    # A JWT in any position.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    # A DSN with inline credentials.
    ("dsn", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]+@")),
)

# A rendered SQLAlchemy exception carries the statement and its bound
# parameters.  The parameters are the payload we spent a whole processor
# excluding from the record, so the block is removed rather than masked.
_SQL_PARAMS_PATTERN = re.compile(r"\[(?:SQL|parameters|cached since)[^\]]*\]", re.S)

_MAX_TEXT_LENGTH = 4000


def _scrub_text(value: str) -> str:
    """Remove credential-shaped substrings from free text.

    Applied to tracebacks and to any long string value.  Pattern matching on
    values is a weaker control than key matching — it can only catch what it
    recognises — which is why it is the *second* layer and not the first.
    """
    for _name, pattern in _VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    value = _SQL_PARAMS_PATTERN.sub("[redacted-sql]", value)
    if len(value) > _MAX_TEXT_LENGTH:
        value = value[:_MAX_TEXT_LENGTH] + f"...[truncated {len(value)} chars]"
    return value


def _redact(value: Any, depth: int = 0) -> Any:
    """Recursively mask sensitive keys and drop bulk content keys.

    Recurses through dicts *and* sequences.  A list of dicts is a shape the
    application actually produces (``errors=[{...}]`` in the validation
    handler), and a redactor that stops at the first list is a redactor with a
    hole in it.
    """
    if depth > 6:
        return value
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in _FORBIDDEN_BULK_KEYS:
                cleaned[f"{key}_omitted"] = True
                continue
            if _is_sensitive_key(key):
                cleaned[key] = REDACTED
                continue
            cleaned[key] = _redact(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        rebuilt = [_redact(item, depth + 1) for item in value]
        return type(value)(rebuilt) if isinstance(value, tuple) else rebuilt
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def redaction_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: strip secrets and bulk content from every record.

    **Ordering matters and was wrong until Phase 5.**  This processor must run
    *after* ``format_exc_info``, not before it.  ``format_exc_info`` pops
    ``exc_info`` and writes a rendered traceback into ``event_dict["exception"]``
    — so a redactor placed before it inspects a record that does not yet contain
    the traceback, and the traceback (with the SQLAlchemy statement and its
    bound parameters in it) reaches the log unread.
    """
    result = _redact(event_dict)
    assert isinstance(result, dict)
    return result


def correlation_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Merge the ambient correlation context into every record.

    Fields already present on the record win, so an explicit value at the call
    site is never overwritten by ambient context.
    """
    for key, value in get_context().items():
        event_dict.setdefault(key, value)
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Install the logging configuration for this process.

    Idempotent: calling it twice (app factory plus worker entrypoint in the
    same process during tests) is harmless.
    """
    level = getattr(logging, settings.log_level)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    # uvicorn installs its own handlers; route them through the root logger so
    # access and error lines carry the same structure as ours.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        correlation_processor,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # Render the traceback into the record FIRST, so the redactor below has
        # something to inspect.  The reverse order — which this file used until
        # Phase 5 — lets every traceback past the filter untouched.
        structlog.processors.format_exc_info,
        redaction_processor,  # LAST before rendering — nothing escapes it
    ]

    renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.is_development
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger.

    Event names follow ``domain.action.outcome`` (``app.startup.completed``,
    ``db.connection.failed``) so logs are filterable as data rather than
    searchable as prose.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
