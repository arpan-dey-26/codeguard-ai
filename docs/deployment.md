# Deployment

How to run CodeGuard AI somewhere real, and what it refuses to do if you get it
wrong.

**Read this first:** production **will not start** unless it is configured
safely. That is deliberate. A misconfiguration that stops a deploy is an
inconvenience; the same misconfiguration that starts and serves is an incident.
Every refusal names the variable and says what to do.

---

## 1. What you need

| | Why |
|---|---|
| PostgreSQL 16+ | The design uses `FOR UPDATE SKIP LOCKED`, partial indexes, JSONB and `TIMESTAMPTZ`. SQLite has none of them. |
| A GitHub App | Both its private key (to act on repositories) *and* its OAuth credentials (to authenticate people). |
| A Gemini API key | Optional. Without it, reviews fail with `ai_unavailable` and everything else works. |
| TLS termination | A reverse proxy, a load balancer or a platform. The application does not terminate TLS. |
| A place to run two processes | The API and the worker, from one image. |

Deliberately **not** needed: Redis, Celery, a message broker, Kubernetes, a
second datastore. PostgreSQL is the job queue.

---

## 2. Creating the GitHub App

GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.

**Permissions** — repository:

| Permission | Level | What needs it |
|---|---|---|
| Contents | Read-only | Fetching file content at a commit |
| Pull requests | **Read & write** | Reading the diff; posting review comments |
| Metadata | Read-only | Mandatory |

Nothing else. Each extra permission is one more thing an installation token can
do if it leaks.

**Subscribe to events:** Pull request, Installation, Installation repositories.

**Webhook URL:** `https://<your-api-host>/api/v1/webhooks/github`
**Webhook secret:** generate one — `openssl rand -hex 32` — and set
`GITHUB_WEBHOOK_SECRET` to the same value. A receiver with no secret rejects
everything, which is correct but looks like a broken integration.

**Callback URL:** exactly `<PUBLIC_BASE_URL>/api/v1/auth/github/callback`.
This must match character for character, including the scheme and any trailing
path. GitHub compares it to what we send, and a mismatch presents as a generic
"redirect_uri mismatch" with no further detail.

Leave **"Request user authorization (OAuth) during installation"** unchecked
and **"Expire user authorization tokens"** as you like — the user token is used
twice and discarded either way.

Then: generate a private key (downloads a `.pem`), and note the **Client ID**
and generate a **Client secret**. Those two are `GITHUB_OAUTH_CLIENT_ID` and
`GITHUB_OAUTH_CLIENT_SECRET` — they are *not* the App ID and private key, and
the two pairs do different jobs (see `docs/security-model.md` §2).

---

## 3. Environment variables

**Required** means the process refuses to start without it outside development.
**Secret** means it must come from a secret store, never from a repository, a
build argument or an image layer.

### Application

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `APP_ENV` | yes | no | — (set `production`) | Selects the guardrails. Anything other than `development`/`test` is production-grade. |
| `DEBUG` | no | no | `false` | Must be false; refused otherwise. |
| `LOG_LEVEL` | no | no | `INFO` | `DEBUG` here is a privacy decision, not just a volume one. |
| `APP_VERSION` | no | no | `0.1.0` | Reported by `/health`. |

### HTTP and proxying

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `FRONTEND_URL` | yes | no | — | The single allowed browser origin. Becomes the CORS allow-list. Must be https. |
| `PUBLIC_BASE_URL` | yes | no | — | This API's external origin. The OAuth redirect URI is built from it, never from the request. Must be https. |
| `TRUSTED_HOSTS` | yes | no | — | Comma-separated `Host` values. `*` is refused. |
| `TRUSTED_PROXY_COUNT` | no | no | `0` | Number of proxies in front. **Set this** behind a load balancer, or client addresses are the proxy's. |

`TRUSTED_PROXY_COUNT` is the one people get wrong in both directions. Zero
behind a proxy means every client looks like the proxy, so rate limiting is
global and session records are useless. Too high means trusting entries the
client wrote. It is the number of hops **you control**, counted from this
process outwards.

### Database

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `DATABASE_URL` | yes | **yes** | — | Must use `postgresql+asyncpg://`. The development default is refused by name. |
| `DB_POOL_SIZE` | no | no | `5` | Per process. Multiply by process count against the server's `max_connections`. |
| `DB_MAX_OVERFLOW` | no | no | `10` | |

