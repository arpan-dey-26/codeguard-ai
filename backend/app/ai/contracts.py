"""Provider-neutral data contracts for AI code review.

These types are the *vocabulary* every AI provider speaks.  They contain no
vendor concepts: no Gemini ``responseSchema``, no Anthropic tool call, no SDK
object.  That is the whole point — the orchestration layer built in Phase 3
depends on these types and never on a vendor.

Two boundaries are fixed here and are worth understanding:

**What goes in.**  A provider receives an already-rendered, already-budgeted
``context_document``.  It does not receive a repository, a clone, a token, or
the ability to fetch anything.  The context is assembled upstream by the diff
and AST layers (Phase 2) under a token budget, and the provider's only job is
to send it and return structured findings.  A provider therefore *cannot*
accidentally widen the blast radius by reading more of the repository.

**What comes out.**  A provider returns ``RawFinding`` objects — the word
*raw* is load-bearing.  These are the model's **claims**, not facts.  File
paths and line numbers in a ``RawFinding`` are unverified and must not be used
to post anything anywhere.  The validation and line-mapping layer (Phase 3,
architecture.md §13) is what turns claims into publishable findings.

Phase 0 defines these shapes and nothing that produces them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FindingCategory(StrEnum):
    """Closed taxonomy from architecture.md §12.1.

    Closed rather than free-text so that analytics grouping stays meaningful
    and a provider cannot invent categories the dashboard cannot render.
    """

    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    RESOURCE = "resource"
    MAINTAINABILITY = "maintainability"


class FindingSide(StrEnum):
    """Which image of the file a finding claims to be about.

    Mirrors ``app.analysis.diff.Side`` and GitHub's own vocabulary, but is
    declared here so the AI contract stays independent of the analysis layer.

    Not in architecture.md §12.1, which predates Phase 2. Added so the model
    *can* claim ``LEFT`` and the validator can **reject** it explicitly — the
    alternative, silently coercing LEFT to RIGHT, is precisely the "no
    LEFT/deletion finding is accidentally published as a RIGHT comment"
    invariant being broken. See ADR-0010.
    """

    LEFT = "LEFT"
    RIGHT = "RIGHT"


class FindingSeverity(StrEnum):
    """Consequence-anchored severity rubric (architecture.md §11.4)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Everything a provider is given, and nothing more.

    ``context_document`` is the rendered review context described in
    architecture.md §11.3: repository and PR metadata, the scoped diff, the
    structural summary and bounded surrounding code, already line-numbered and
    already truncated to fit the token budget.  It is untrusted data and each
    provider is responsible for fencing it away from its own instructions.
    """

    repository_full_name: str
    pull_request_number: int
    head_sha: str
    context_document: str
    max_findings: int = 25


@dataclass(frozen=True, slots=True)
class RawFinding:
    """One unverified claim from the model.

    Mirrors the schema in architecture.md §12.1.  ``anchor_snippet`` is the
    field that makes the claim checkable: a verbatim excerpt the model says
    appears at ``line_start``.  Downstream validation searches for it in the
    real file, and a claim whose anchor cannot be found never becomes an
    inline comment.
    """

    finding_id: str
    category: FindingCategory
    severity: FindingSeverity
    title: str
    description: str
    file_path: str
    line_start: int
    anchor_snippet: str
    confidence: float
    impact: str
    rationale: str
    line_end: int | None = None
    side: FindingSide = FindingSide.RIGHT
    suggested_fix: str | None = None
    cwe_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Token accounting and latency for a single provider call.

    Recorded per review run because it is a historical fact of that call and
    cannot be reconstructed later (architecture.md §16.15).
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """What a provider returns for one review request."""

    model: str
    summary: str
    findings: tuple[RawFinding, ...] = ()
    usage: ProviderUsage = field(default_factory=ProviderUsage)

    raw_claim_count: int = 0
    """How many items the model actually put in its findings array.

    Distinct from ``len(findings)``: a provider drops items it cannot coerce
    into a ``RawFinding`` at all. Without this number the validation funnel
    silently loses those items — "emitted" would already mean "emitted and
    parseable", and the funnel would not add up. A dashboard funnel whose
    stages do not sum is worse than no funnel, because it looks authoritative.
    """

    dropped_claim_count: int = 0
    """Items the provider could not coerce. ``raw_claim_count ==
    dropped_claim_count + len(findings)`` must hold for every provider."""

    @property
    def finding_count(self) -> int:
        return len(self.findings)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What a provider can do, so orchestration can adapt rather than assume.

    Providers differ in how they are made to emit structured output — Gemini
    uses a response schema, Anthropic uses a forced tool call.  That mechanism
    is a provider-internal detail; what the orchestrator needs to know is
    only whether structure can be *enforced* or must be *parsed defensively*.
    """

    supports_structured_output: bool
    supports_system_instructions: bool
    max_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Non-secret provider state, safe to expose on a health endpoint.

    ``configured`` is a boolean *about* a credential, never the credential.
    ``implemented`` is honest about Phase 0: the seam exists, the integration
    does not.
    """

    name: str
    configured: bool
    implemented: bool
    model: str | None = None
