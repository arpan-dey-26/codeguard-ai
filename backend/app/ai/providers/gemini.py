"""Google Gemini provider.

Implements ``AIReviewProvider`` over the Generative Language REST API using
``httpx`` — the project's existing HTTP stack. No vendor SDK is installed:
one request shape and one response shape do not justify a second HTTP stack,
a second retry policy and a second set of exception types (ADR-0004).

Everything Gemini-specific stops here. The orchestrator, the validator, the
persistence layer and the GitHub publisher all speak ``RawFinding`` and have
no idea which vendor produced it.

Three properties worth calling out:

*   **Structured output is enforced, not requested.** ``responseMimeType`` and
    ``responseSchema`` make the model emit JSON matching our schema, so there
    is no prose to regex and no fenced code block to strip.
*   **Errors are classified by status and payload shape, never by string
    matching on a message.** Vendor messages change; status codes and error
    `status` fields are contractual.
*   **The model gets no tools.** It returns data. It cannot call GitHub, read
    a file, run a command or fetch a URL, so however it behaves, it cannot
    act.

The API key is passed in the ``x-goog-api-key`` header rather than as a query
parameter, so it cannot end up in a URL that is logged, put in an exception
message, or recorded in a proxy's access log.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.ai.contracts import (
    FindingCategory,
    FindingSeverity,
    FindingSide,
    ProviderCapabilities,
    ProviderUsage,
    RawFinding,
    ReviewRequest,
    ReviewResult,
)
from app.ai.prompt import (
    FINDING_RESPONSE_SCHEMA,
    build_system_prompt,
    wrap_untrusted_context,
)
from app.ai.provider import BaseAIReviewProvider
from app.core.config import AIProviderName
from app.core.errors import (
    ConfigurationError,
    PermanentInputError,
    ThrottledError,
    TransientError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_API_VERSION = "v1beta"


class GeminiResponseError(PermanentInputError):
    """The response was received but could not be turned into findings.

    Permanent by classification: the same request would produce the same
    unparseable shape, and burning the retry budget on it only delays the
    signal that something is wrong with the prompt or the schema.
    """

    problem_type = "ai-response-invalid"
    title = "AI response could not be parsed"


class GeminiQuotaExceededError(ThrottledError):
    """Free-tier quota exhausted. Retryable, but only after a real delay."""

    problem_type = "ai-quota-exceeded"
    title = "AI quota exceeded"


class GeminiProvider(BaseAIReviewProvider):
    """Initial AI provider for CodeGuard AI."""

    def __init__(self, settings: Any, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings)
        self._client = client
        self._owns_client = client is None

    # -- identity ----------------------------------------------------------
    @property
    def name(self) -> AIProviderName:
        return AIProviderName.GEMINI

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_structured_output=True,
            supports_system_instructions=True,
            max_input_tokens=None,
        )

    @property
    def model(self) -> str | None:
        return self._settings.gemini_model

    def _has_credentials(self) -> bool:
        return self._settings.gemini_api_key is not None

    @property
    def is_implemented(self) -> bool:
        return True

    # -- review ------------------------------------------------------------
    async def review_code(self, request: ReviewRequest) -> ReviewResult:
        self.ensure_configured()

        body = self._build_request_body(request)
        started = time.perf_counter()
        payload = await self._call(body)
        latency_ms = int((time.perf_counter() - started) * 1000)

        result = self._parse_response(payload, latency_ms=latency_ms, request=request)
        logger.info(
            "ai.review.completed",
            provider="gemini",
            model=self.model,
            # Counts and durations only. Never the prompt, never the raw
            # response: both contain repository source by construction.
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=latency_ms,
            findings=result.finding_count,
            truncated=result.usage.truncated,
        )
        return result

    def _build_request_body(self, request: ReviewRequest) -> dict[str, Any]:
        wrapped, _nonce = wrap_untrusted_context(request.context_document)
        schema = json.loads(json.dumps(FINDING_RESPONSE_SCHEMA))
        # Honour the caller's cap rather than the schema's static default, so a
        # small PR cannot be answered with twenty-five findings.
        schema["properties"]["findings"]["maxItems"] = request.max_findings

        return {
            # Instructions live in their own channel. Repository content never
            # joins them — that separation is the first injection defence.
            "system_instruction": {"parts": [{"text": build_system_prompt()}]},
            "contents": [{"role": "user", "parts": [{"text": wrapped}]}],
            "generationConfig": {
                "temperature": self._settings.gemini_temperature,
                "maxOutputTokens": self._settings.gemini_max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(schema),
            },
        }

    async def _call(self, body: dict[str, Any]) -> dict[str, Any]:
        api_key = self._settings.gemini_api_key
        if api_key is None:  # pragma: no cover — ensure_configured covers this
            raise ConfigurationError("GEMINI_API_KEY is not set.")

        base = self._settings.gemini_api_base_url.rstrip("/")
        url = f"{base}/{_API_VERSION}/models/{self.model}:generateContent"
        headers = {
            # Header, not a query parameter: a key in a URL leaks into logs,
            # exception messages and proxy access logs.
            "x-goog-api-key": api_key.get_secret_value(),
            "Content-Type": "application/json",
        }

        client = self._client
        owns = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(self._settings.gemini_timeout_seconds))
        try:
            response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise TransientError("Gemini request timed out.") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"Network failure calling Gemini: {type(exc).__name__}") from exc
        finally:
            if owns:
                await client.aclose()

        self._raise_for_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise GeminiResponseError("Gemini returned a non-JSON body.") from exc
        if not isinstance(data, dict):
            raise GeminiResponseError("Gemini returned a non-object body.")
        return data

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Classify by status code and error payload — never by message text."""
        status = response.status_code
        if 200 <= status < 300:
            return

        detail = _error_status(response)

        if status in (401, 403):
            # An API-key problem is permanent: retrying never populates a key.
            raise ConfigurationError(
                "Gemini rejected the API key. Check GEMINI_API_KEY and that the "
                "Generative Language API is enabled for the project."
            )
        if status == 429:
            raise GeminiQuotaExceededError(
                "Gemini quota or rate limit reached.",
                retry_after=_retry_after_seconds(response),
            )
        if status == 400:
            # Includes an invalid request and an unusable schema. Ours to fix.
            raise PermanentInputError(f"Gemini rejected the request ({detail or 'bad request'}).")
        if status == 404:
            raise ConfigurationError(
                f"Gemini model {self.model!r} was not found. Check GEMINI_MODEL."
            )
        if status in (500, 502, 503, 504) or status >= 500:
            raise TransientError(f"Gemini returned {status}.")
        raise PermanentInputError(f"Gemini returned an unexpected status {status}.")

    def _parse_response(
        self, payload: dict[str, Any], *, latency_ms: int, request: ReviewRequest
    ) -> ReviewResult:
        prompt_feedback = payload.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            # A safety filter blocked the prompt. Permanent for this input.
            raise GeminiResponseError(
                f"Gemini blocked the request ({prompt_feedback.get('blockReason')})."
            )

        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiResponseError("Gemini returned no candidates.")
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise GeminiResponseError("Gemini returned a malformed candidate.")

        finish_reason = str(candidate.get("finishReason") or "")
        truncated = finish_reason == "MAX_TOKENS"
        if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
            raise GeminiResponseError(f"Gemini stopped early ({finish_reason}).")

        text = _extract_text(candidate)
        if not text:
            raise GeminiResponseError("Gemini returned an empty response body.")

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # With responseSchema set this should not happen; if it does, the
            # response is unusable and retrying the same prompt will not help.
            raise GeminiResponseError(f"Gemini output was not valid JSON ({exc.msg}).") from exc
        if not isinstance(parsed, dict):
            raise GeminiResponseError("Gemini output was not a JSON object.")

        usage = payload.get("usageMetadata") or {}
        findings, received, dropped = _coerce_findings(parsed.get("findings"), request.max_findings)
        return ReviewResult(
            model=str(self.model),
            summary=str(parsed.get("review_summary") or "")[:1200],
            findings=findings,
            raw_claim_count=received,
            dropped_claim_count=dropped,
            usage=ProviderUsage(
                input_tokens=_maybe_int(usage.get("promptTokenCount")),
                output_tokens=_maybe_int(usage.get("candidatesTokenCount")),
                latency_ms=latency_ms,
                truncated=truncated,
            ),
        )