### Authentication

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `SESSION_SECRET` | **yes** | **yes** | — | HMAC key for sessions and CSRF. ≥32 chars. No fallback exists. |
| `GITHUB_OAUTH_CLIENT_ID` | yes | no | — | Identifies the App to the OAuth endpoint. |
| `GITHUB_OAUTH_CLIENT_SECRET` | yes | **yes** | — | Completes the code exchange. |
| `SESSION_TTL_SECONDS` | no | no | `28800` | Absolute lifetime; never extended. |
| `SESSION_IDLE_TIMEOUT_SECONDS` | no | no | `7200` | Separate from the absolute expiry. |
| `SESSION_COOKIE_NAME` | no | no | `codeguard_session` | |
| `SESSION_COOKIE_DOMAIN` | no | no | unset | Set only to share a cookie across subdomains. |
| `SESSION_COOKIE_SECURE` | no | no | unset → `true` | Unset decides from the environment. |
| `SESSION_COOKIE_SAMESITE` | no | no | `lax` | `strict` **breaks the OAuth return**. |
| `OAUTH_STATE_TTL_SECONDS` | no | no | `600` | How long a login may take. |

### GitHub App

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `GITHUB_APP_ID` | for reviews | no | — | |
| `GITHUB_PRIVATE_KEY` | for reviews | **yes** | — | PEM, single-line with `\n`, or base64 — detected by content. |
| `GITHUB_WEBHOOK_SECRET` | for reviews | **yes** | — | Without it every delivery is rejected. |
| `GITHUB_API_BASE_URL` | no | no | `https://api.github.com` | Change for GHES. |
| `GITHUB_WEBHOOK_MAX_BODY_BYTES` | no | no | `26214400` | |

### AI provider

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `AI_PROVIDER` | no | no | `gemini` | |
| `GEMINI_API_KEY` | for reviews | **yes** | — | Sent in a header, never a query string. |
| `GEMINI_MODEL` | for reviews | no | — | No default on purpose; pick one against current documentation. |
| `AI_MAX_REQUESTS_PER_REVIEW` | no | no | `1` | Free-tier guard: a review that tries to exceed it fails with `ai_budget_exceeded` rather than making the call. A failed provider call still spends budget, because a provider failing repeatedly is what the ceiling is for. **Enforced since v0.6.0** — before that this row described a limit that nothing read. |
| `GEMINI_MODEL` and the rest of the provider knobs below are tuning, not credentials. |||||
| `GEMINI_API_BASE_URL` | no | no | `https://generativelanguage.googleapis.com` | Change only for a proxy you control. |
| `GEMINI_TIMEOUT_SECONDS` | no | no | `90.0` | |
| `GEMINI_TEMPERATURE` | no | no | see config | Lower is more deterministic. |
| `GEMINI_MAX_OUTPUT_TOKENS` | no | no | `8192` | |

### Review budgets and the publication kill switch

Added to this document in v0.6.0. These were readable only in
`app/core/config.py`, which meant the **kill switch below was undocumented** —
an operator who needed to stop CodeGuard writing to GitHub had no documented way
to find it. That is now fixed.

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `REVIEW_PUBLISH_ENABLED` | no | no | `true` | **The kill switch.** `false` runs the whole pipeline — analysis, model call, validation, persistence — and stops before writing to GitHub. Use this to pause publication without stopping reviews or losing data. |
| `REVIEW_MAX_INLINE_COMMENTS` | no | no | `20` | Findings past the cap are demoted to the summary, never dropped. |
| `REVIEW_MIN_CONFIDENCE` | no | no | `0.6` | Publication threshold. |
| `REVIEW_MIN_CONFIDENCE_HIGH_SEVERITY` | no | no | `0.5` | Lower, deliberately: a probable critical issue is worth surfacing. |
| `AI_MAX_FINDINGS` | no | no | `25` | Surplus claims are recorded as `over_limit`, not discarded, so the funnel still sums. |
| `AI_MAX_FILES_PER_REVIEW` | no | no | `40` | |
| `AI_MAX_CHANGED_LINES_PER_REVIEW` | no | no | `3000` | |
| `AI_MAX_CONTEXT_BYTES` | no | no | `180000` | A real ceiling on the document sent to the provider, including its file manifest. Corrected in v0.6.0; before that the manifest was uncounted and a large pull request could exceed this by 20%. |
| `AI_MAX_ESTIMATED_INPUT_TOKENS` | no | no | `60000` | Pessimistic estimate, used to reject early rather than pay for a rejected request. |
| `AI_CONTEXT_LINES_AROUND_DECLARATION` | no | no | `8` | |
| `APP_NAME` | no | no | `CodeGuard AI` | |
| `DB_ECHO` | no | no | `false` | Never enable in production: it logs SQL. |

