# Phase 5 completion report

**Production Hardening, Authentication, Real Integrations, Security & Reliability**

Date: 2026-09-17 · Revision: `0005_phase5_auth_and_fixes`

---

## Read this first

Two claims that shape everything below, stated before the detail rather than
buried in it.

**No live integration was exercised in this phase.** No GitHub App credentials,
no GitHub OAuth credentials and no Gemini API key exist in this environment
(`GITHUB_TOKEN` and `GH_TOKEN` are present but are 14-character proxy
placeholders, not GitHub tokens). Nothing in this report claims that GitHub or
Gemini integration *works*. It claims the code is written, unit-tested against
`httpx.MockTransport`, and structurally verified — which is a different and
weaker statement.

**PyPI and npm return 403.** Re-verified at the start of this phase: `pip
download fastapi` → "No matching distribution found"; `npm view react` → `403
Forbidden`. Per `/root/.ccr/README.md` a 403 is an organisation policy denial to
report, not route around. So FastAPI, SQLAlchemy, Alembic, asyncpg, pytest,
ruff, mypy, React, Vite and Vitest **cannot be installed**, and every claim
below that would need them is marked **BLOCKED**, never inferred.

---

## 1. Audit findings and fixes

Method: read `architecture.md` in full; read every Phase 0–4 module; compared
all 10 ORM models against all 4 migrations column by column; extracted every
table and column referenced in `dashboard.py`'s SQL and resolved each against
the migration-defined schema. Completion reports were not consulted.

**17 defects found. 17 fixed. Every fix has a regression test.**

