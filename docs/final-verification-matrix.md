# Final verification matrix

What was actually executed, in what environment, with what result, and where the
evidence is. Anything not executed says so.

**Date of this run:** 2026-09-18
**Version:** 0.6.1
**Environment:** one shared Linux cloud container, Python 3.11.15, PostgreSQL 16.13

## The vocabulary, and what each word costs

| Status | Means |
|---|---|
| **LIVE VERIFIED** | Exercised against the real third-party service with real credentials. |
| **REAL LOCAL VERIFIED** | Executed for real, locally: real interpreter, real PostgreSQL, real HTTP. No third party. |
| **MOCK VERIFIED** | Executed against a fake or in-memory stand-in for the third party. |
| **STATICALLY VERIFIED** | Read, parsed or type-checked. Not executed. |
| **BLOCKED** | Could not be verified. The reason is named. |
| **NOT IMPLEMENTED** | The code does not exist. |

**Nothing in this project is LIVE VERIFIED.** No GitHub App credentials, no GitHub
OAuth credentials and no Gemini API key have been available in any environment
this project has been built or verified in, through Phase 6 inclusive. Every row
that would need one says BLOCKED.

## Environment capability, re-probed for this run

| Capability | State | Evidence |
|---|---|---|
| Python 3.11.15 with httpx, pydantic, pydantic-settings, PyJWT, starlette, uvicorn | available | `python3 -c 'import …'` |
| pytest 9.0.3, ruff 0.15.11, mypy 1.20.2 (uv-managed, `/root/.local/bin`) | available | `--version` |
| PostgreSQL 16.13, socket `/var/tmp/pg3`, port 55433 | available | `pg_isready` |
| Node 22.22.2, npm 10.9.7, `tsc` | available | `--version` |
| **PyPI** | **BLOCKED — 403 Forbidden** | `curl -o /dev/null -w %{http_code} https://pypi.org/simple/` → `403` |
| **npm registry** | **BLOCKED — 403 Forbidden** | `curl … https://registry.npmjs.org/` → `403` |
| **Docker daemon** | **BLOCKED — Docker unavailable** | CLI 29.4.3 present; `/var/run/docker.sock` absent |
| fastapi, sqlalchemy, alembic, asyncpg, structlog, pytest-asyncio | **BLOCKED — dependency unavailable** | `uv pip install sqlalchemy` → 403 |
| React, Vite, Vitest | **BLOCKED — dependency unavailable** | npm 403 |
| GitHub App / OAuth / Gemini credentials | **BLOCKED — credential unavailable** | all environment variables unset |
| Browser tooling for the frontend | **BLOCKED — dependency unavailable** | no Vite dev server, no browser driver |

A note on the shims. Two stand-ins were used and neither weakens a result.
`/tmp/p6shim/structlog.py` is a no-op logger; no test in the suite asserts on
rendered log output (verified by grep for `configure_logging`, `JSONRenderer`
and `capsys` across `tests/`), and the redaction functions that *are* under test
are pure and called directly. `verification/shim_minimal/` supplies the few
SQLAlchemy names the dashboard query-builder imports at module scope, so the
real query-builder functions can be called and the **SQL they return** executed;
it does not execute any SQL itself. **A test run through a shim is not
equivalent to a real framework test**, and no row below claims it is.

## 1. Tooling and the test suite