### Limits and reliability

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `RATE_LIMIT_ENABLED` | no | no | `true` | |
| `RATE_LIMIT_AUTH_PER_MINUTE` | no | no | `10` | Per process. See the caveat below. |
| `RATE_LIMIT_API_PER_MINUTE` | no | no | `120` | |
| `RATE_LIMIT_WEBHOOK_PER_MINUTE` | no | no | `600` | |
| `MAX_REQUEST_BODY_BYTES` | no | no | `1048576` | Non-webhook bodies. Enforced before buffering. |
| `GITHUB_MAX_RESPONSE_BYTES` | no | no | `41943040` | Upstream response ceiling, enforced while streaming. |
| `WORKER_SHUTDOWN_DRAIN_SECONDS` | no | no | `120` | **Must be less than the orchestrator's stop grace period.** |
| `JOB_VISIBILITY_TIMEOUT_SECONDS` | no | no | `900` | |
| `REVIEW_RUN_ABANDON_TIMEOUT_MULTIPLIER` | no | no | `3` | |

> **Rate limiting is per process and approximate.** Counters live in memory, so
> with N API processes the effective limit is N× the configured value, and a
> restart forgets them. It bounds a runaway client; it is not a precise global
> quota. Put a real limiter at the edge if you need one.

### Dashboard

| Variable | Required | Secret | Production default | Purpose |
|---|---|---|---|---|
| `DASHBOARD_ALLOW_UNAUTHENTICATED_READS` | no | no | `false` — **refused if true** | Phase 4 needed it. Phase 5 does not. |
| `DASHBOARD_MAX_PAGE_SIZE` | no | no | `100` | |

---

## 4. Deploying

### Build

```bash
# Pin the base image. Resolve the digest once, review it, record it.
docker buildx imagetools inspect python:3.12-slim-bookworm

docker build \
  --target runtime \
  --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:<digest> \
  -t codeguard-ai:$(git rev-parse --short HEAD) \
  ./backend
```

One image serves both processes. That is what makes "the worker is running
different code than the API" structurally impossible.

### Migrate, then start

Migrations run as a **release command**, before new instances take traffic:

```bash
docker run --rm --env-file <secrets> codeguard-ai:<tag> alembic upgrade head
```

Never on startup. Two booting instances would race each other, and a failed
migration would become a crash loop instead of a visible deploy failure.

### Run

```bash
# API
docker run -d --name codeguard-api \
  --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --env-file <secrets> \
  codeguard-ai:<tag>

# Worker — same image, different command
docker run -d --name codeguard-worker \
  --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --stop-timeout 150 \
  --env-file <secrets> \
  codeguard-ai:<tag> python -m app.worker
```

`--stop-timeout` must exceed `WORKER_SHUTDOWN_DRAIN_SECONDS`. Docker's default
is 10 seconds; a review job outlives that easily (the Gemini timeout alone is
90s), and a worker killed mid-job leaves that job `running` until the
visibility-timeout sweep reclaims it.

`docker-compose.prod.yml` shows the same topology with the hardening flags
already set. Read its header: it is a reference topology, and it is explicit
about what it does *not* provide.

### Reverse proxy

```nginx
location / {
    proxy_pass         http://codeguard-api:8000;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;

    # The webhook body can legitimately be large.
    client_max_body_size 25m;
}
```

Then set `TRUSTED_PROXY_COUNT=1`.

---

## 5. Database roles

> **The database in `docker-compose.yml` is not this.** (v0.6.1) That service
> runs a single superuser with a plain-text password from the file, no roles and
> no grants. It exists so the project starts on a laptop. It enforces no least
> privilege, and nothing about its presence in a Compose file makes it a
> production topology. `docker-compose.prod.yml` does ship a `db` service, and
> its own header is explicit that it is there "so the file runs" — that file is
> a reference topology. In a real deployment `DATABASE_URL` points at a managed
> database with backups that have been restored at least once (§6).
>
> The roles below are what a production database needs, and they are **not
> created for you by anything in this repository**. Someone has to run the
> script.

Run `infrastructure/sql/least_privilege.sql` once, as a superuser, **after**
the first migration. It creates three roles:

| Role | Can | Used by |
|---|---|---|
| `codeguard_migrate` | DDL; owns the schema | the migration step only |
| `codeguard_app` | `SELECT/INSERT/UPDATE/DELETE` | the API and the worker |
| `codeguard_readonly` | `SELECT`, minus session tables and `users.email` | investigation, BI |