| # | Issue | Sev | Component | Evidence (pre-fix) | Fix | Test |
|---|---|---|---|---|---|---|
| A1 | Webhook body size checked after `await request.body()`, so an unauthenticated POST of any size was fully buffered before rejection | **High** | `api/v1/webhooks.py:77-81` | `raw_body = await request.body()` preceded the cap; no size middleware in `main.py` | `BodySizeLimitMiddleware` — pure ASGI, checks `Content-Length` and counts streaming bytes; per-path ceilings | `test_phase5_regressions.py::test_responses_are_read_with_a_ceiling` + `test_security_invariants.py` |
| A2 | `enqueue` inserted blind against the partial unique index on `dedupe_key`; a collision raised `IntegrityError` → 500 → **the webhook audit row rolled back with it** | **High** | `repositories/jobs.py:144`, `webhook_service.py:207` | `uq_jobs_dedupe_key_active` exists; insert path unguarded. Two `installation` deliveries for one installation collide | `SAVEPOINT` + `IntegrityError` → return `None`; new `IngestOutcome.COALESCED`; `mark_coalesced` writes the audit row | `test_phase5_regressions.py` (4 cases) |
| A3 | Write transaction held open across up to 300 sequential GitHub fetches | **High** | `github_sync_service.py:149-198` | 4 write statements, then `_run_analysis` → `get_source` → HTTP, before any commit | `await self._session.commit()` before step 5 | `test_phase5_regressions.py::test_the_sync_service_commits_before_running_analysis` |
| A4 | `mark_completed` / `mark_retry` / `mark_failed` keyed on `id` alone — a worker whose lock the sweeper revoked could overwrite another worker's job state | **High** | `repositories/jobs.py:189-255` | No `AND locked_by = :worker_id`; `JobRepository` never received a worker id | `_FENCE` on all three; `worker_id` threaded from `JobRunner`; zero-row release logged | `test_phase5_regressions.py` (5 cases, incl. AST check of all 4 call sites) |
| A5 | A 429/5xx at publication escaped `publish()`; findings were already committed, so the retry re-inserted **every finding** under a new run id | **Med** | `review/publisher.py:368-397` | Only 3 exception classes caught; `ON CONFLICT (review_run_id, fingerprint)` — a new run id is a new row | Catch `TransientError`/`ThrottledError` → `PublishOutcome.FAILED` → run is `partial_success` | `test_phase5_regressions.py` (2 cases) |
| A6 | Retryable AI failure left `review_runs` `running` forever; the comment claimed a sweeper reclaimed it | **Med** | `review/service.py:230-235` | `sweep_stale` targets `jobs` only; `supersede_older_runs` matches `head_sha <> :keep` so a retry never supersedes | `ReviewRunRepository.abandon_stale`, called from the worker sweep | `test_phase5_regressions.py` (2 cases) + real-PG check |
| A7 | `sync_installation` and `sync_repositories` were routed with **no registered handler** | **Med** | `github/events.py:139,144` vs `handlers/__init__.py` | Every install/uninstall/suspend/repo-change failed with "No handler registered"; `mark_uninstalled` had no caller | Both handlers written; `deactivate_missing` added; router↔registry test | `test_phase5_regressions.py` (2 cases, bidirectional) |
| A8 | Redaction processor ran **before** `format_exc_info`, so every traceback bypassed it; `_redact` also skipped lists | **Med** | `core/logging.py:134-150` | At redaction time the record held only `exc_info=True`; `format_exc_info` wrote `exception` afterwards | Reordered; recursion through lists and tuples; value-pattern scrubbing; SQL-parameter block removal | `test_phase5_regressions.py` (8 cases) |
| A9 | Upstream responses read with no ceiling | **Med** | `github/client.py:250` | `AsyncClient.request` buffers fully; blob endpoint returns inline base64 to 100 MB; `max_file_bytes` ran after | `_send_bounded` — `Content-Length` check plus counted `aiter_bytes` | `test_phase5_regressions.py` |
| A10 | SIGTERM observed only between jobs; no in-flight drain | **Low** | `jobs/runner.py:209-228` | `run_forever` checks `stop` at loop top; no cancellation point in `run_once` | `run_until_drained` with a bounded deadline; `stop_grace_period` in compose | `test_phase5_regressions.py` |
| A11 | Pagination followed `Link` to any absolute URL with the installation token attached | **Low** | `github/client.py:241` | `url = path_or_url if startswith("http")`; no host check | `_require_trusted_host` in the request path | `test_phase5_regressions.py` (2 cases) |
| B1 | 24 columns carry `server_default` in migration 0003 that the models do not declare | **Med** | `review_run.py`/`finding.py`/… vs `0003` | `env.py` sets `compare_server_default=True`; two hot INSERT paths omit 11 NOT NULL columns and rely on the defaults | `server_default=` added to all 24 model columns | Migration applied to real PG; both INSERT paths exercised |
| B2 | Four migration-created indexes absent from model metadata | **Med-High** | `0004` vs models | `target_metadata = Base.metadata`; autogenerate would propose dropping them | Declared in `__table_args__` with provenance comments | Real-PG index-existence check |
| B3 | Three `ON DELETE SET NULL` FK child columns unindexed | **Med** | `changed_files.review_run_id`, `findings.changed_file_id`, `review_runs.webhook_event_id` | Sequential scan of the child table per parent delete; fires on ordinary repo/PR deletes | Three partial indexes in `0005` | `verify_auth_sql.py::the Phase 5 indexes all exist` |
| B4 | `skip_reason_breakdown` had no supporting index | **Med** | `dashboard.py:1100-1110` | Only index on `changed_files` leads with `pull_request_id`; the default analytics view has no repo filter | `ix_changed_files_created_at_unanalysed` (partial) | Real-PG index check |
| C1 | `_fail_fast_on_production_gaps` checked `debug` only, while claiming to require infrastructure secrets | **High** | `core/config.py` | Production inherited `postgresql+asyncpg://codeguard:codeguard@localhost` — credentials published in this repo — plus no session secret, no trusted hosts | Eight-check validator reporting every problem at once | `test_scope_and_security.py` (7 cases) + CI job |
| B5 | `finding_events.created_at` annotated `Mapped[object]` | **Low** | `finding_event.py:58` | Disabled type checking on that attribute | `Mapped[datetime]` | Typecheck |

### Checked and found clean

Recorded because an absence of findings is a result, not a gap in the audit.

