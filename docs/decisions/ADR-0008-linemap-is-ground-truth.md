# ADR-0008 — The LineMap is ground truth, derived from the diff alone

- **Status:** Accepted · **Date:** 2026-09-16 · **Phase:** 2

## Context

Phase 3 will validate every AI-produced finding against a claimed file path and
line number. That validation is only as good as the thing it validates
*against*. If the reference is approximate, the validation is theatre.

There was a tempting shortcut: derive changed lines lazily, from whatever the
model says plus a quick re-read of the file. It is less code and it usually
works.

## Decision

Build the `LineMap` **before** any AI exists, from the unified diff alone, and
make it the single reference for every later location question.

- Pure: string in, data out. No GitHub, no database, no clock, no AI.
- Immutable and deterministic.
- Deletions are first-class (`side: LEFT`, old-file numbering) and never folded
  into new-file lines.
- Commentability is a property of the map, computed from hunk membership —
  not a guess made at publication time.
- `position` (GitHub's deprecated diff offset) is **not** the internal
  representation. `line` + `side` is.

## Consequences

- The reference is fixed before anything has an incentive to bend it. A model's
  interpretation of line numbers cannot influence the design, because the
  design predates the model.
- It is exhaustively testable with golden fixtures, and was: 40 cases covering
  hunk arithmetic, `\ No newline at end of file`, CRLF, Unicode, zero-context
  hunks, absent patches and malformed input.
- Two off-by-one bugs were caught by those fixtures during Phase 2 itself — a
  phantom trailing line from the final newline, and the no-newline marker.
  Both would have silently mis-anchored review comments.
- Cost: the diff layer cannot reuse GitHub's `position` field, so the mapping
  work is ours. That is the point.

## Alternatives rejected

- **Derive locations at publication time.** Rejected: it makes the reference
  depend on the thing being checked.
- **Use GitHub's `position`.** Rejected: documented as closing down, and it is
  an offset into a patch rather than a location in a file — unusable for
  anchoring against source.
