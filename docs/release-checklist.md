# Release checklist

Two lists. The first is what Phase 6 actually did and can be re-run. The second
is what someone must do before this system touches a real repository, because
none of it could be done here.

**Version:** 0.6.1 · **Classification:** DEPLOYABLE-SHAPED / NOT PRODUCTION-VERIFIED

---

## A. Verified in this release — re-runnable

Every box below was ticked by running the command, not by reading the code.

### Code quality

- [x] `ruff check app tests` — 0 violations
- [x] `ruff format --check app tests` — 159 files already formatted
- [x] `mypy app --ignore-missing-imports` — 9 errors, all `unused-ignore` on
      tree-sitter stubs absent from this environment, individually reviewed
- [x] `npx tsc --noEmit` (frontend, strict + `noUncheckedIndexedAccess` +
      `exactOptionalPropertyTypes`) — 0 errors

### Tests

- [x] `pytest tests/ --ignore=tests/api --ignore=tests/integration` — **871 passed**
- [x] Frontend pure-logic tests compiled with real `tsc`, run under real Node via
      the repository's own runner (`npm run test`) — **67 passed**
- [x] Every Phase 6 **defect** regression fails against the Phase 5 tree and
      passes against this one — 11 of 11, verified by running the file against
      the extracted Phase 5 tarball. (The file's 12th test pins the §27 scope
      decision rather than a defect, and correctly passes on both trees.)

### Database — real PostgreSQL 16

- [x] `python3 backend/verification/run_all.py` — **170 passed, 0 failed**
- [x] Migrations upgrade → exercise → downgrade to zero tables → upgrade → exercise
- [x] Schema parity in **both** directions: every migration column has a model
      column and every model column has a migration column, plus `server_default`
      parity, so autogenerate would propose nothing
- [x] Queue and review-run races driven by genuinely concurrent backends

### Security

- [x] Zero `eval` / `exec` / `compile` / `__import__` / shell invocations in the
      application (asserted, not assumed)
- [x] No credential appears in any log statement, health response, error body or
      `safe_summary`
- [x] 113 adversarial tests against the AI → GitHub path
- [x] Authorization boundary exercised in SQL: cross-tenant ids return 404
- [x] `docs/final-security-review.md` written, with every finding classified

### Configuration

- [x] Production refuses to start with a development secret, a wildcard CORS
      origin, or `DASHBOARD_ALLOW_UNAUTHENTICATED_READS` enabled — 9 unsafe
      configurations refused, verified
- [x] Every configured limit is read by something — a standing test
- [x] Every setting the application reads is documented — in `.env.example`
      (47 keys an operator sets) or `docs/deployment.md` (the tuning knobs).
      Verified by a standing test, not by inspection. This began as an
      overclaim: the checklist said `.env.example` listed *every* key, and it
      listed 47 of 68. Measuring it found 16 settings documented nowhere —
      including `REVIEW_PUBLISH_ENABLED`, **the kill switch for writing to
      GitHub**, which an operator needing to pause publication had no documented
      way to find. Now documented.
- [x] `.env.example` contains no stale key (every key is a real setting) and no
      real secret value

---

## B. Not verified here — required before production

Each of these is **BLOCKED** in this environment for the reason given. None may
be ticked from a code reading.

### Dependencies — BLOCKED: PyPI and npm return 403

- [ ] `pip install -r requirements.txt` succeeds with real fastapi, sqlalchemy,
      alembic, asyncpg, structlog, pytest-asyncio
- [ ] `npm ci` succeeds and the frontend builds
- [ ] `pytest tests/api tests/integration` — **72 test functions have never run**
- [ ] Frontend component tests under Vitest — have never run
- [ ] Re-check the 9 residual mypy errors once tree-sitter stubs are installable

### Container — BLOCKED: Docker daemon unavailable

- [ ] `docker build` succeeds
- [ ] The image runs as a non-root user
- [ ] One image, two processes: `api` and `worker` both start
- [ ] `docker compose -f docker-compose.prod.yml up` reaches a healthy state
- [ ] `/healthz` and `/readyz` behave correctly under an orchestrator, including
      a database blip (liveness must **not** restart the container)

### Database migration against the real tooling — BLOCKED: alembic unavailable

- [ ] `alembic upgrade head` against a fresh database
- [ ] `alembic revision --autogenerate` proposes **no** changes. If it proposes
      any, fix the underlying model/migration mismatch — do not commit the
      generated file.

### GitHub — BLOCKED: credentials unavailable

- [ ] GitHub App created; App id, private key and webhook secret configured
- [ ] Webhook endpoint reachable, and a real delivery verifies its signature
- [ ] An installation token is minted from the RS256 JWT
- [ ] A pull-request event drives a complete review
- [ ] A review comment appears on the right line of the right commit
      — **use a dedicated test repository only. Never publish a test comment to
      an unrelated user's repository.**
- [ ] A second push during a review aborts publication (stale head)
- [ ] Re-running a review posts nothing new (marker idempotency)
- [ ] Uninstalling the App stops reviews and does not delete history

### AI provider — BLOCKED: credentials unavailable

- [ ] `GEMINI_API_KEY` and model configured
- [ ] A real request returns a parseable structured response
- [ ] A malformed response is classified, not crashed on
- [ ] Rate-limit and quota responses are handled with the server-directed delay
- [ ] Confirm the free-tier ceiling: one provider call per review, enforced

### Operational

- [ ] Secrets in a real secret manager, not a `.env` file on disk
- [ ] TLS terminated, `TRUSTED_PROXY_COUNT` set to the real hop count
- [ ] `TRUSTED_HOSTS` set to the real host names, never left at `*`
- [ ] `FRONTEND_URL` set to the real dashboard origin over https. **There is no
      `CORS_ORIGINS` variable** — an earlier draft of this checklist named one,
      and it does not exist. The allowed origin is derived:
      `FRONTEND_URL` → `Settings.cors_origins` → the single entry passed to
      `CORSMiddleware`. A wildcard is impossible to configure by accident, which
      is the point: `allow_origins=["*"]` with credentials is what the derivation
      exists to prevent.
- [ ] `SESSION_SECRET` generated with a CSPRNG, at least 32 bytes
- [ ] `DASHBOARD_ALLOW_UNAUTHENTICATED_READS` is **off** — confirm in the running
      process, not only in the file
- [ ] Database backups configured and a restore tested
- [ ] Log aggregation confirmed to contain no credentials in practice
- [ ] Alerting on worker liveness, job dead-letter depth and review failure rate
- [ ] A rollback plan that does not require a database downgrade

### Honesty gates — check these last, and check them in the running system

- [ ] The dashboard does not display "GitHub Connected", "Gemini Connected",
      "Production Ready" or "All Systems Operational" anywhere. A configured
      credential is not a working integration, and the integration health model
      distinguishes `configured` from `authenticated` from `operational` for
      exactly this reason.
- [ ] No accuracy, precision, recall or false-positive-rate figure is displayed.
      There is no labelled benchmark dataset, so there is no valid methodology
      for any of them.
- [ ] Every integration still reports `configured` rather than `operational`
      until a real operation of that kind has succeeded.

---

## C. Release classification

Tick A in full and this is what it is: a system whose own logic has been
executed and verified, and whose every external boundary has not.

**DEPLOYABLE-SHAPED / NOT PRODUCTION-VERIFIED.**

It becomes production-verified when section B is complete — not before, and not
by re-reading section A.