- **Stale-PR race.** Four independent layers hold; the inverted case (an older
  job overtaking a newer run) is unreachable — the older job aborts at the
  head-SHA re-read.
- **Webhook signature handling.** `hmac.compare_digest`; header shape validated
  before comparison; size cap and HMAC both precede `json.loads`; a missing
  secret returns a *failure*; replay caught by `UNIQUE (github_delivery_id)` +
  `ON CONFLICT DO NOTHING` (race-free, not check-then-insert); the failure
  reason is logged and never returned.
- **Error messages to clients.** No `CodeGuardError` detail reachable from a
  route interpolates SQL, a file path, a token or an upstream body.
- **Secrets at log call sites.** All 48 `logger.*` calls in `app/` enumerated;
  none passes a credential, payload, source, prompt or completion.
- **Column parity.** All 10 tables, 193 columns: names, types, nullability, FKs,
  unique and check constraints all match between models and migrations.
- **Raw SQL references.** Every table (8) and column (128 across them) in
  `dashboard.py` exists.
- **`ON CONFLICT` targets.** All 8 have a matching unique constraint.
- **Downgrades.** All 5 migrations reverse correctly, in valid dependency order.
- **Type choices.** All 34 timestamp columns are `TIMESTAMPTZ`; no `String`
  without a length; no `JSON`/`JSONB` divergence.

---

## 2. Authentication implementation

| Requirement (§4–10) | Status | Where |
|---|---|---|
| `users` keyed on GitHub's numeric id | Done | `db/models/user.py`, `0005` |
| `user_installation_access` (composite PK, TTL refresh) | Done | same |
| Server-side sessions with revocation | Done | `user_sessions` |
| Single-use OAuth state | Done | `oauth_states` |
| `GET /auth/github/login` | Done | `api/v1/auth.py` |
| `GET /auth/github/callback` | Done | same |
| `POST /auth/logout` · `/logout-all` | Done | same |
| `GET /api/v1/me` | Done | same |
| `GET /auth/session` (always-200 boot probe) | Done | same |
| Secure / HttpOnly / SameSite cookies | Done | `auth/cookies.py` |
| Absolute expiry + separate idle timeout | Done | `session_ttl_seconds`, `session_idle_timeout_seconds` |
| Token rotation on login | Done | `rotate_session_token`, called in the callback |
| CSRF, session-bound | Done | `auth/tokens.py`, `api/authn.py` |
| `ViewerScope` derived from the session | Done | `api/scope.py` |
| Production fails closed | Done | `core/config.py` |
| Frontend sign-in / sign-out | Done | `hooks/useAuth.tsx`, `components/auth/AuthGate.tsx` |

**None of the forbidden substitutes is present.** No hard-coded user, no static
dev token, no `X-User-ID` header, no client-supplied installation id, no user id
in a query parameter, no frontend-only authorization, no hidden form field, no
`localStorage` identity. Asserted, not merely intended:
`test_authorization_boundary.py::test_no_query_builder_accepts_an_installation_id_from_the_caller`
and `credentials.test.ts` (14 cases).

**No GitHub access token reaches the browser.** The user access token is used
for `/user` and `/user/installations` and discarded — never persisted, logged,
cookied or returned. `test_the_oauth_client_never_persists_a_user_token`.

### Authorization tests (§10)

| Scenario | Result |
|---|---|
| User A's scope vs User B's | Disjoint; same SQL shape, different bound parameter |
| Direct-ID attack on another tenant's repository / PR / run / finding | Scope predicate still applied; route 404s |
| Cross-tenant response code | 404 everywhere — AST scan asserts no dashboard route can emit 403 |
| User with no installations | `FALSE` predicate on all 4 list builders — sees nothing, not everything |
| `repository_id` filter for an out-of-scope repository | ANDed with the scope; cannot widen |
| Unauthenticated, sign-in available | 401 |
| Unauthenticated, sign-in unconfigured | 503 |
| Unscoped child queries (`pull_request_runs`, `review_run_changed_files`, `finding_events`) | AST scan proves each is preceded by a scoped parent query and a 404 guard |

