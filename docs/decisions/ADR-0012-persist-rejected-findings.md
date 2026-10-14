# ADR-0012 — Rejected model claims are persisted, not discarded

- **Status:** Accepted · **Date:** 2026-09-16 · **Phase:** 3

## Context

`FindingValidator` rejects claims at fourteen gates. A rejected claim is never
published. The obvious thing to do with it is nothing.

## Decision

Persist it: a `findings` row with `mapping_status = 'dashboard_only'`,
`anchor_verified = false`, and `rejection_reason` set to the gate that stopped
it. The `review_runs` funnel counters record the totals.

## Consequences

**The rejections are the measurement.** The single most important question
about an AI code-review tool is "how often is it wrong, and wrong in what way?"
Keeping only the findings that survived validation makes that question
permanently unanswerable — the survivors are, by construction, the ones that
passed. The rejections are where the answer lives:

- a rising `anchor_not_found` rate is a prompt regression;
- a rising `line_not_in_diff` rate means the context is showing the model code
  it should not comment on;
- a non-zero `secret_in_text` rate is a security signal;
- `unknown_file` and `unsafe_path` above zero are a **prompt-injection
  indicator**, because a well-behaved model does not invent `../../etc/passwd`.

None of these are reconstructable after the fact. A rejected claim leaves no
other trace anywhere in the system.

**Why the funnel counters are stored as columns** rather than computed from
these rows: `findings_emitted` counts claims the model made, including ones
dropped before a row could be written (malformed JSON items the provider could
not coerce). The count is a historical fact about a run, not an aggregate of
surviving rows, and that is the criterion for storing a number rather than
computing it (architecture.md §16.15).

**The cost** is rows that are never shown on the main dashboard views. They are
bounded by `ai_max_findings` per run (25), so a repository with a thousand runs
accumulates at most 25,000 such rows — trivially indexed, and excluded from the
default findings query by `mapping_status`.

**The risk, and its mitigation.** A rejected claim's text is untrusted model
output that may quote attacker-influenced source. It is stored with a
generated title, a truncated detail string, zero confidence, and never appears
in a GitHub comment. Gate 2 (secret leakage) runs *before* persistence, so a
claim containing a credential shape is rejected and its text is never written.
