# ADR-0002 — AI credentials are validated at provider use, not at settings load

- **Status:** Accepted
- **Date:** 2026-09-15
- **Phase:** 0
- **Refines:** `architecture.md` §21.4

## Context

`architecture.md` §21.4 requires configuration to fail fast: "a missing secret
crashes the process immediately rather than failing at 3 a.m. on the first
webhook."

Phase 0 also requires that the test suite and CI run with **no API key of any
kind**, and that the application start and report its own status without one.
Applied naively to AI credentials, fail-fast makes both impossible.

## Decision

Split the rule by what the secret is for.

- **Infrastructure secrets** (`DATABASE_URL`) are validated when `Settings` is
  constructed. Without them nothing the application does can work.
- **AI provider credentials** are *not* required to construct `Settings` or to
  construct a provider. They are checked by `BaseAIReviewProvider.ensure_configured()`,
  called at the top of `review_code`.

A provider therefore always reports its state honestly
(`status().configured`), and a genuine attempt to review code without a key
raises `ConfigurationError` immediately and with a precise message.

## Consequences

- CI runs keyless; no secret is needed to validate a pull request.
- The health endpoint can report "provider selected, not configured" — an
  actionable operational state that a crash-on-boot design cannot express.
- A misconfigured deployment is not detected at boot. Mitigated: `/health`
  surfaces `configured: false`, and Phase 1 adds it to the integration page's
  setup checklist.

## Alternatives rejected

- **Require a key at boot.** Rejected: CI and tests would need a real secret,
  which is both a leak risk and a needless dependency.
- **Default to a dummy key.** Rejected: it converts a loud configuration error
  into a confusing authentication error from the vendor.