---

## 3. Security hardening

| Area | Done |
|---|---|
| CORS | Single origin from `FRONTEND_URL`; credentials on; `Authorization` removed from `allow_headers` (nothing sends it); `X-CSRF-Token` added; `X-Request-ID` exposed |
| CSRF | Double-submit, HMAC-bound to the session; enforced on every unsafe method by a dependency every route already has |
| Trusted host | `TrustedHostMiddleware`; `*` refused in production |
| Trusted proxy | `ProxyHeadersMiddleware` by hop count, counted from the right; `0` ignores the headers entirely |
| Security headers | CSP (`default-src 'none'`, `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'none'`), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store`, conditional HSTS |
| Body limits | Pure-ASGI, pre-buffer, per-path |
| Upstream limits | Streaming ceiling on GitHub responses |
| Rate limiting | Per-process fixed windows, three buckets, fails open, probes exempt — **limitations documented in three places** |
| Webhook | Size cap before HMAC (now actually true), constant-time compare, replay index, coalesced deliveries audited |
| Error handling | RFC 9457; no stack trace, SQL, path or upstream body reaches a client |
| Logging | Redaction after traceback rendering; recursion through lists; credential-pattern scrubbing; SQL-parameter removal |
| Secrets | Never logged, committed, imaged or sent to the frontend |
| Docker | Non-root both stages; runtime deps only; digest-pinnable base; `.dockerignore`; healthcheck; no baked secrets |
| Runtime | `--read-only`, `--cap-drop ALL`, `no-new-privileges`, resource limits, `stop_grace_period` |
| Database | Three-role least privilege; app role has DML only |
| Config | Eight-check production validator; no fallback session secret |

Full threat model, including **residual risk per attacker**, in
`docs/security-model.md` §10.

---

## 4. Real integrations

**Nothing in this table is claimed to work.** The rule for this phase was
"never claim a live integration works unless it was actually exercised", and
none was.

| Integration | Credentials present | Live call made | Status | What that means |
|---|---|---|---|---|
| GitHub App auth (JWT → installation token) | **No** | **No** | **NOT VERIFIED** | RS256 signing is unit-tested with a throwaway key; the exchange is mocked. Whether GitHub accepts our JWT is unknown. |
| GitHub webhook delivery | **No** | **No** | **NOT VERIFIED** | HMAC verified against locally-computed signatures and a replay harness that imports production signing code. No real delivery received. |
| GitHub PR sync | **No** | **No** | **NOT VERIFIED** | Driven by `MockTransport` against documented response shapes. |
| GitHub review publication | **No** | **No** | **NOT VERIFIED** | Publisher logic, 422 salvage and ambiguous-write reconciliation are unit-tested. No comment has been posted to any repository. |
| GitHub OAuth (sign-in) | **No** | **No** | **NOT VERIFIED** | Flow, state handling and session issue are unit-tested; SQL verified against real PostgreSQL. No human has signed in. |
| Gemini | **No** | **No** | **NOT VERIFIED** | Request shape, schema coercion and error mapping unit-tested against `MockTransport`. No model has been called. |
| PostgreSQL | **Yes** (local 16.13) | **Yes** | **OPERATIONAL** | 103 checks executed against a real server, including all 5 migrations and the Phase 5 auth SQL. |

Per §16, **no experimental comment was published to any repository**, and there
is no dedicated test repository because there are no credentials to use one
with.

---

## 5. Test execution

Counted two ways, because either number alone misleads. **Functions** is what
was written; **cases** is what actually ran, after parametrisation expands.

| Suite | Functions defined | Functions executed | Cases run | Passed | Failed | Blocked | Environment |
|---|---|---|---|---|---|---|---|
| `tests/unit/` | 160 | 160 | 196 | 196 | 0 | 0 | Python 3.12 + minimal runner |
| `tests/analysis/` | 114 | 114 | 174 | 174 | 0 | 0 | same |
| `tests/review/` | 124 | 124 | 144 | 144 | 0 | 0 | same + `httpx.MockTransport` |
| `tests/dashboard/` | 83 | 83 | 132 | 132 | 0 | 0 | same |
| `tests/auth/` **(new)** | 30 | 30 | 40 | 40 | 0 | 0 | same |
| `tests/api/` | 28 | **0** | 0 | — | — | **28** | needs FastAPI — **not installable** |
| `tests/integration/` | 44 | **0** | 0 | — | — | **44** | needs SQLAlchemy + asyncpg — **not installable** |
| **Backend total** | **583** | **511** | **686** | **686** | **0** | **72** | |
| Frontend (4 files) | 61 | 61 | 61 | 61 | 0 | 0 | Node 22.22.2, compiled by `tsc` |

**The 72 blocked functions are blocked, not passing.** They are counted in
"Functions defined" so the gap stays visible rather than hidden behind a
denominator that excludes them. 511 of 583 functions executed: **87.7%**.

### Real-PostgreSQL verification (16.13, port 55433)

| Harness | Checks | Result |
|---|---|---|
| `verify_phase3_db.py` | 20 | 20 passed |
| `verify_dashboard_sql.py` | 55 | 55 passed |
| `verify_auth_sql.py` **(new)** | 28 | 28 passed |
| **Total** | **103** | **103 passed, 0 failed** |

`verify_auth_sql.py` extracts each `text(...)` literal from
`app/db/repositories/auth.py` via AST, so the statement executed is the
statement the application runs. **Stated limitation:** parameters are
substituted as literals by the harness, because asyncpg is unavailable —
SQLAlchemy's binding is therefore *not* verified here and is exercised only in
CI.

What it proves, concretely: a renamed GitHub login updates the same user rather
than creating a second; a recycled handle is a different user; a null email does
not erase a stored one; revoked access disappears on the next sync; an
uninstalled App grants nothing; an expired *or* idle session resolves to
nothing; `touch` advances idleness but not absolute expiry; the pre-rotation
token stops working; logout takes effect immediately; a replayed OAuth state is
refused; deleting a user cascades to their sessions.

---

## 6. Build verification

Each tool reported separately, because "the build passed" is the claim this
phase exists to stop making.

| Tool | Status | Detail |
|---|---|---|
| **Python syntax** | **PASS** | 0 errors across 147 files (`ast.parse`) |
| **pytest** | **BLOCKED** | Not installable (PyPI 403). A minimal runner with two shims (`structlog`, `pytest`) executed 511 of 583 functions. **No production module is shimmed.** |
| **ruff** | **BLOCKED** | Not installable |
| **mypy** | **BLOCKED** | Not installable |
| **Alembic (upgrade)** | **PASS** (equivalent) | Alembic not installable; `ddl_render.py` executes the real migration modules and emits DDL, applied to real PostgreSQL → 14 tables, 61 indexes |
| **Alembic (downgrade → re-upgrade)** | **PASS** (equivalent) | downgrade → **0 tables**; re-upgrade → 14 tables; 0 errors |
| **PostgreSQL 16.13** | **PASS** | 103 checks executed |
| **SQLAlchemy / asyncpg** | **BLOCKED** | Not installable. Query builders are pure, so the SQL was verified without them; ORM mapping and parameter binding were not. |
| **FastAPI** | **BLOCKED** | Not installable. Route wiring verified structurally (AST scan that every dashboard route declares `ScopeDep`); no request was served. |
| **npm ci** | **BLOCKED** | npm 403 |
| **npm run build (Vite)** | **BLOCKED** | npm 403 |
| **TypeScript** | **PASS** | `tsc` 0 errors across 45 files, against hand-written ambient stubs for React and react-router. **The stubs are mine, not the real types** — this catches wrong argument counts and wrong types at call sites, not everything real `@types/react` would. |
| **Frontend tests** | **PASS** | 61 cases, 4 files, Node 22 |
| **Docker build** | **BLOCKED** | Docker unavailable here. A CI job asserts non-root, no dev toolchain, no `.env`/`.git` in the image. |
| **Browser verification** | **BLOCKED** | No browser and no running server (FastAPI unavailable) |
| **Secret scan** | **PASS** | No private key material; no `ghp_`/`AIza`/`sk-ant-` patterns in `app/` or `src/` |
| **Fake-data scan** | **PASS** | No `Math.random`, faker, lorem ipsum or placeholder constants in production code |

---

## 7. Production readiness

**This is deployable-shaped, not production-proven.** Both halves matter.

**Ready:** authentication and authorization implemented and tested; production
fails closed on eight distinct misconfigurations; secrets never logged,
committed or imaged; container hardened and non-root; database least privilege
scripted; migrations reversible and verified; structured logging with real
redaction; graceful shutdown; liveness/readiness separated; backup and restore
procedures written with a drill; rate limiting present and honestly labelled;
documentation covers setup, deployment, rotation, troubleshooting and a
checklist.

**Not ready, and why:**

1. **No integration has been proven live.** The first real deployment is also
   the first test of every GitHub and Gemini code path. Expect to find things.
2. **The ASGI stack has never been executed.** FastAPI is not installable here,
   so no route has served a request in this environment. Middleware ordering is
   argued in a docstring and verified structurally, not observed.
3. **Session handling has never been exercised end to end.** The SQL is
   verified against real PostgreSQL; the HTTP flow around it is not.
4. **The frontend has never been built or rendered.** No bundle, no browser.
5. **No load or soak testing.** §45's performance targets are unverified; the
   `EXPLAIN` evidence says queries use indexes, which is not the same as
   knowing latency under concurrency.
6. **Rate limiting is per-process.** Adequate for one or two processes;
   insufficient as a real quota.

Before a first real deployment, run the "Verify by using it" block in
`docs/deployment.md` §10. It is the only part that distinguishes a deployment
that works from one that starts.

---

## 8. Security invariants

Twenty invariants. **PASS** means verified by an executed test or command in
this environment; **NOT VERIFIED** means the control exists in code and could
not be exercised. The full table with enforcement locations is in
`docs/security-model.md` §11.

| | Invariant | Status |
|---|---|---|
| 1 | No credential logged, at any depth, including inside a rendered traceback | **PASS** |
| 2 | No credential committed | **PASS** |
| 3 | No AI key reaches the frontend | **PASS** |
| 4 | Repository code is never executed | **PASS** |
| 5 | Repositories never cloned; whole repos never sent to an AI provider | **PASS** |
| 6 | Webhook signature verified on raw bytes, constant-time, before parsing | **PASS** |
| 7 | Missing webhook secret fails closed | **PASS** |
| 8 | Webhook bodies size-capped before buffering | **PASS** |
| 9 | No AI finding published without passing every structural gate | **PASS** |
| 10 | Every dashboard query applies a `ViewerScope` in SQL | **PASS** |
| 11 | A caller cannot supply an installation id to widen scope | **PASS** |
| 12 | An empty scope returns nothing, not everything | **PASS** |
| 13 | A cross-tenant id returns 404, not 403 | **PASS** |
| 14 | Production refuses to start when not secure | **PASS** |
| 15 | No fallback session secret constant exists | **PASS** |
| 16 | Session cookie is HttpOnly; no token in browser storage | **PASS** |
| 17 | State-changing requests require a session-bound CSRF token | **PASS** |
| 18 | `X-Forwarded-For` counted from the right, by trusted hop count | **PASS** |
| 19 | Installation token only sent to the configured GitHub host | **PASS** |
| 20 | Runtime container is non-root with no dev toolchain | **NOT VERIFIED** — Docker unavailable here; Dockerfile sets it, CI asserts it |

**19 PASS · 1 NOT VERIFIED · 0 FAIL.** The one NOT VERIFIED stays NOT VERIFIED.

---

## 9. Deployment architecture and commands

```
              ┌──────────────┐
  browser ───►│ reverse proxy│  TLS terminates here
              │  (TLS, WAF)  │  TRUSTED_PROXY_COUNT=1
              └──────┬───────┘
                     │  X-Forwarded-For / -Proto
       ┌─────────────┴─────────────┐
       ▼                           ▼
  ┌─────────┐                 ┌─────────┐
  │   api   │  same image     │ worker  │
  │ uvicorn │◄───────────────►│ python  │
  └────┬────┘                 └────┬────┘
       │                           │
       └───────────┬───────────────┘
                   ▼
           ┌───────────────┐
           │ PostgreSQL 16 │  data + durable job queue
           └───────────────┘
