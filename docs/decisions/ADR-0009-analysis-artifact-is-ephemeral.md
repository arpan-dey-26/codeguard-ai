# ADR-0009 — The Phase 2 analysis artifact is ephemeral

- **Status:** Accepted · **Date:** 2026-09-16 · **Phase:** 2

## Context

Phase 2 produces a `PullRequestAnalysis`: per-file diff classification, a
LineMap, AST declarations and changed-line → declaration mappings. Persisting
it is tempting — it is structured, it took work to compute, and a dashboard
could show it.

## Decision

Do not persist it. No new tables, no migration.

## Consequences

Four reasons, in order of weight:

1. **`architecture.md` §16 defines no Phase 2 table.** The schema is specified;
   inventing a table because persistence "seems convenient" is exactly the
   speculative modelling Phase 0 set out to avoid.
2. **It is reproducible.** Its inputs are the head SHA, the patches and the
   source — all either already stored (`changed_files`, Phase 1) or
   retrievable from GitHub. Storing a derived value that can be recomputed
   creates a second source of truth that can drift.
3. **It carries third-party source text.** `FileAnalysis.source` and
   `.line_map` hold real code. §16.7 is explicit about not storing customer
   source, and the same reasoning applies with more force here.
4. **Nothing consumes it across a process boundary yet.** Phase 3 runs in the
   same job, immediately after. If a later phase genuinely needs it across
   runs, that is the moment to design persistence — with a consumer to shape it.

`to_dict()` exists and is deterministic, so the artifact can be logged at debug
level, diffed between runs, or persisted later without redesign. It excludes
source text by construction, which a test asserts.

## Alternatives rejected

- **A `file_analyses` table now.** Rejected: unused columns acquire meanings
  nobody can explain, and it would store source code.
- **Cache the artifact in `jobs.payload`.** Rejected: the payload is ids only,
  by design, and JSONB is not a cache.