# -- response coercion ------------------------------------------------------


def _coerce_findings(raw: Any, limit: int) -> tuple[tuple[RawFinding, ...], int, int]:
    """Turn the model's array into ``RawFinding`` objects.

    Per-item isolation: an item that cannot be coerced is dropped with a
    counted warning rather than failing the whole response. One malformed
    finding must not discard the other seven.

    Note what this does **not** do: it performs no semantic validation, checks
    no path, verifies no line. Coercion produces *claims*. Turning a claim into
    something publishable is the validator's job, and keeping the two apart is
    what stops "it parsed" from being mistaken for "it is true".

    Returns ``(findings, received, dropped)``. The counts are returned rather
    than only logged because the review run stores a validation funnel, and a
    funnel that omits the items lost here does not add up.
    """
    if not isinstance(raw, list):
        return ((), 0, 0)

    findings: list[RawFinding] = []
    dropped = 0
    for item in raw[: max(0, limit)]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            findings.append(_coerce_one(item))
        except (ValueError, TypeError, KeyError):
            dropped += 1
    if dropped:
        logger.warning("ai.response.items_dropped", count=dropped)
    return (tuple(findings), len(raw), dropped)


def _coerce_one(item: dict[str, Any]) -> RawFinding:
    category = FindingCategory(str(item["category"]).strip().lower())
    severity = FindingSeverity(str(item["severity"]).strip().lower())
    side_raw = str(item.get("side") or "RIGHT").strip().upper()
    side = FindingSide(side_raw)  # raises for anything else — dropped by caller

    line_start = int(item["line_start"])
    line_end_raw = item.get("line_end")
    line_end = int(line_end_raw) if line_end_raw is not None else None

    confidence = float(item.get("confidence", 0.0))
    # Clamped rather than rejected: a model that says 1.4 means "very sure",
    # and the threshold logic downstream needs a number in range.
    confidence = min(1.0, max(0.0, confidence))

    return RawFinding(
        finding_id=str(item.get("finding_id") or "")[:16],
        category=category,
        severity=severity,
        title=str(item["title"])[:120],
        description=str(item["description"])[:1200],
        file_path=str(item["file_path"])[:400],
        line_start=line_start,
        line_end=line_end,
        side=side,
        anchor_snippet=str(item["anchor_snippet"])[:300],
        confidence=confidence,
        impact=str(item.get("impact") or "")[:400],
        rationale=str(item.get("rationale") or "")[:800],
        suggested_fix=(str(item["suggested_fix"])[:1200] if item.get("suggested_fix") else None),
        cwe_id=(str(item["cwe_id"])[:12] if item.get("cwe_id") else None),
    )


