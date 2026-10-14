# ADR-0007 — Split the error hierarchy from its HTTP rendering

- **Status:** Accepted · **Date:** 2026-09-15 · **Phase:** 1

## Context

Phase 0 put both the exception hierarchy and the RFC 9457 handlers in
`app/core/errors.py`, which therefore imported FastAPI.

Phase 1 introduces the worker: a process with no HTTP surface that raises,
catches and classifies these exceptions constantly. Its retry decision is a
single `isinstance` check against `TransientError` / `ThrottledError`. Making
it import a web framework to do that is a dependency inversion with no
justification, and it means the queue plane cannot be tested — or run — without
the API's dependency tree.

## Decision

- `app/core/errors.py` keeps the hierarchy and nothing else. It imports only
  `__future__`. HTTP status codes are plain integers.
- `app/api/problem.py` holds `register_exception_handlers` and the
  problem-details rendering.
- `app/main.py` registers them from the new location. Behaviour is unchanged.

## Consequences

- The worker, the GitHub client and the job queue are importable without
  FastAPI, which is what let this phase's client and auth suites run against a
  fake transport rather than a running application.
- One extra module, and one import line moved in `main.py`.
- A test asserts `core/errors.py` imports no web framework, so the coupling
  cannot creep back.

## Alternatives rejected

- **Leave it.** Rejected: the worker would carry the API's dependency tree, and
  a retry classification that needs a web framework is a design smell that gets
  worse as the queue grows.