```

No Redis, no broker, no orchestrator required. Two processes, one image — which
is what makes "the worker is running different code" structurally impossible.

```bash
# 1. build, with a pinned base
docker build --target runtime \
  --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:<digest> \
  -t codeguard-ai:<tag> ./backend

# 2. migrate — a release command, never on startup
docker run --rm --env-file <secrets> codeguard-ai:<tag> alembic upgrade head

# 3. least privilege — once, after the first migration
psql "$SUPERUSER_URL" -f infrastructure/sql/least_privilege.sql

# 4. run
docker run -d --name codeguard-api --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --env-file <secrets> codeguard-ai:<tag>

docker run -d --name codeguard-worker --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --stop-timeout 150 \
  --env-file <secrets> codeguard-ai:<tag> python -m app.worker
```

`docker-compose.prod.yml` encodes the same topology with the hardening flags
set, and its header is explicit about what it does not provide.

Full variable table (Variable · Required · Secret · Production default ·
Purpose): `docs/deployment.md` §3.

---

## 10. Known limitations

**Verification**

1. No live GitHub or Gemini call has ever been made from this codebase.
2. FastAPI, SQLAlchemy, asyncpg, pytest, ruff, mypy, React, Vite and Vitest
   cannot be installed (PyPI/npm 403). 72 test functions are blocked.
3. The TypeScript check runs against hand-written React stubs, not real types.
4. No Docker build, no browser rendering, no load test has been executed.
5. `verify_auth_sql.py` inlines parameters as literals; SQLAlchemy's binding is
   unverified.

**Design, accepted deliberately**

6. Access revocation takes up to one refresh interval (architecture.md §16.3).
   Bounded and observable via `access_synced_at`.
7. Rate limiting is per-process and approximate.
8. `/health` reports no integration above `configured`, and never will until
   real operation outcomes are recorded. This is honest, and it is also less
   useful than a verified status would be.
9. The App private key lives in process memory. Bounding that properly needs a
   KMS or a separate signing service.
10. Dashboard mutations (`PATCH /findings/{id}`, `POST /re-review`, settings
    writes) are still not implemented. There are users now; the endpoints are
    not.
11. A structurally valid but semantically wrong finding is undetectable by this
    system. The dashboard says so rather than implying otherwise.
12. No lockfile hash pinning for Python, and no SBOM.
13. `pip-audit` and the Docker assertions are written into CI but have never
    run, because CI has never run.

**Operational**

14. Single-region, single-database. No replica, no failover.
15. No metrics endpoint. Observability is structured logs plus health endpoints.
16. Backup and restore are documented and scripted; no drill has been performed
    against a real deployment, because there is no real deployment.

---

## 11. Exit criteria

| # | Criterion | Status | Note |
|---|---|---|---|
| 1 | Full Phase 0–4 audit completed | **Met** | 17 defects found, all fixed |
| 2 | Dependency/execution matrix built from facts | **Met** | §5, §6 |
| 3 | All identified auth-bypass issues fixed | **Met** | C1, plus the scope wiring |
| 4 | `users` table implemented | **Met** | |
| 5 | Sessions implemented with revocation | **Met** | |
| 6 | `user_installation_access` implemented | **Met** | |
| 7 | GitHub OAuth login flow | **Met** | Code complete; **never exercised live** |
| 8 | Logout invalidates the session server-side | **Met** | |
| 9 | Session cookies Secure/HttpOnly/SameSite | **Met** | |
| 10 | CSRF protection independent of CORS | **Met** | |
| 11 | `ViewerScope` derived from the session | **Met** | |
| 12 | Production fails closed without auth | **Met** | Eight checks + CI |
| 13 | User A cannot read User B's data | **Met** | 8 scenarios |
| 14 | Direct-ID attack returns 404 | **Met** | AST-asserted no route can 403 |
| 15 | Real GitHub App integration verified | **NOT MET** | No credentials |
| 16 | Real webhook delivery verified | **NOT MET** | No credentials |
| 17 | Real Gemini call verified | **NOT MET** | No credentials |
| 18 | Review published to a test repository | **NOT MET** | No credentials |
| 19 | Provider abstraction preserved | **Met** | Invariant tests still pass |
| 20 | Real pytest run | **NOT MET** | Not installable; 511/583 executed by a minimal runner |
| 21 | Real Alembic upgrade/downgrade/upgrade | **Partial** | DDL rendered from the real migrations and applied to real PostgreSQL; Alembic itself not installable |
| 22 | Real FastAPI route tests | **NOT MET** | Not installable |
| 23 | `npm ci` + `npm run build` | **NOT MET** | npm 403 |
| 24 | Browser verification | **NOT MET** | No browser, no server |
| 25 | CORS restricted, credentials-safe | **Met** | |
| 26 | Security headers present | **Met** | Incl. conditional HSTS |
| 27 | Trusted host and proxy configured | **Met** | |
| 28 | Webhook hardening | **Met** | Size cap now genuinely pre-buffer |
| 29 | Database least privilege | **Met** | Scripted with a verification step |
| 30 | Secret management documented, incl. rotation | **Met** | `deployment.md` §7 |
| 31 | Docker hardened | **Met** | Non-root, runtime deps only, pinnable, `.dockerignore` |
| 32 | Production config separated and fail-closed | **Met** | |
| 33 | Structured logging with redaction | **Met** | Ordering defect fixed |
| 34 | Rate limiting | **Met** | With documented limitations |
| 35 | Graceful shutdown and worker reliability | **Met** | Drain + fence + abandoned-run sweep |

**28 met · 1 partial · 6 not met.** Every "not met" is blocked by an absent
credential or an uninstallable dependency, both of which are environment facts
rather than work left undone — and neither is converted into a pass.

---

## 12. Recommended Phase 6 (not implemented)

In the order a real deployment would need them.

1. **Exercise every integration against real credentials.** A GitHub App on a
   dedicated test repository, a Gemini key, a real OAuth sign-in. Record the
   outcomes so `/health` can report `authenticated` and `operational` with
   evidence. This is the largest gap and everything else is smaller than it.
2. **Run the real stack.** Install FastAPI, SQLAlchemy, asyncpg, pytest, ruff
   and mypy; execute the 72 blocked tests; run `npm ci` and `npm run build`;
   render the dashboard in a browser. Several claims in this report would
   become observations.
3. **Dashboard mutations.** `PATCH /findings/{id}` with a `finding_events` row,
   `POST /pull-requests/{id}/re-review`, per-repository settings. The
   authorization boundary they need now exists.
4. **A metrics endpoint and alerting.** Prometheus exposition; alerts on the
   conditions listed in `docs/deployment.md` §8.
5. **Failure injection.** Kill a worker mid-review; drop the database under
   load; make GitHub return 500s and 429s. The recovery paths are written and
   none has been watched.
6. **Load and soak testing.** Establish the latency numbers §45 asks for, under
   concurrency, against realistic data volumes.
7. **Distributed rate limiting**, if more than a couple of API processes.
8. **Supply chain.** Hash-pinned lockfiles, an SBOM, signed images.
9. **Key management.** Move the App private key to a KMS or a signing service
   so a compromised process cannot mint installation tokens.
10. **A semantic benchmark** — a labelled dataset and a stated methodology —
    which is the only thing that would let this product publish an accuracy
    figure. Until it exists, it publishes none.
