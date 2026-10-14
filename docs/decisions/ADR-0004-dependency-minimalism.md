# ADR-0004 — Add a dependency only when code uses it today

- **Status:** Accepted
- **Date:** 2026-09-15
- **Phase:** 0

## Context

It is tempting to install everything the finished system will need — Recharts,
TanStack Query, Radix, Tree-sitter, an AI SDK — while setting up the project.
Each unused dependency is a bundle-size cost, an audit surface, and a version
that will be stale by the time it is first imported.

## Decision

A dependency enters `pyproject.toml` or `package.json` in the phase where code
imports it, not before.

Consequently absent from Phase 0, by design:

| Not installed | Arrives in |
|---|---|
| Any AI vendor SDK | AI integration phase |
| `tree-sitter` and grammars | Phase 2 (AST analysis) |
| Recharts | Phase 5 (analytics) |
| TanStack Query, Radix UI | Phase 4 (dashboard) |
| `pytest-cov`, `respx`, Playwright, Vitest | the phases whose tests need them |

`src/charts/` exists as a directory with a README; the library does not.

## Consequences

- A small, comprehensible surface for a project that must be explainable in a
  viva.
- Version choices are made against current documentation at the moment of use.
- Slightly more friction per phase (one install step), which is the point.

## Alternatives rejected

- **Install the full target stack now.** Rejected: unused dependencies rot,
  and a reviewer cannot tell which of them the project actually relies on.
