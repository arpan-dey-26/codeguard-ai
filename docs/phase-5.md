# Phase 5 — Authentication, and the defects the audit found

## Objective

Move from *extensively mocked, offline-verified software* toward *securely
deployable software*. Two halves, and the second is the one that mattered more
than expected:

1. Build the authentication the architecture always specified and Phase 4
   deliberately did not ship.
2. Audit Phases 0–4 without trusting their completion reports, and fix what was
   actually wrong.

The audit found **17 real defects**. Five were high severity. None of them
were visible in the Phase 1–4 reports, because a report describes what was
built, and these were things that were built incorrectly.

---

## The seam, filled in

Phase 4 shipped `app/api/scope.py` with a `ViewerScope` that every dashboard
query already took, and a `resolve_scope` that refused to serve rather than
inventing a placeholder session. Phase 5 replaced the body of one function:

```python
def resolve_scope(settings, context=None):
    if context is not None:
        return ViewerScope.for_session(context)   # ← Phase 5
    ...
```

Every query, every route, every response model is unchanged. That was the whole
argument for building the seam first, and it held: the diff that added
authentication touched `scope.py`, one dependency signature in
`dashboard/common.py`, and nothing else in the dashboard.

The one structural change was making `scope.py` depend on a `Protocol` rather
than importing `SessionContext` directly. Importing the concrete class dragged
SQLAlchemy into every consumer of `ViewerScope` — including the pure query
builders and the tests that exercise them without a database. The dependency
pointed the wrong way: an authorization boundary should not know which
persistence library stores sessions.

---

## What authentication looks like

```
browser ──► GET /auth/github/login
              ├─ create oauth_states row   (single-use, TTL)
              └─ set state cookie          (Lax, HttpOnly)
                     │
                     ▼
              github.com/login/oauth/authorize
                     │
                     ▼
        GET /auth/github/callback?code&state
              ├─ 1. compare state cookie      ← this browser started the flow
              ├─ 2. DELETE … RETURNING state  ← and this state is unspent
              ├─ 3. exchange code             ← only now
              ├─ 4. GET /user                 ← who
              ├─ 5. GET /user/installations   ← what they may see
              ├─ 6. create session, then ROTATE its token
              └─ set session + csrf cookies
```

### Four decisions worth defending

**Server-side sessions, not JWTs.** A signed token in a cookie cannot be
revoked without building the server-side list this already is. Architecture.md
§17.4 requires `POST /auth/logout` to *invalidate* a session; with a stateless
token, logout would be a lie — the token keeps working until it expires on its
own. The extra round trip buys real logout, real idle timeout and real "sign
out everywhere".

**The stored form is a plain SHA-256.** Not bcrypt, not Argon2. The input is
256 bits from `secrets.token_urlsafe`, so there is no dictionary to slow down
and a KDF would add its cost to *every authenticated request*. The property
being bought is that a database dump contains no usable session, and SHA-256
over full-entropy input gives exactly that. This reasoning does not transfer to
passwords, which is why it is written down here rather than left as a
precedent.

**`state` is checked twice, independently.** The database row catches replay —
`DELETE … RETURNING` makes deleting *be* the check, where select-then-delete is
a race two concurrent callbacks can both win. The cookie catches login CSRF,
where an attacker starts a flow and feeds the victim the resulting callback
URL; the victim ends up signed into the *attacker's* account and typing into
it. Neither check catches the other's attack.

**The CSRF token is HMAC-derived from the session token.** No second table, and
— the part most implementations miss — it does not verify against a *different*
session. An attacker's own valid CSRF token is useless against a victim's
session.

---

## The audit

Read `architecture.md` in full, then every Phase 0–4 module, then compared
models against migrations field by field and dashboard SQL against the
migration-defined schema. Completion reports were not consulted.

### What was wrong

| Sev | Defect | Consequence |
|---|---|---|
| High | Webhook body size checked **after** `await request.body()` | Unauthenticated POST of any size fully resident before rejection. The comment claimed the opposite. |
| High | `enqueue` inserted blind against a partial unique index | A second delivery on a live `dedupe_key` raised `IntegrityError` → 500 → **the webhook audit row rolled back with it**. GitHub does not redeliver. |
| High | Write transaction held open across up to 300 GitHub fetches | Pool exhaustion under concurrency; row locks held for the whole analysis. Two modules asserted this could not happen. |
| High | Job release statements keyed on `id` alone | A worker whose lock the sweeper revoked could mark a job complete out from under the worker now running it. |
| Med | Publication transient escaped `publish()` | Findings already committed, so the retry re-inserted **every finding** under a new run id. The dashboard double-counted per retry. |
| Med | Retryable AI failure left `review_runs` `running` forever | The comment claimed a sweeper reclaimed it. No such sweeper existed. |
| Med | `sync_installation` / `sync_repositories` routed with **no handler** | Every install, uninstall, suspend and repo change failed permanently. `mark_uninstalled` had no caller anywhere. |
| Med | Redaction processor ran **before** `format_exc_info` | Every traceback — SQL statement and bound parameters included — reached the log unread. `_redact` also skipped lists. |
| Med | Unbounded upstream response reads | One 100 MB blob ≈ 100 MB response + 133 MB string + decoded bytes, before any size guard. |
| Med | 24 `server_default`s in migrations, absent from models | `compare_server_default=True`, so autogenerate would drop them — and two hot INSERT paths depend on them. |
| Med | Four migration indexes absent from model metadata | Autogenerate would propose dropping the indexes serving every unfiltered list query. |
| Med | Three `ON DELETE SET NULL` FK columns unindexed | Sequential scan of the child table on every parent delete. |
| Low | Pagination followed `Link` to any host, token attached | Token disclosure if the header were ever attacker-influenced. |
| Low | SIGTERM observed only between jobs | Worker SIGKILLed mid-review; job stuck `running` for 15 minutes. |
| High | `_fail_fast_on_production_gaps` checked `debug` and nothing else, while its docstring claimed to require infrastructure secrets | A production process inherited the development `DATABASE_URL` — with `codeguard:codeguard` published in this repository — plus no session secret and a wildcard host list, and started. |
| Med | `skip_reason_breakdown` had no supporting index | Sequential scan of the largest table in the schema, in the default state of the analytics screen. |
| Low | `finding_events.created_at` annotated `Mapped[object]` | Type checking disabled on that attribute and everything reading it. |

