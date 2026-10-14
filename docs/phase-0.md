# Phase 0 — Repository Foundation & Developer Infrastructure

Scope, deliverables and exit criteria for the foundation phase.

## Objective

A clean, verified, provider-ready foundation — and nothing more. The value of
this phase is that the hard-to-retrofit things exist before any feature does:
structured logging with redaction, correlation IDs, validated configuration,
migration tooling, an enforced architecture boundary, and CI.

A project that adds structured logging in month three has untraceable logs for
two months.

## Delivered

| Area | Delivered |
|---|---|
| Structure | Monorepo: `backend/`, `frontend/`, `infrastructure/`, `docs/`, `.github/` |
| Backend | FastAPI app factory, versioned router, typed throughout |
| Health | `/health` (report), `/healthz` (liveness, no DB), `/readyz` (DB + migration state) |
| Errors | Typed hierarchy + RFC 9457 problem documents; no stack trace ever leaves the process |
| Config | `pydantic-settings`, `SecretStr`, startup validation, `.env.example` |
| Logging | structlog, JSON in production, correlation IDs, **redaction with a test** |
| Database | Async SQLAlchemy 2.x engine and session, connection and revision probes |
| Migrations | Alembic using the app's own settings and async engine; one baseline revision |
| AI | `AIReviewProvider` protocol, ABC, registry, Gemini + Claude seams |
| Docker | Compose: db + api + worker; one backend image, two commands |
| Frontend | Vite + React 19 + TS strict + Tailwind, routing, API client, design tokens |
| Tests | pytest: startup, health, config, redaction, provider abstraction, database |
| Tooling | ruff, mypy, ESLint, Prettier |
| CI | GitHub Actions: backend, frontend, secret scan |
| Docs | README, setup notes, four ADRs |

## Exit criteria

> ### Correction, added in v0.6.1 — these were never executed
>
> **The list below is the set of exit criteria this phase defined. It is not a
> record of checks that ran.** Every box was ticked when the phase was written;
> Phase 6 later established, by trying to run them, that none of them had been.
>
> The environment this project was built in cannot install FastAPI, SQLAlchemy,
> Alembic, React or Vite (PyPI and the npm registry both return 403), has no
> Docker daemon, and has no GitHub or AI credentials. `pytest` could not even
> start — `tests/conftest.py` imported FastAPI at module scope, so one missing
> dependency aborted the whole session. And when the project's own `ruff` step
> was finally run in Phase 6 it reported **210 violations**, which means CI had
> never run either.
>
> The criteria are left here unchanged, because rewriting them would erase what
> this phase set out to achieve. What is corrected is the claim that they were
> met. The authoritative record of what has and has not been executed is
> [`docs/final-verification-matrix.md`](final-verification-matrix.md); where the
> two disagree, the matrix is right.

| Exit criterion | Defined in Phase 0 | Actually executed, as of v0.6.1 |
|---|---|---|
| `docker compose up -d db` starts a healthy PostgreSQL | yes | **BLOCKED — Docker unavailable** |
| `alembic upgrade head` applies, `alembic downgrade -1` reverses | yes | **BLOCKED — dependency unavailable.** The migration *SQL* is executed up, down and up again against a real PostgreSQL by `backend/verification/verify_migration_cycle.py`; Alembic itself has never run. |
| `uvicorn app.main:app` starts and `/health` returns a report | yes | **BLOCKED — dependency unavailable** (FastAPI) |
| `python -m app.worker` starts and connects to the database | yes | **BLOCKED — dependency unavailable** (SQLAlchemy) |
| `pytest` passes with no API key and no network access | yes | **REAL LOCAL VERIFIED since Phase 6** — 871 tests, after the conftest fix. 72 test functions in `tests/api` and `tests/integration` remain BLOCKED. |
| `ruff`, `mypy`, `tsc`, `eslint` pass | yes | **ruff, mypy and tsc: REAL LOCAL VERIFIED since Phase 6.** ESLint: **BLOCKED — dependency unavailable**. |
| `npm run build` produces a production bundle | yes | **BLOCKED — dependency unavailable** (npm 403) |
| The SPA renders, themes, routes, and reads live backend health | yes | **BLOCKED — dependency unavailable.** No component in this project has ever been mounted in a browser. |
| CI runs all of the above without AI credentials | yes | **BLOCKED — credential unavailable.** The workflow has never been executed by GitHub Actions. |

## Explicitly out of scope

GitHub webhooks · GitHub App authentication · job queue · domain schema · diff
extraction · AST analysis · AI API calls · prompts · finding validation · line
mapping · inline comments · dashboard authentication · dashboard pages ·
analytics · production deployment.

## Deferred decisions

| Decision | Deferred to | Why |
|---|---|---|
| Gemini model identifier | AI integration phase | Chosen against current vendor documentation, not guessed |
| Job queue table shape | Phase 1 | Designed with the code that uses it |
| Domain schema | Phase 1 & 3 | `architecture.md` §16 specifies it; built when used |
| Server-state library (TanStack Query) | Phase 4 | Nothing to cache yet |
| Chart library (Recharts) | Phase 5 | No charts yet |
