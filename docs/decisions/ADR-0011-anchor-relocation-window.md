# ADR-0011 — Anchors may relocate within ±3 lines, but only unambiguously

- **Status:** Accepted · **Date:** 2026-09-16 · **Phase:** 3

## Context

Every finding must quote `anchor_snippet`: text the model says appears at the
line it cites. Validation compares that snippet against the real file. The
strict rule — the snippet must be at exactly the cited line or the finding is
dropped — is the safest possible rule and also throws away good findings, because
models routinely miscount line numbers by one or two while quoting the code
correctly.

## Decision

Gate 11 searches a window of ±3 lines around the cited line:

1. If the anchor matches at the **cited** line, that line wins outright. No
   search, no ambiguity, `anchor_relocated = false`.
2. Otherwise, if the anchor matches **exactly one** line in the window, the
   finding is relocated to it, `anchor_relocated = true`, and the comment body
   says so.
3. If it matches **more than one** line in the window, the finding is rejected
   as `anchor_ambiguous`.
4. If it matches none, it is rejected as `anchor_not_found`.

Every candidate line must independently pass the diff (`is_commentable`) and
presentation gates. Relocation cannot move a finding onto a line the pull
request did not touch, or onto a line we never showed the model.

`claimed_line_start`, `resolved_line` and `anchor_offset` are all persisted.

## Consequences

**Why ±3 and not ±10.** The window is a trade between recall and evidence. A
unique exact match three lines away is strong evidence the model was looking at
that line and miscounted. A unique match ten lines away is much weaker: in a
typical file, ten lines is enough to contain a different occurrence of similar
code, and the uniqueness test starts to pass by luck rather than by meaning.
Three absorbs the errors models actually make — off-by-one, off-by-two, and the
occasional blank-line miscount — without inviting coincidence.

**Why ambiguity is rejected rather than resolved.** With two identical
candidate lines, any tie-break (nearest, first, highest) is a guess. A comment
on the wrong one of two identical lines looks *correct* to a reader, so the
error is invisible and unchallengeable. Publishing nothing is strictly better
than publishing a confident wrong location.

**Why the relocation is disclosed in the comment.** A developer who compares
the comment against the model's reasoning would otherwise see a discrepancy and
conclude the tool is unreliable. Saying "resolved from the model's claim at
line N by matching the quoted source" turns an apparent defect into evidence
that verification happened.

**The measurement this enables.** `anchor_offset` over many runs answers "how
often is the model exactly right about location, and how wrong is it when it is
wrong?" — a genuine, defensible research output of the project. A strict rule
would have destroyed that data by dropping every non-zero offset.