Point `DATABASE_URL` at `codeguard_app`. The application issues no DDL — that
was verified by reading every statement in `app/db/repositories/`, not assumed —
so it loses nothing, and an injection or a compromised process can no longer
`DROP TABLE` or quietly `ALTER` away a constraint the code relies on.

Verify it applied, rather than assuming:

```sql
SET ROLE codeguard_app;
CREATE TABLE should_fail (id int);   -- expect: permission denied
RESET ROLE;
```

---

## 6. Backups and restore

**A backup you have never restored is a hypothesis.** Schedule the drill, not
just the dump.

```bash
# Nightly, encrypted at rest, retained per your policy.
pg_dump --format=custom --no-owner --no-privileges \
        "$DATABASE_URL" > codeguard-$(date +%F).dump
```

Restore drill — into a **scratch** database, never over a live one:

```bash
createdb codeguard_restore_test
pg_restore --dbname=codeguard_restore_test --no-owner codeguard-<date>.dump

# Then check the things that would actually matter:
psql codeguard_restore_test -c "SELECT version_num FROM alembic_version;"
psql codeguard_restore_test -c "SELECT count(*) FROM findings;"
psql codeguard_restore_test -c "SELECT count(*) FROM review_runs WHERE status='succeeded';"
```

What a restore does **not** bring back, and what to do about it:

- **Sessions.** Everyone signs in again. Harmless.
- **In-flight jobs.** Jobs restored as `running` are reclaimed by the
  visibility-timeout sweep. Jobs restored as `queued` run again — every handler
  is idempotent, so this is safe.
- **Secrets.** They were never in the database. Restore them from your secret
  store.

---

## 7. Rotating a secret

| Secret | How | Effect |
|---|---|---|
| `SESSION_SECRET` | Set a new value, restart | Every session invalidated; everyone signs in again. That is the intended effect of a rotation. |
| `GITHUB_WEBHOOK_SECRET` | Change it in the App settings **and** the environment, as close to simultaneously as you can | Deliveries in the gap are rejected. GitHub does not redeliver — check the App's delivery log afterwards and replay by hand if needed. |
| `GITHUB_PRIVATE_KEY` | Generate a new key on the App, deploy, then delete the old one from GitHub | Cached installation tokens (≤1h) keep working; new ones use the new key. |
| `GITHUB_OAUTH_CLIENT_SECRET` | Generate a new one, deploy, delete the old | In-flight logins in the gap fail and can simply be retried. |
| `GEMINI_API_KEY` | Replace, restart | Reviews in flight fail retryably and are rescheduled. |
| Database password | Rotate on the server, update `DATABASE_URL`, restart | Open connections survive until recycled. |

---

## 8. Operating it

### Probes

| Endpoint | Answers | Point your probe at |
|---|---|---|
| `/healthz` | Is the process alive? No dependency checks. | **liveness** |
| `/readyz` | Is the database reachable and migrated? | **readiness** |
| `/health` | The full report, for humans and the dashboard | neither |

Never point liveness at `/readyz`. A liveness probe that touches the database
restarts healthy containers during a database blip, turning a partial outage
into a total one.

### Reading `/health` honestly

`integrations[].state` uses a strict vocabulary:

```
not_configured → configured → reachable → authenticated → operational
```

**Every entry currently stops at `configured`, and `last_verified_at` is null.**
That is not a bug. This endpoint makes no live calls: a health check that
authenticates against GitHub and Gemini on every poll would burn rate limit on
a fixed schedule and make an upstream outage look like ours. `configured` means
*credentials are present*, nothing more. Do not render "Connected" for anything
below `authenticated`.

### Logs worth alerting on

| Event | Severity | Means |
|---|---|---|
| `webhook.signature.invalid` | warning | One is noise. A sustained stream is someone probing the secret. |
| `job.failed_unexpected` | error | An exception that is not one of ours — a bug. Should page. |
| `jobs.sweep.reclaimed` | warning | A worker died or the visibility timeout is shorter than real job duration. |
| `jobs.release.fenced_out` | warning | A worker reported on a job whose lock had been revoked. Sustained means the timeout needs raising. |
| `review_runs.abandoned` | warning | Runs closed by the sweep. Sustained means reviews are dying mid-flight. |
| `app.dashboard.unauthenticated_reads_enabled` | warning | Should never appear in production — the process would not have started. |
| `app.session.ephemeral_key` | warning | No `SESSION_SECRET`. Should never appear in production. |
| `github.request.untrusted_host` | error | A pagination link pointed off-host. Investigate. |
| `ratelimit.rejected` | warning | Expected occasionally; a flood means an abusive client or limits set too low. |