| Area | Test | Environment | Result | Evidence |
|---|---|---|---|---|
| Lint | `ruff check app tests` | real ruff 0.15.11 | **REAL LOCAL VERIFIED** — All checks passed | 0 violations |
| Format | `ruff format --check app tests` | real ruff | **REAL LOCAL VERIFIED** | 159 files already formatted |
| Types (backend) | `mypy app --ignore-missing-imports` | real mypy 1.20.2 | **REAL LOCAL VERIFIED** — 9 errors, all `unused-ignore` on tree-sitter stubs absent from this environment | see §Residual below |
| Types (frontend) | `tsc --noEmit`, strict + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` | real tsc | **REAL LOCAL VERIFIED** — 0 errors | |
| Unit / integration suite | `pytest tests/ --ignore=tests/api --ignore=tests/integration` | real pytest 9.0.3 | **REAL LOCAL VERIFIED** — **871 passed**, 0 failed | |
| API + DB-integration suite | `pytest tests/api tests/integration` | — | **BLOCKED — dependency unavailable** (fastapi, sqlalchemy) | 72 test functions not run |
| Frontend unit tests | **67** pure-logic tests, compiled with real `tsc` and run under real Node via the repository's own `npm run test` runner | real node 22.22.2 | **REAL LOCAL VERIFIED** — 67 passed | Vitest itself BLOCKED (npm 403). *Phase 6 reported 17 here; that was an undercount — the scratch harness used then compiled only two of the five test files. Corrected in v0.6.1 by running `scripts/run-tests.mjs`, which is what `npm run test` runs.* |
| Frontend component / render tests | — | — | **BLOCKED — dependency unavailable** | React and Vitest uninstallable |

**A finding about the suite itself.** At the end of Phase 5, `tests/conftest.py`
imported FastAPI at module scope, so one missing optional dependency aborted the
*entire* pytest session — the suite could not run at all. Separately, 96 of the
runnable tests are coroutines and `pytest-asyncio` is uninstallable; pytest 9.0.3
fails such a test with "You need to install a suitable plugin for your async
framework". (An earlier Phase 6 note claimed pytest reported them as *passed*
without running them. Running the Phase 6 regression file against the Phase 5
tree disproved that; the claim is corrected here rather than carried forward.
The consequence was 96 tests that could not run, not 96 tests silently lying.)
Both are fixed, and **this is the first phase in which the backend suite has
actually executed.**

## 2. Database, against a real PostgreSQL 16.13

Every harness drives the *exact SQL the application builds* — extracted from the
repository modules by AST, or produced by calling the real query-builder
functions. Re-runnable: `python3 backend/verification/run_all.py`.

| Area | Test | Environment | Result | Evidence |
|---|---|---|---|---|
| Migrations | upgrade → exercise → downgrade to 0 tables → upgrade → exercise | real PostgreSQL | **REAL LOCAL VERIFIED** — 7 passed | `verify_migration_cycle.py`; 14 tables, 216 constraints |
| Job queue | lease race, 8 workers ↔ 1 job, 8 ↔ 8, concurrent duplicate enqueue, redelivery, 10 stale-recovery cycles, sweep racing claim, dead-letter | real PostgreSQL, separate concurrent `psql` backends | **REAL LOCAL VERIFIED** — 21 passed | `verify_queue_concurrency.py` |
| Stale PR / concurrent runs | 8 workers ↔ 1 commit, attempt numbering, supersede scope, supersede ↔ finish race ×20 | real PostgreSQL, concurrent backends | **REAL LOCAL VERIFIED** — 12 passed | `verify_review_run_race.py` |
| Review entities | constraints, cascades, the inline-requires-verified-anchor invariant | real PostgreSQL | **REAL LOCAL VERIFIED** — 20 passed | `verify_phase3_db.py` |
| Auth and scope | session lifecycle, `ViewerScope`, cross-tenant isolation | real PostgreSQL | **REAL LOCAL VERIFIED** — 28 passed | `verify_auth_sql.py` |
| Dashboard | every query executes; the Phase 4 fixture set produces the specified numbers | real PostgreSQL | **REAL LOCAL VERIFIED** — 55 passed | `verify_dashboard_sql.py` |
| Audits | execution primitives, secrets, injection/traversal/SSRF/redirect, config parity, schema parity both directions, analytics integrity | real PostgreSQL + AST | **REAL LOCAL VERIFIED** — 27 passed | `final_audit.py` |
| **SQLAlchemy binding and session lifecycle** | — | — | **BLOCKED — dependency unavailable** | the SQL is proven; the ORM layer around it is not |
| **`alembic revision --autogenerate`** | — | — | **BLOCKED — dependency unavailable** | parity checked by AST instead: every migration column ↔ every model column, both directions, plus `server_default` parity |

**Total: 170 checks against a real PostgreSQL, 0 failures.**

## 3. HTTP and the middleware stack

| Area | Test | Environment | Result | Evidence |
|---|---|---|---|---|
| Middleware stack | 41 tests over real HTTP: security headers, conditional HSTS, correlation id, body limits, trusted host, forwarded headers by hop count, CORS, rate limiting, stack order | real Starlette + `TestClient` | **REAL LOCAL VERIFIED** | `tests/security/test_middleware_over_http.py` |
| Rate limiting | 429 carries `Retry-After`; probes exempt; buckets independent; session-keyed | real HTTP | **REAL LOCAL VERIFIED** | found defect P6-5 |
| Body size limit | oversized body refused before buffering; never reaches the app | real HTTP | **REAL LOCAL VERIFIED** | |
| FastAPI routes, dependency injection, RFC 9457 rendering | — | — | **BLOCKED — dependency unavailable** | routes are STATICALLY VERIFIED only |

## 4. The AI → GitHub path

| Area | Test | Environment | Result | Evidence |
|---|---|---|---|---|
| Validator, 14 gates | every gate asserted by its specific `RejectionReason` | real pytest | **REAL LOCAL VERIFIED** | `tests/review/test_validator.py` |
| Adversarial model output (§13) | 113 tests: traversal, SHA mismatch, fabricated anchors, credential leakage from every prose field, unicode and control payloads, marker forgery, a barrage that must publish nothing, and a truthful claim that must survive | real pytest | **REAL LOCAL VERIFIED** | `tests/review/test_adversarial_model_output.py`; found defect P6-2 |
| Publisher | stale head, 422 salvage, ambiguous write reconciliation, idempotent markers | real pytest, fake GitHub client | **MOCK VERIFIED** | `tests/review/test_publisher.py` |
| Gemini provider | request shaping, response parsing, error classification | real pytest, fake HTTP transport | **MOCK VERIFIED** | `tests/review/test_gemini_provider.py` |
| **A real review of a real pull request** | — | — | **BLOCKED — credential unavailable** | no GitHub App, no Gemini key |
| **A comment posted to a real repository** | — | — | **BLOCKED — credential unavailable** | and per the brief, only a dedicated test repository would ever be used |
| **Webhook delivery from GitHub** | — | — | **BLOCKED — credential unavailable** | signature verification is REAL LOCAL VERIFIED against synthetic payloads |

## 5. Resource limits (§21)

| Area | Test | Environment | Result | Evidence |
|---|---|---|---|---|
| Every configured limit is read by something | AST sweep of `Settings` against the whole application | real pytest | **REAL LOCAL VERIFIED** | found 3 inert settings; see P6-9 |
| `AI_MAX_REQUESTS_PER_REVIEW` | the call past the ceiling is refused; a failing call still spends budget | real pytest | **REAL LOCAL VERIFIED** | now enforced by `ProviderCallBudget` |
| Context byte ceiling | 5 scales from 40 to 3000 files | real pytest | **REAL LOCAL VERIFIED** | found defect P6-6 |
| Findings cap, file cap, anchor bounds | | real pytest | **REAL LOCAL VERIFIED** | `tests/security/test_resource_limits.py` |
| Request body ceiling | | real HTTP | **REAL LOCAL VERIFIED** | middleware suite |

## 6. Performance (§25)

Measured locally, median of 15 runs after warm-up. **These are not SLA numbers
and are not presented as any.** One shared cloud container, synthetic inputs, no
GitHub and no AI provider in the loop.

| Stage | 1 unit | mid | large |
|---|---|---|---|
| `parse_patch` + `build_line_map` | 0.07 ms (1 hunk) | 0.38 ms (10) | 3.59 ms (100 hunks, 20 KB) |
| Python AST analysis | 0.19 ms (461 B) | 4.93 ms (9 KB) | 37.61 ms (92 KB) |
| `build_review_context` | 0.04 ms (1 file) | 0.96 ms (40) | 4.72 ms (3000 files) |
| `validate` | 0.03 ms (1 claim) | 0.66 ms (25) | 4.64 ms (200 claims) |
| `render_comment_body` | < 0.01 ms | | |

Every stage scales linearly in its input; none hides a quadratic. The complete
CPU-bound pipeline for a 3000-file pull request is under 50 ms.

**Not measured and not estimated:** the GitHub round trips and the AI provider
call, which dominate a real review's wall-clock time. No credentials exist for
either, so no end-to-end latency figure appears anywhere in this release.

Re-runnable: `python3 backend/verification/benchmark.py`.

## 7. Deployment

| Area | Test | Environment | Result | Evidence |
|---|---|---|---|---|
| Dockerfile, compose files, entrypoint | read and parsed | — | **STATICALLY VERIFIED** | never built, never run |
| **Container build** | — | — | **BLOCKED — Docker unavailable** | daemon socket absent |
| **Two-process deployment (api + worker from one image)** | — | — | **BLOCKED — Docker unavailable** | |
| **Health and readiness probes under an orchestrator** | — | — | **BLOCKED — Docker unavailable** | the endpoints themselves are BLOCKED on FastAPI too |
| CI workflow | read | — | **STATICALLY VERIFIED** | see the finding below |

**A finding about CI.** When Phase 6 first ran the project's own configured lint
step, `ruff check` reported 210 violations and `ruff format --check` wanted to
reformat 81 of 153 files. Both are CI steps. **Every commit since Phase 0 would
have failed CI**, which means CI had never actually run. It is now clean, but
the workflow has still never been executed by GitHub Actions — that remains
**BLOCKED — credential unavailable**.

## Residual mypy errors

Nine, all of one kind: `Unused "type: ignore" comment` in
`treesitter_adapter.py` (4) and `python_adapter.py` (5). The ignores are correct
where tree-sitter's type stubs are installed and redundant where they are not;
this environment is the latter. Removing them would break the former. Recorded
as an environment artefact rather than suppressed, and rechecked whenever
tree-sitter stubs become installable.

## How to reproduce

```bash
# backend gate
cd backend
export PATH="$HOME/.local/bin:$PATH"
ruff check app tests && ruff format --check app tests
mypy app --ignore-missing-imports
pytest tests/ --ignore=tests/api --ignore=tests/integration   # 871 passed