### What was clean

Stated because absence of a finding is a result. **Stale-PR race:** four
independent layers, and the inverted case (an older job overtaking a newer run)
is unreachable. **Webhook signature handling:** constant-time, verified over
raw bytes before parsing, missing secret fails closed, replay caught by a unique
index race-free. **Error messages:** no `CodeGuardError` detail reachable from a
route interpolates SQL, a path, a token or an upstream body. **Secrets at log
call sites:** all 48 enumerated; none passes a credential. **Column parity:**
all 10 tables, 193 columns — names, types, nullability, FKs, unique and check
constraints all match. **Raw SQL:** every table and column referenced in
`dashboard.py` exists. **Downgrades:** all four reverse correctly.

---

## Hardening

**Fail closed at start-up.** Production refuses to boot without a session
secret (≥32 chars), real trusted hosts, an https public base URL and an https
frontend URL — and refuses `DEBUG=true`, the development database URL, and
`DASHBOARD_ALLOW_UNAUTHENTICATED_READS`. Every problem is reported at once, not
one per restart.

**No fallback session secret.** Development generates a random key per process
and logs a warning. A shared constant in the source would be a real signing key
that every reader of this repository holds.

**Proxy headers by hop count, not address list.** Uvicorn's `--proxy-headers`
trusts a list of proxy *addresses*, which are not knowable at build time on a
container platform — and `--forwarded-allow-ips "*"` means trusting a header
any client can send. `TRUSTED_PROXY_COUNT` is static, knowable, and the thing
an operator actually knows. The address is read **from the right**: `entries[0]`
is whatever the client wrote there.

**Body limits as pure ASGI.** By the time a `BaseHTTPMiddleware` sees a
`Request`, the body may already be buffered. Counting bytes on the message
stream is the only placement that works.

**HSTS, conditionally.** It is a promise the browser holds for a year and
cannot be withdrawn server-side. Sent only when the deployment says it is
HTTPS-only. `preload` omitted deliberately.

**Rate limiting, honestly labelled.** In-process fixed windows. With N
processes the effective limit is N×, and a restart forgets every counter — said
in the module docstring, the env template and the deployment guide, because a
limiter whose real behaviour is undocumented is worse than none: somebody will
size a threat model around it.

**Container.** Non-root in *both* stages (development ran as root). Runtime
dependencies only — pytest, ruff and mypy used to ship to production. Base
image via a build argument so a deployment can pin a digest, with the tag as
the default because a digest written into the file goes stale and silently
blocks security updates. `.dockerignore` keeps `.env` and `.git` out of the
context; a layer is forever.

**Database least privilege.** Three roles: DDL, DML, read-only. The application
issues no DDL — verified by reading every statement in `app/db/repositories/`,
not assumed — so it loses nothing and can no longer drop a table or quietly
alter away a constraint it depends on.

---

## What was deliberately not done

Per the phase's own non-goals: no new AI provider, no autonomous agent, no
automatic commits or PR modification, no code-execution sandbox, no Kubernetes,
no microservices, no Redis or Celery, no billing, no enterprise SSO, no mobile
app, no semantic AI benchmark. Still a modular monolith, still two processes
from one image, still PostgreSQL as the queue.

Three more, with reasons:

**No live integration was exercised.** No GitHub App credentials, no OAuth
credentials and no Gemini key exist in this environment. Every GitHub and
Gemini claim in this repository therefore rests on `httpx.MockTransport`
against documented response shapes. The report says so in the one place it
would be tempting not to.

**`/health` reports no integration above `configured`.** Reaching
`authenticated` or `operational` honestly requires recording the outcome of
real operations, which is Phase 6 work. Until then the endpoint says
`configured` and leaves `last_verified_at` null, rather than letting a UI infer
"Connected" from a present credential.

**Dashboard mutations are still absent.** `PATCH /findings/{id}` and
`POST /re-review` are authenticated actions attributed to a user. There are now
users — but the phase's scope was authentication and hardening, and adding
write endpoints in the same change as the authorization boundary that guards
them would have meant shipping both without either being settled.

---

## Verification

Full detail, including what could not be executed and why, is in
`docs/phase-5-report.md` §5 and §6.

The summary: **686 backend cases and 61 frontend cases pass, 0 fail.** 103
checks ran against a real PostgreSQL 16.13 — 28 of them the Phase 5
authentication SQL, extracted from the module's AST so the statement executed
is the statement the application runs. Migrations 0001–0005 upgrade, downgrade
to zero tables, and re-upgrade cleanly. The frontend typechecks with 0 errors.

**Not executed, and not claimed:** FastAPI route tests, SQLAlchemy/asyncpg
integration tests, `npm ci` / `npm run build`, browser verification, Docker
build, and any live GitHub or Gemini call. PyPI and npm both return 403 in this
environment — an organisation egress policy, re-verified at the start of this
phase. Those 72 defined-but-unexecuted test functions are counted as
**blocked**, not as passing.
