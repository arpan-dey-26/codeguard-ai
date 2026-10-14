# ADR-0006 — Key `changed_files` on the commit, not on the review run

- **Status:** Accepted · **Date:** 2026-09-15 · **Phase:** 1
- **Amends:** `architecture.md` §16.7

## Context

The baseline keys `changed_files` on `review_run_id`, modelling it as a
snapshot belonging to one review run. But `review_runs` is a Phase 3 table,
and Phase 1 needs to persist a changed-file set to prove the GitHub API
integration works.

Creating `review_runs` early, unused and unexercised, would be speculative
modelling of exactly the kind Phase 0 set out to avoid.

## Decision

Key the Phase 1 table on `(pull_request_id, head_sha, file_path)`.
Phase 3 adds a nullable `review_run_id` column.

## Consequences

This is arguably the better key regardless of scheduling:

- The changed-file set is a property of a **commit**, not of a review run.
  Two runs over the same commit cannot disagree about which files changed.
- A re-run over an already-synced commit reuses the rows instead of paying for
  another paginated GitHub call.
- Both commits' file sets coexist after a push, which is what a "what changed
  between runs?" view will want in Phase 4.

Cost: Phase 3 must add one nullable column and backfill nothing. Findings
still reference `changed_file_id`, so no downstream design changes.

## Alternatives rejected

- **Create `review_runs` now.** Rejected: a table with no code using it
  acquires columns nobody can explain.
- **Defer persisting changed files to Phase 2.** Rejected: the Phase 1 exit
  criterion is proving the GitHub integration end to end, and "we fetched the
  files but stored nothing" does not prove it.
