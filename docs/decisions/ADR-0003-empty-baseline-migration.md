# ADR-0003 — Ship one empty baseline migration

- **Status:** Accepted
- **Date:** 2026-09-15
- **Phase:** 0

## Context

Phase 0 defines no domain tables — the CodeGuard schema belongs to the phases
that use it (`architecture.md` §16), and speculative modelling is explicitly
out of scope.

But a project with zero revisions cannot demonstrate that its migration
pipeline works: `alembic upgrade head` has nothing to apply, the
`alembic_version` table is never created, and `/readyz` therefore cannot
distinguish "database never migrated" from "database unreachable".

## Decision

Ship exactly one revision, `0001_baseline`, whose `upgrade()` and
`downgrade()` are both empty.

## Consequences

- `alembic_version` exists, so `/readyz` reports a real revision.
- `upgrade` / `downgrade` are exercisable from the first commit, so
  reversibility is verified continuously rather than discovered to be broken
  at migration fifteen.
- Every later migration has a stable parent; the chain never needs rewriting.
- This is the **only** migration allowed to be empty.

## Alternatives rejected

- **No migration at all.** Rejected: leaves the migration pipeline unverified
  and `/readyz` unable to report migration state.
- **Create the `jobs` table now to have something real.** Rejected: that is
  Phase 1's schema, and building a table before the code that uses it is how
  schemas acquire columns nobody can explain.
