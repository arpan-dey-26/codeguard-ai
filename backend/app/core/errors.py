"""Error hierarchy.

**No web framework is imported here, deliberately.**  The worker process
raises, catches and classifies these exceptions constantly and has no HTTP
surface at all; making it import FastAPI to raise a ``TransientError`` would
be a dependency inversion with no justification.  HTTP rendering of these
errors lives in ``app.api.problem``.

The point of the hierarchy is that the *class* — not a string, not a status
code — tells the caller what to do next.  The worker's retry decision is a
single ``isinstance`` check (architecture.md §22.1):

    Transient        → retry with exponential backoff and jitter
    Throttled        → retry after the server-directed delay
    PermanentInput   → fail fast; retrying cannot change the outcome
    PermanentAccess  → fail fast; deactivate the affected entity
    Configuration    → fail fast; a human must intervene
    anything else    → a bug; fail, log with a traceback, alert
"""

from __future__ import annotations

# Status codes are plain integers rather than framework constants so that this
# module stays importable from a process with no web framework installed.
_HTTP_BAD_REQUEST = 400
_HTTP_FORBIDDEN = 403
_HTTP_UNPROCESSABLE = 422
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_INTERNAL = 500
_HTTP_NOT_IMPLEMENTED = 501
_HTTP_UNAVAILABLE = 503


class CodeGuardError(Exception):
    """Base class for every error this application raises deliberately."""

    problem_type: str = "internal-error"
    title: str = "Internal error"
    status_code: int = _HTTP_INTERNAL
    retryable: bool = False

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail


class TransientError(CodeGuardError):
    """Would probably succeed if retried (5xx, timeout, connection reset)."""

    problem_type = "transient-error"
    title = "Temporary failure"
    status_code = _HTTP_UNAVAILABLE
    retryable = True