Every line is one JSON object on stdout, with a `correlation_id` that ties a
user-visible error to the server-side record.

---

## 9. Troubleshooting

**Sign-in returns "The sign-in attempt could not be verified."**
The `state` failed one of its two checks. Usually: `SESSION_COOKIE_SAMESITE` is
`strict` (it must be `lax` — the return from github.com is a cross-site
navigation), the cookie was dropped because `SESSION_COOKIE_SECURE` is true
over plain HTTP, or more than `OAUTH_STATE_TTL_SECONDS` elapsed. A back-button
retry of a completed login also lands here — correctly; state is single-use.

**GitHub says "redirect_uri mismatch".**
`PUBLIC_BASE_URL` and the App's callback URL disagree. They must match exactly,
including scheme.

**Signed in, but every page is empty.**
Most likely correct. You are signed in with no App installations — the account
menu says "No installations". Install the App on a repository, then sign out
and in again to refresh the access cache.

**Every write returns 403 "Request origin could not be verified".**
The CSRF header is missing or wrong. Check that the CSRF cookie is being set
(the session cookie name plus `_csrf`), that `X-CSRF-Token` is in the CORS
`allow_headers`, and that the SPA's origin matches `FRONTEND_URL` exactly —
scheme, host and port.

**The process will not start and prints a list.**
Working as intended. Every line names a variable and what is wrong with it.

**Webhooks arrive but nothing happens.**
Check `/health` → `github.webhook_secret_configured`. Then look for
`webhook.signature.invalid` (secret mismatch) or `webhook.delivery.ignored`
with a reason — draft pull requests and unsupported actions are recorded and
deliberately not queued.

**A review stays "Queued" forever.**
Is the worker running? `docker logs codeguard-worker` should show
`worker.startup.completed`. Then check `jobs` for a `dead` row: the job
exhausted its attempts and is waiting for a human.

**Reviews fail with `ai_unavailable`.**
`GEMINI_API_KEY` or `GEMINI_MODEL` is missing. `/health` shows
`ai_provider.configured`.

---

## 10. Production checklist

Tick each one **after checking it**, not after intending it.

**Secrets**
- [ ] `SESSION_SECRET` generated with `openssl rand -base64 48`, from a secret store
- [ ] No `.env` committed — CI enforces this
- [ ] `GITHUB_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_CLIENT_SECRET`, `GEMINI_API_KEY` all injected at run time, never baked
- [ ] Rotation procedure (§7) read by whoever will need it at 3am

**Configuration**
- [ ] `APP_ENV=production`, `DEBUG=false`
- [ ] `FRONTEND_URL` and `PUBLIC_BASE_URL` both https
- [ ] `TRUSTED_HOSTS` names real hosts, not `*`
- [ ] `TRUSTED_PROXY_COUNT` matches the actual topology
- [ ] `DASHBOARD_ALLOW_UNAUTHENTICATED_READS` unset or false
- [ ] The process starts — which proves all of the above

**GitHub App**
- [ ] Callback URL matches `PUBLIC_BASE_URL` exactly
- [ ] Webhook URL and secret match the deployment
- [ ] Permissions are Contents:read, Pull requests:write, Metadata:read — and nothing else
- [ ] A test delivery shows 202 in the App's delivery log

**Database**
- [ ] PostgreSQL 16+
- [ ] `alembic upgrade head` run as a release command
- [ ] `least_privilege.sql` applied; `codeguard_app` verified unable to `CREATE TABLE`
- [ ] Backups scheduled **and one restore drill completed**
- [ ] `DB_POOL_SIZE` × process count is within `max_connections`

**Runtime**
- [ ] Runtime image, not development
- [ ] Non-root confirmed: `docker run --rm <image> id -u` is not 0
- [ ] `--read-only`, `--cap-drop ALL`, `no-new-privileges`
- [ ] Worker `--stop-timeout` > `WORKER_SHUTDOWN_DRAIN_SECONDS`
- [ ] Liveness → `/healthz`, readiness → `/readyz`
- [ ] Logs shipped somewhere searchable

**Verify by using it**
- [ ] Sign in with GitHub end to end
- [ ] `/api/v1/me` lists the expected installations
- [ ] An unauthenticated `GET /api/v1/overview` returns 401
- [ ] A second account sees only its own installations
- [ ] Sign out, then confirm the old cookie no longer works
- [ ] Open a pull request and watch a review run appear

The last group is the one people skip. It is the only group that proves the
deployment works rather than merely starts.