# database gate (needs a PostgreSQL 16 the harnesses can reach)
CODEGUARD_PGDIR=/var/tmp/pg3 CODEGUARD_PGPORT=55433 python3 verification/run_all.py   # 170 passed

# local timings — not an SLA
python3 verification/benchmark.py

# frontend
cd ../frontend && npx tsc --noEmit
```

In an environment with working package registries, the blocked rows above are
the work to do next, in this order: install the real dependencies, run
`tests/api` and `tests/integration` (72 tests), run
`alembic revision --autogenerate` and confirm it proposes nothing, build the
container, then — with a GitHub App and a Gemini key, against a dedicated test
repository only — exercise a real review end to end.

---

# Appendix — requirement audit, Phases 0–6

One row per headline requirement of each phase: what it is, where it lives,
what tests it, whether that test **actually executed**, and the resulting status.
The fourth column is the one that matters; a requirement whose test exists but
did not run is not verified.

## Phase 0 — foundations

| Requirement | Implementation | Test | Executed? | Status |
|---|---|---|---|---|
| Vendor-neutral AI provider seam | `app/ai/contracts.py`, `app/ai/registry.py` | `tests/unit/test_ai_provider.py` (17) | yes | **REAL LOCAL VERIFIED** |
| Framework-free error hierarchy | `app/core/errors.py` | `tests/unit/test_backoff.py` (17) | yes | **REAL LOCAL VERIFIED** |
| Settings validated, secrets never echoed | `app/core/config.py` | `tests/unit/test_config.py`, `final_audit.py` §23 | yes | **REAL LOCAL VERIFIED** |
| Structured logging with redaction | `app/core/logging.py` | `tests/unit/test_logging_redaction.py` | yes (redaction functions are pure) | **REAL LOCAL VERIFIED** |
| Empty baseline migration (ADR-0003) | `alembic/versions/0001` | `verify_migration_cycle.py` | yes | **REAL LOCAL VERIFIED** |

## Phase 1 — webhook ingress and the durable queue

| Requirement | Implementation | Test | Executed? | Status |
|---|---|---|---|---|
| HMAC-SHA256 over raw bytes, constant-time | `app/github/signature.py` | `tests/unit/test_webhook_signature.py` | yes | **REAL LOCAL VERIFIED** |
| A delivery is processed exactly once | partial unique index + `_INSERT_SQL` | `verify_queue_concurrency.py` (6 concurrent duplicates → 1 job) | yes, concurrently | **REAL LOCAL VERIFIED** |
| `FOR UPDATE SKIP LOCKED` claim loop | `app/db/repositories/jobs.py` | `verify_queue_concurrency.py` (8 workers ↔ 1 job) | yes, concurrently | **REAL LOCAL VERIFIED** |
| A slow worker cannot release a reassigned job | lease fence on every release | `verify_queue_concurrency.py` | yes, concurrently | **REAL LOCAL VERIFIED** |
| Stale-job recovery and dead-lettering | `sweep_stale`, `_DEAD_LETTER_SQL` | `verify_queue_concurrency.py` (10 cycles) | yes | **REAL LOCAL VERIFIED** |
| GitHub App RS256 JWT → installation token | `app/github/app_auth.py` | `tests/unit/test_github_app_auth.py` | yes, with a throwaway key | **MOCK VERIFIED** |
| Retry classification (5xx, timeout, 403, 429) | `app/github/client.py` | `tests/unit/test_github_client.py` (22) | yes, fake transport | **MOCK VERIFIED** |
| A real delivery from GitHub | — | — | **no** | **BLOCKED — credential unavailable** |

## Phase 2 — diff and AST analysis

| Requirement | Implementation | Test | Executed? | Status |
|---|---|---|---|---|
| Unified-diff parsing → `LineMap` (ADR-0008) | `app/analysis/diff/parser.py` | `tests/analysis/test_diff_parser.py` | yes | **REAL LOCAL VERIFIED** |
| `line` + `side`, never deprecated `position` | `app/review/publisher.py` | `tests/review/test_publisher.py` | yes | **REAL LOCAL VERIFIED** |
| Source is read at the head SHA, never cloned | `app/analysis/source/` | `tests/analysis/test_source_file.py`, `final_audit.py` §22 | yes | **REAL LOCAL VERIFIED** |
| Repository code is parsed, never executed | `ast` + tree-sitter | `final_audit.py` §22 (0 eval/exec/compile/shell) | yes | **REAL LOCAL VERIFIED** |
| Changed line → declaration mapping | `app/analysis/mapping/mapper.py` | `tests/analysis/test_mapping.py` | yes | **REAL LOCAL VERIFIED** |

## Phase 3 — AI review and the trust plane

| Requirement | Implementation | Test | Executed? | Status |
|---|---|---|---|---|
| Deterministic, budgeted context | `app/review/context.py` | `tests/review/test_context_builder.py`, `test_resource_limits.py` | yes | **REAL LOCAL VERIFIED** |
| The byte ceiling is a ceiling | `build_review_context` | `test_resource_limits.py`, 5 scales to 3000 files | yes | **REAL LOCAL VERIFIED** (defect P6-6 fixed) |
| The free-tier call ceiling is enforced | `app/ai/budget.py` | `test_resource_limits.py` | yes | **REAL LOCAL VERIFIED** (defect P6-9 fixed) |
| All fourteen validation gates | `app/review/validator.py` | `test_validator.py` + `test_adversarial_model_output.py` (113) | yes | **REAL LOCAL VERIFIED** |
| **AI output cannot bypass validation and reach GitHub** | type boundary + no `RawFinding` import | `test_adversarial_model_output.py`, structural assertion | yes | **REAL LOCAL VERIFIED** |
| Model prose cannot forge a comment's identity | `_sanitise_model_text`, `extract_marker` | 4 parametrized + 1 structural | yes | **REAL LOCAL VERIFIED** (defect P6-2 fixed) |
| Rejections persisted with a specific reason (ADR-0012) | `RejectionReason`, `FindingRepository` | `verify_phase3_db.py` | yes | **REAL LOCAL VERIFIED** |
| An inline finding cannot be stored without a verified anchor | DB check constraint | `verify_migration_cycle.py` asserts the DB refuses it | yes | **REAL LOCAL VERIFIED** |
| Stale head aborts publication | `_observed_head_sha` | `tests/review/test_publisher.py` | yes, fake client | **MOCK VERIFIED** |
| 422 salvage; ambiguous write reconciled | `ReviewPublisher` | `tests/review/test_publisher.py` | yes, fake client | **MOCK VERIFIED** |
| A real Gemini call | — | — | **no** | **BLOCKED — credential unavailable** |
| A real comment on a real pull request | — | — | **no** | **BLOCKED — credential unavailable** |

## Phase 4 — dashboard API and UI

| Requirement | Implementation | Test | Executed? | Status |
|---|---|---|---|---|
| Every dashboard number is a SQL aggregate over real rows | `app/db/repositories/dashboard.py` | `verify_dashboard_sql.py` (55) | yes, real PostgreSQL | **REAL LOCAL VERIFIED** |
| Keyset pagination, cursor is a position | `app/api/filters.py` | `tests/dashboard/test_pagination.py` | yes | **REAL LOCAL VERIFIED** |
| A superseded run is never shown as current | `head_sha`-anchored query | `verify_dashboard_sql.py` | yes | **REAL LOCAL VERIFIED** |
| The funnel's stages sum (emitted / dropped / validated / rejected) | distinct columns | `final_audit.py` §26 | yes | **REAL LOCAL VERIFIED** |
| No accuracy / precision / recall / false-positive claim | wording discipline | `final_audit.py` §26, frontend tests | yes | **REAL LOCAL VERIFIED** |
| A rejected claim is never labelled a false positive | wording discipline | `final_audit.py` §26 | yes | **REAL LOCAL VERIFIED** |
| Never colour alone; every state has a glyph and a label | `frontend/src/utils/status.ts` | 17 frontend tests, real Node | yes | **REAL LOCAL VERIFIED** |
| React components render correctly | — | — | **no** | **BLOCKED — dependency unavailable** |

## Phase 5 — authentication, security, deployment

| Requirement | Implementation | Test | Executed? | Status |
|---|---|---|---|---|
| Real authentication, no header/query/localStorage identity | `app/auth/`, opaque server-side sessions | `verify_auth_sql.py` (28), `tests/auth/` | yes, real PostgreSQL | **REAL LOCAL VERIFIED** |
| Only the SHA-256 of a session token is stored | `app/auth/tokens.py` | `tests/auth/test_tokens_and_csrf.py` | yes | **REAL LOCAL VERIFIED** |
| Authorization is a SQL predicate; cross-tenant → 404 | `ViewerScope`, `user_installation_access` | `verify_auth_sql.py`, `tests/dashboard/test_scope_and_security.py` | yes | **REAL LOCAL VERIFIED** |
| CSRF double-submit derived from the session | `app/auth/csrf.py` | `tests/auth/`, middleware suite | yes | **REAL LOCAL VERIFIED** |
| The login redirect cannot be used to inject a header | `_safe_redirect_path` | `tests/unit/test_phase6_regressions.py` + 7 payloads | yes | **REAL LOCAL VERIFIED** (defect P6-3 fixed) |
| Security headers, HSTS only over TLS | `SecurityHeadersMiddleware` | 41 tests over real HTTP | yes | **REAL LOCAL VERIFIED** |
| Proxy headers trusted by hop count, not address | `ProxyHeadersMiddleware` | real HTTP, incl. a forged client entry | yes | **REAL LOCAL VERIFIED** |
| Body size refused before buffering | pure-ASGI `BodySizeLimitMiddleware` | real HTTP | yes | **REAL LOCAL VERIFIED** |
| Rate limiting answers 429 with `Retry-After` | `RateLimitMiddleware` | real HTTP | yes | **REAL LOCAL VERIFIED** (defect P6-5 fixed) |
| Production fails closed on unsafe configuration | `app/core/config.py` validators | `final_audit.py` §23 — 9 unsafe configs refused | yes | **REAL LOCAL VERIFIED** |
| `safe_summary` leaks no secret | `app/core/config.py` | `final_audit.py` §23 — 19 fields checked | yes | **REAL LOCAL VERIFIED** |
| Container runs as non-root, two processes from one image | `Dockerfile`, compose files | — | **no** | **BLOCKED — Docker unavailable** |
| Health and readiness under an orchestrator | `app/api/health.py` | — | **no** | **BLOCKED — Docker unavailable** + FastAPI unavailable |
| A real person signing in with GitHub | — | — | **no** | **BLOCKED — credential unavailable** |

## Phase 6 — final verification

| Requirement | Implementation | Test | Executed? | Status |
|---|---|---|---|---|
| The test suite runs at all | `tests/conftest.py` | the suite itself | yes — **first time in the project** | **REAL LOCAL VERIFIED** |
| Async tests actually execute | `pytest_pyfunc_call` fallback hook | `test_phase6_regressions.py` | yes | **REAL LOCAL VERIFIED** |
| Lint and format clean under the project's own config | `pyproject.toml` | `ruff check`, `ruff format --check` | yes | **REAL LOCAL VERIFIED** |
| A superseded run stays superseded | `finish` guard + `_settle` | `verify_review_run_race.py`, 20-round concurrent race | yes | **REAL LOCAL VERIFIED** (defect P6-1 fixed) |
| Schema parity in both directions | AST comparison | `final_audit.py` §24 | yes | **REAL LOCAL VERIFIED** (defect P6-8 fixed) |
| Every configured limit is read by something | AST sweep | `test_resource_limits.py` | yes | **REAL LOCAL VERIFIED** (defect P6-9 fixed) |
| Every Phase 6 fix has a test that fails on Phase 5 | `test_phase6_regressions.py` | run against the extracted Phase 5 tarball | yes — **11 of 11 defect regressions failed there**, all pass here. The file's 12th test is a *scope pin* (§27, the absent finding-status workflow), not a defect regression; it passes on both trees by design. | **REAL LOCAL VERIFIED** |
| The verification is reproducible | `backend/verification/` + `run_all.py` | run from its new location | yes — 170 passed | **REAL LOCAL VERIFIED** |
| `alembic revision --autogenerate` proposes nothing | — | — | **no** | **BLOCKED — dependency unavailable** |
| Anything against GitHub or Gemini | — | — | **no** | **BLOCKED — credential unavailable** |