def _extract_text(candidate: dict[str, Any]) -> str:
    content = candidate.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and "text" in p)


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate a JSON-Schema fragment into Gemini's schema dialect.

    Gemini accepts a subset of OpenAPI 3.0 schema fields — ``enum``, ``items``,
    ``maxItems``, ``nullable``, ``properties``, ``required`` — with upper-case
    type names. Unsupported fields are ignored by the model rather than
    rejected, but they are stripped here anyway so the request carries only
    what has an effect.

    **``maxItems`` is deliberately kept**, because it is supported and is the
    one constraint that changes behaviour: it is how the caller's
    ``max_findings`` reaches the model at all. Stripping it would leave the cap
    enforced only after the fact by the validator, which means paying for
    output we then discard.

    ``maxLength`` is dropped, so field-length limits are *not* enforced by the
    model. That is fine and is why every string is truncated again in
    ``ValidatedFinding`` — model-side limits were never the guarantee.

    The translation lives here because it is a vendor detail; the schema itself
    stays provider-neutral in ``app.ai.prompt``.
    """
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("maxLength", "minLength", "minItems"):
            continue
        if key == "type" and isinstance(value, str):
            out["type"] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            out["properties"] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out["items"] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


def _error_status(response: httpx.Response) -> str:
    """Google's machine-readable error ``status``, never its prose message."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("status") or "")
    return ""


def _retry_after_seconds(response: httpx.Response) -> int | None:
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(0, int(header))
        except ValueError:
            pass
    return None


def _maybe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["GeminiProvider", "GeminiQuotaExceededError", "GeminiResponseError"]