class ThrottledError(CodeGuardError):
    """Explicitly told to slow down.  Carries the server-directed delay.

    Distinct from ``TransientError`` because the delay is *not* ours to choose:
    ignoring a server's ``Retry-After`` and applying our own backoff is how a
    client earns a longer ban.
    """

    problem_type = "throttled"
    title = "Rate limited"
    status_code = _HTTP_TOO_MANY_REQUESTS
    retryable = True

    def __init__(self, detail: str | None = None, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.retry_after = retry_after


class AmbiguousWriteError(CodeGuardError):
    """A write failed in a way that leaves its effect unknown.

    A POST that times out, or whose connection drops mid-flight, may already
    have been applied on the server. Retrying it blindly duplicates whatever it
    created — for us, GitHub review comments.

    Deliberately **not** retryable. The caller must reconcile against server
    state (read back what exists) before trying again. That is a different
    operation from a retry, and conflating the two is how duplicate comments
    appear on a pull request.
    """

    problem_type = "ambiguous-write"
    title = "Write outcome unknown"
    status_code = _HTTP_UNAVAILABLE
    retryable = False


class PermanentInputError(CodeGuardError):
    """The input is invalid; retrying changes nothing."""

    problem_type = "invalid-input"
    title = "Invalid input"
    status_code = _HTTP_UNPROCESSABLE


class GitHubValidationError(PermanentInputError):
    """GitHub rejected the request body as invalid (HTTP 422).

    A distinct class because the publisher treats it differently from every
    other permanent input error. Creating a review is **atomic**: one inline
    comment GitHub considers unpositionable rejects the entire request, and the
    response does not say which one. So a 422 is not "give up" — it is the
    signal to retry with a smaller, safer set of comments and, failing that, to
    post the findings as a summary instead of losing them.

    Making that a subclass rather than a status-code check at the call site
    keeps the publisher free of HTTP knowledge, which is the whole point of the
    hierarchy.
    """

    problem_type = "github-validation-failed"
    title = "GitHub rejected the request"


class PermanentAccessError(CodeGuardError):
    """We are no longer permitted to perform this operation.

    Raised on a 404 or 401 from GitHub for a resource we previously could
    reach: the App was uninstalled, its permissions were reduced, or the
    repository was deleted or made private.  Never retried — and the affected
    installation or repository is marked inactive.
    """

    problem_type = "access-lost"
    title = "Access unavailable"
    status_code = _HTTP_FORBIDDEN


class ConfigurationError(CodeGuardError):
    """The deployment is misconfigured; a human must intervene.

    A missing GitHub private key or AI API key is permanent by definition: no
    amount of retrying populates an environment variable.
    """

    problem_type = "configuration-error"
    title = "Service not configured"
    status_code = _HTTP_UNAVAILABLE


class ProviderNotImplementedError(CodeGuardError):
    """A provider exists as a seam but its integration is not written yet."""

    problem_type = "provider-not-implemented"
    title = "AI provider not implemented"
    status_code = _HTTP_NOT_IMPLEMENTED


class ProviderBudgetExceededError(CodeGuardError):
    """One review tried to call the AI provider more times than it may.

    Not retryable: a retry would make the same extra call. The ceiling is
    ``AI_MAX_REQUESTS_PER_REVIEW``, which ``docs/deployment.md`` describes to
    operators as a free-tier guard — and which, until Phase 6, nothing read.
    A documented safety limit that no code enforces is worse than no limit,
    because an operator who sets it believes they are protected.
    """

    problem_type = "provider-budget-exceeded"
    title = "AI request budget exhausted for this review"
    status_code = _HTTP_UNAVAILABLE


# -- Webhook-specific errors -------------------------------------------------


class WebhookSignatureError(CodeGuardError):
    """The delivery is not authentically from GitHub.

    A security event, not a validation error: the body failed HMAC
    verification, was unsigned, or carried a malformed signature header.  It is
    rendered as 401 with no detail whatsoever — telling an attacker *why* the
    signature failed is free information.
    """

    problem_type = "invalid-signature"
    title = "Invalid webhook signature"
    status_code = 401


class WebhookPayloadError(PermanentInputError):
    """The body is authentic but is not a payload we can process.

    Authenticity and validity are separate questions: a valid signature proves
    GitHub sent the bytes, not that the bytes are a shape we understand
    (architecture.md §8.2, step 7).
    """

    problem_type = "invalid-payload"
    title = "Invalid webhook payload"


class PayloadTooLargeError(CodeGuardError):
    """The request body exceeds the configured ceiling."""

    problem_type = "payload-too-large"
    title = "Payload too large"
    status_code = 413


class ResponseTooLargeError(PermanentInputError):
    """An *upstream* response exceeded the ceiling we are willing to buffer.

    A subclass of ``PermanentInputError`` because the size of a blob does not
    change between attempts: retrying would download the same oversized body
    again. Distinct from ``PayloadTooLargeError``, which is about a request
    *we* received.
    """

    problem_type = "upstream-response-too-large"
    title = "Upstream response too large"


class AuthenticationRequiredError(CodeGuardError):
    """No valid session accompanies this request.

    401, and deliberately not 403: the caller has presented no identity at all,
    so the correct advice is "authenticate", not "you lack permission".
    """

    problem_type = "authentication-required"
    title = "Authentication required"
    status_code = 401


class CsrfValidationError(CodeGuardError):
    """A state-changing request failed its cross-site request forgery check.

    403 rather than 401: the session is valid. What failed is the proof that
    *this* request was issued by our own frontend rather than by another
    origin that happens to be able to make the browser send the cookie.
    """

    problem_type = "csrf-validation-failed"
    title = "Request origin could not be verified"
    status_code = _HTTP_FORBIDDEN


class RateLimitedError(ThrottledError):
    """The caller has exceeded a request budget we enforce.

    A subclass of ``ThrottledError`` so it inherits the ``Retry-After``
    rendering and the retryable classification already wired through the
    problem-details handler.
    """

    problem_type = "rate-limited"
    title = "Too many requests"


__all__ = [
    "_HTTP_BAD_REQUEST",
    "AmbiguousWriteError",
    "AuthenticationRequiredError",
    "CodeGuardError",
    "ConfigurationError",
    "CsrfValidationError",
    "GitHubValidationError",
    "PayloadTooLargeError",
    "PermanentAccessError",
    "PermanentInputError",
    "ProviderBudgetExceededError",
    "ProviderNotImplementedError",
    "RateLimitedError",
    "ResponseTooLargeError",
    "ThrottledError",
    "TransientError",
    "WebhookPayloadError",
    "WebhookSignatureError",
]
