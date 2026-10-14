# ADR-0010 — The model may claim `side: LEFT`; the validator rejects it

- **Status:** Accepted · **Date:** 2026-09-16 · **Phase:** 3

## Context

GitHub's review-comment API places a comment on either image of a file:
`side: RIGHT` is the new version, `side: LEFT` is a line the pull request
deleted. Platform policy (architecture.md §9.5) is that we never comment on
deletions — a comment on code that no longer exists is confusing, and it
accounts for a large share of 422 rejections.

The question is how the AI contract should express that. Two options:

1. **`RawFinding` has no `side` field.** Every finding is implicitly `RIGHT`.
2. **`RawFinding` has a `side` field the model may set to `LEFT`,** and the
   validator rejects `LEFT` explicitly.

Option 1 looks safer: a field that does not exist cannot carry a wrong value.

## Decision

Option 2. `FindingSide` is part of the AI contract, the response schema offers
both values, and `FindingValidator` gate 6 rejects anything that is not
`RIGHT`, counting it as `wrong_side`.

## Consequences

**The invariant being protected is "no deletion finding is published as a
RIGHT comment."** Option 1 does not protect it — it *guarantees the breach*.
A model that has noticed a problem with a deleted line will still report it;
with no `side` field it simply reports it with a line number, and that line
number is a **pre-image** line number. We would then treat a LEFT line as a
RIGHT line, and post a comment on whatever unrelated code now occupies that
position in the new file. Silent coercion is the bug, not the fix.

Making the field explicit converts an undetectable mispositioning into a
counted rejection. That has three further benefits:

- The rejection count is a **prompt-quality signal**. A rising `wrong_side`
  rate means the instruction "report only RIGHT findings" is not landing, and
  that is actionable.
- The policy is enforced in exactly one place. A reader of `validator.py` sees
  the rule; a reader of a contract with no `side` field sees nothing and must
  infer the policy from its absence.
- If the policy ever changes — commenting on deletions is a legitimate product
  choice — the change is one gate, not a schema migration plus a new field
  plus re-prompting.

**The cost** is one more field a model can get wrong, and one more gate. Both
are cheap. The alternative's cost is a class of wrong-location comments that
nothing in the system can detect.
