# ADR-0001 — Introduce an `AIReviewProvider` abstraction; Gemini is the initial provider

- **Status:** Accepted
- **Date:** 2026-09-15
- **Phase:** 0
- **Amends:** `architecture.md` §6.5, §11, §19 (Appendix A)

## Context

The approved baseline names the Anthropic Claude API as *the* AI provider and
specifies a concrete `ClaudeClient` in `app/ai/client.py`, coupled to
Anthropic's forced-tool-call mechanism for structured output.

For the initial implementation and academic demonstration the project will use
the **Google Gemini API** instead, primarily because of its free-tier
availability. Claude remains desirable as a future option, and neither vendor
should be assumed permanent for the life of the project.

Coupling the review orchestration layer to either vendor would mean that
changing provider — a configuration-level concern — requires editing the
pipeline, its tests and its error handling.

## Decision

Introduce an `AIReviewProvider` seam between the review orchestration layer
and any vendor:

```
                    AIReviewProvider            (Protocol — the contract)
                           │
             ┌─────────────┴─────────────┐
             │                           │
      GeminiProvider               ClaudeProvider
   (initial provider)          (future, optional)
```

- `AIReviewProvider` is a `typing.Protocol`. Orchestration depends on the
  protocol and never on a concrete class.
- `BaseAIReviewProvider` is an ABC supplying credential checking and status
  reporting that every provider needs identically.
- `app/ai/registry.py` maps `AI_PROVIDER` to a class. Adding a provider is one
  enum member and one registry entry.
- `app/ai/contracts.py` holds vendor-neutral types (`ReviewRequest`,
  `RawFinding`, `ReviewResult`, `ProviderUsage`, `ProviderCapabilities`).

The single most important consequence: **the structured-output mechanism lives
inside each provider, not in the orchestrator.** Gemini enforces shape with a
response schema; Anthropic enforces it with a forced tool call. Both are
implementation details of producing a `RawFinding`. The finding schema from
`architecture.md` §12 is unchanged and stays provider-neutral, which is what
keeps the §13 validation and line-mapping layer working identically whichever
vendor is selected.

## Consequences

**Positive**

- Provider is a configuration change, not a code change.
- Keeping two providers in the registry from day one proves the seam is real.
  An abstraction validated by a single implementation is not validated.
- Orchestration can be tested with a structurally-typed stub — no vendor SDK,
  no mock framework, no network, no API key.
- `architecture.md` §13 (the trust plane) is entirely unaffected: it validates
  `RawFinding` objects and does not care where they came from.

**Negative / accepted costs**

- One layer of indirection that a single-vendor design would not need. Judged
  worth it because the vendor has already changed once before any code existed.
- Provider-specific tuning (prompt caching, `temperature=0` semantics, token
  limits) cannot live in shared code. `ProviderCapabilities` exists so
  orchestration can adapt rather than assume.

## Alternatives rejected

- **Hard-code Gemini.** Fastest now. Rejected: the vendor changed once already,
  and the assignment explicitly requires vendor independence.
- **Adopt a general LLM orchestration framework.** Rejected: the pipeline *is*
  the contribution of this project; a framework would hide exactly the part
  under assessment, and add a large dependency for one API call.
- **Swap providers with an `if` statement at the call site.** Rejected: that
  puts vendor knowledge in orchestration, which is the thing being avoided.
