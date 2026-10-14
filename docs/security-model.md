# Security model

What this system protects, who it protects it from, and where the protection
actually is. Written so that a reader can check the claims rather than take
them.

The organising rule: **every claim below points at code or at a test.** A
security property nobody can locate is a property nobody can maintain.

---

## 1. What is worth protecting

| Asset | Why it matters | Where it lives |
|---|---|---|
| Private repository source | The most sensitive thing the system touches. Never stored. | In flight only — fetched, analysed, discarded |
| Review findings | Describe weaknesses in someone's code, by file and line | `findings` |
| GitHub App private key | Mints installation tokens for every installation | Process memory only |
| Installation tokens | Read and write access to repositories | In-memory cache, 1 hour |
| Webhook secret | Anything that has it can forge a delivery | Process memory only |
| Gemini API key | Billable, and rate-limited per key | Process memory only |
| Session cookies | Are a user, for the session's lifetime | Browser; SHA-256 in `user_sessions` |
| OAuth client secret | Lets an attacker complete a login flow as us | Process memory only |

**Not stored anywhere, ever:** repository source files, full webhook payloads,
complete AI prompts containing source, user access tokens, any credential above
in plaintext.

---

## 2. Trust boundaries

```
     ┌─────────────┐  HMAC-SHA256 over raw bytes
     │   GitHub    │──────────────────────────────►  webhook receiver
     └─────────────┘                                  (unauthenticated,
            ▲                                          signature-gated)
            │ installation token (1h, memory only)
            │
  ┌─────────┴─────────┐          ┌──────────────┐
  │   CodeGuard API   │◄─────────│   Browser    │  session cookie
  │   + worker        │  cookie  │   (the SPA)  │  HttpOnly, Secure, Lax
  └─────────┬─────────┘          └──────────────┘  + X-CSRF-Token header
            │
            │ API key in a header, never a query string
            ▼
     ┌─────────────┐
     │   Gemini    │   ← output is UNTRUSTED. See §5.
     └─────────────┘
```

Four boundaries, four different authentication mechanisms, and they are
deliberately not interchangeable:

1. **GitHub → us (webhooks).** HMAC signature over the raw request bytes. The
   only unauthenticated public path.
2. **Us → GitHub (App).** RS256 JWT signed with the App private key, exchanged
   for a short-lived installation token.
3. **Browser → us.** A session cookie resolving to a server-side row, plus a
   CSRF token on every state-changing request.
4. **Person → GitHub (OAuth).** A separate client id and secret. **This is the
   only thing that establishes who a human is.** The App's private key can act
   on repositories and cannot say who is at the keyboard; conflating the two is
   how a dashboard ends up "authenticated" as nobody.

---

## 3. Authentication

**GitHub OAuth only.** No passwords, so no password storage, no reset flow, no
credential stuffing surface. Every user of this product already has a GitHub
account, and the resource being protected is authorised *by GitHub* — so
authorisation and identity come from the same place.

### The session

| Property | Choice | Why not the alternative |
|---|---|---|
| Form | Opaque 256-bit random token | A JWT cannot be revoked without the server-side list this already is |
| Storage | SHA-256 in `user_sessions` | A database dump yields no usable session |
| Hash | Plain SHA-256, not a KDF | The input is full-entropy random: there is no dictionary to slow down, and a KDF would tax every request |
| Transport | `HttpOnly; Secure; SameSite=Lax` | `HttpOnly` turns an XSS bug from "steal the credential" into "act while the page is open" |
| Absolute expiry | 8h, never extended | A session that renews itself forever makes "8 hours" untrue for the users who matter most |
| Idle timeout | 2h, separate | Activity postpones idleness, never the absolute limit |
| Rotation | Immediately after login | Session fixation: the identifier changes at the instant it becomes authenticated |
| Logout | Server-side revoke, *then* clear cookies | Clearing a cookie is a request to a browser; revoking the row is what makes logout mean something |

`SameSite=Lax` rather than `Strict` is forced by the flow, not chosen for
convenience: the browser returns from github.com as a cross-site top-level
navigation, and `Strict` would withhold the cookie on exactly that request.

### CSRF

Double-submit, with the token **HMAC-derived from the session token**:

```
csrf = HMAC-SHA256(SESSION_SECRET, "codeguard.csrf.v1" ‖ session_token)
```

Three properties follow, and the second is the one most implementations miss:

- It cannot be computed without the server key.
- **It does not verify against a different session.** An attacker's own valid
  CSRF token is useless against a victim's session, which defeats the
  "log the victim in as me, then submit" variant.
- It needs no second table and expires exactly when its session does.

**CORS is not CSRF protection**, and the two are constantly confused. A
preflight stops *JavaScript* reading a cross-origin response; it does not stop
an HTML form or an `<img>` causing the request to be sent with cookies
attached, and the server executes it before any CORS header is considered.

`app/api/authn.py` · tests: `tests/auth/test_tokens_and_csrf.py`

### The OAuth flow

`state` is checked **twice, independently**, and both are required:

1. A row in `oauth_states`, consumed with `DELETE … RETURNING` — deleting *is*
   the check, so a replay finds nothing. A select-then-delete would be a race
   two concurrent callbacks could both win.
2. A cookie set when the flow began, compared in constant time. The database
   row alone cannot tell whether *this browser* started the flow, which is what
   login CSRF exploits — it ends with the victim signed into the attacker's
   account and typing into it.

The user access token is used for two calls (`/user`, `/user/installations`)
and then dropped. It is never persisted, logged, or returned. A test asserts
this: `test_the_oauth_client_never_persists_a_user_token`.

---

## 4. Authorization

**One boundary, applied in SQL, on every query.**

`user_installation_access` holds the installations a user may read. A session
resolves to a `ViewerScope`, every query in
`app/db/repositories/dashboard.py` takes one, and every query applies it as a
bound predicate.

Three rules make this hold:

- **The caller never supplies an installation id.** Not in a header, a query
  parameter, a body field or a cookie. A test asserts that no query builder
  even *accepts* such an argument, so widening one's own scope is not an
  expressible mistake.
- **An empty scope is a real state.** A signed-in user whose access was revoked
  gets `FALSE` in the predicate, not an omitted clause. The bug this guards
  against is `IN ()` being a syntax error and somebody "fixing" it by dropping
  the filter — turning "sees nothing" into "sees everything".
- **A filter narrows; it never replaces.** `repository_id=999999` for somebody
  else's repository is ANDed with the scope and returns nothing.

Cross-tenant ids return **404, not 403** (architecture.md §17.7). A 403 says
"this exists and is not yours", which is a working oracle for enumerating
another tenant's ids.

`app/api/scope.py` · tests: `tests/auth/test_authorization_boundary.py`

**Stated staleness.** Access is cached and refreshed at login, so a revocation
takes effect within one refresh rather than instantly. That is a deliberate,
bounded trade-off (architecture.md §16.3) — checking GitHub on every request
would add a round trip to every page load and burn rate limit. `access_synced_at`
makes the bound observable rather than assumed.

---

## 5. The AI output is untrusted

The governing rule from Phase 3, unchanged: **the model is never trusted.**

Prompt injection is defended in four layers, and only the fourth actually makes
a hostile response harmless:

1. Channel separation — instructions and data never share a channel.
2. Nonce-fenced untrusted content.
3. Schema-forced structured output.
4. **Post-hoc validation.** Fourteen structural gates. A finding is published
   only if its file is one we showed the model, its line is in this pull
   request's diff, and the quoted source is genuinely at that line in the
   reviewed commit.

Layers 1–3 reduce how often bad output happens. Layer 4 is the one that means
bad output cannot reach GitHub. A fixture exercises a deliberately malicious
model response (`file_path = ../../secret`, `line = 999999`, fabricated anchor)
and asserts **zero** GitHub comments result.

`app/review/validator.py` · `docs/decisions/ADR-0011`

---

## 6. Input handling

| Input | Treatment |
|---|---|
| Webhook body | Size-capped **while streaming**, then HMAC over raw bytes, then parsed. In that order. |
| Webhook signature | `hmac.compare_digest`. A missing secret is a **failure**, never a pass. |
| Webhook replay | `UNIQUE (github_delivery_id)` + `ON CONFLICT DO NOTHING` — race-free rather than check-then-insert |
| Dashboard filters | Allow-lists only. An unknown value is a 422 naming the field, never silently ignored |
| Free-text search | Bound to `ILIKE` with `%` and `_` escaped |
| Pagination cursor | A position, not a capability. Deliberately unsigned; every value re-validated on decode |
| `Host` | Allow-listed by `TrustedHostMiddleware` |
| `X-Forwarded-For` | Counted **from the right** by trusted hop count. Reading `entries[0]` reads what the client wrote |
| `next=` on login | Must be a path: one leading slash, no second slash, no backslash, no scheme |
| Repository paths from the model | `is_safe_repository_path` — traversal, absolute paths and symlink-shaped values rejected |

---

## 7. Secrets

**Never logged, by construction.** `app/core/logging.py` runs a mandatory
redaction processor on every record, in two layers:

- **Key-shape matching** at every nesting depth, through dicts *and* lists.
- **Value-pattern scrubbing** of free text — PEM blocks, `ghp_`/`gho_`/`ghs_`
  tokens, `AIza…`, `sk-ant-…`, JWTs, `Authorization:` headers, DSNs with inline
  credentials — plus removal of rendered SQL parameter blocks.

The second layer exists because a **traceback is a string**: key-based
redaction cannot see inside one, and a rendered SQLAlchemy exception carries
the statement and its bound parameters. The processor therefore runs *after*
`format_exc_info`, not before it. (It ran before, until Phase 5, and every
traceback reached the log unread.)

Bulk keys — `patch`, `diff`, `source`, `prompt`, `completion`, `payload`,
`raw_body` — are **dropped, not masked**: their presence in a record is itself
the mistake, and masking would hide the bug instead of preventing it.

**Never committed.** CI fails the build on a committed `.env` or any private
key material. `.dockerignore` keeps both out of the build context, because a
layer is forever.

**Never sent to the frontend.** The settings endpoint returns booleans *about*
secrets. There is no write route.

---

## 8. Configuration fails closed

Production **refuses to start** when any of these is wrong:

- `SESSION_SECRET` missing or under 32 characters
- `DATABASE_URL` still the development default (its credentials are published
  in this repository)
- `TRUSTED_HOSTS` unset or `*`
- `PUBLIC_BASE_URL` unset or not https
- `FRONTEND_URL` not https
- `DEBUG` true
- `DASHBOARD_ALLOW_UNAUTHENTICATED_READS` true
- `SameSite=None` without `Secure`

There is **no development fallback session secret**. A fallback is a secret the
attacker also has, and the one time it matters is the one time somebody forgot
to set the real one. Development generates a random key per process and says so
in the log, so forgetting it costs a re-login and can never be mistaken for a
configured deployment.

`app/core/config.py` · CI: "Assert production refuses to start when it is not secure"

---

## 9. Response headers

Set on every response by `SecurityHeadersMiddleware`:

| Header | Value | What it stops |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | A JSON response being interpreted as HTML |
| `X-Frame-Options` | `DENY` | Clickjacking |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` | This API renders no HTML, so the policy can be total |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Path leakage to third parties |
| `Permissions-Policy` | camera, microphone, geolocation, payment, usb all `()` | Powerful features this API never needs |
| `Cache-Control` | `no-store` | An authenticated response sitting in a shared proxy or a disk cache |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Downgrade — **and only when the deployment is HTTPS-only** |

HSTS is conditional on purpose. It is a promise the browser holds for a year
and cannot be withdrawn server-side, so sending it from a plain-HTTP
environment makes that origin unreachable in that browser with no fix.
`preload` is omitted: submitting to the preload list is an operator's decision
with a slow manual reversal.

---

## 10. Threat model

Attackers, in rough order of likelihood.

### T1 — Someone who can reach the API over the network

*Wants:* any repository's review data.

Stopped by: a session cookie resolving to a server-side row. No session, no
data. Production cannot be configured to serve openly. Rate-limited per
address.

*Residual:* the in-process rate limiter is approximate — with N API processes
the effective limit is N times the configured one, and a restart forgets every
counter. It bounds a runaway client; it is not a precise global quota.

### T2 — A signed-in user reaching for another tenant's data

*Wants:* repositories outside their installations.

Stopped by: the scope predicate in SQL on every query, with the installation
list derived server-side. Cross-tenant ids 404. A filter cannot replace the
scope.

*Residual:* access is cached, so a revocation takes up to one refresh interval
to apply. Bounded and observable via `access_synced_at`.

### T3 — A hostile or compromised AI response

*Wants:* a comment on a file we never showed it, or a fabricated location.

Stopped by: fourteen structural validation gates, applied after the response,
before anything is published. Path traversal, out-of-diff lines and fabricated
anchors are all rejected.

*Residual:* a finding that is **structurally valid and semantically wrong** —
correct file, correct line, quoted source genuinely there, and the claim about
it is nonsense. Nothing in this system detects that, and the dashboard says so
rather than implying otherwise: a validated finding means *anchored*, never
*proven*.

### T4 — Someone forging a webhook delivery

*Wants:* to make us review an attacker-chosen repository, or to flood the queue.

Stopped by: HMAC over the raw bytes, verified before parsing. A missing secret
fails rather than passes. Replay is caught by the delivery-id unique index.
Body size is capped while streaming.

*Residual:* anyone can send unsigned requests. They are rejected cheaply,
logged at warning with the source address, and rate-limited.

### T5 — A cross-site request from a page the user visits

*Wants:* a state-changing action under the victim's session.

Stopped by: `SameSite=Lax` plus a session-bound CSRF token. Two independent
layers, because `Lax` does not cover a same-site subdomain an attacker
controls. Logout is a POST, so it cannot be triggered by an `<img>`.

### T6 — XSS in the SPA

*Wants:* the session.

Stopped by: `HttpOnly` on the session cookie. No token in `localStorage`,
`sessionStorage`, IndexedDB or React state — asserted by
`src/api/__tests__/credentials.test.ts`. React escapes by default and nothing
uses `dangerouslySetInnerHTML`.

*Residual:* an XSS payload can still act **within** the session while the page
is open. `HttpOnly` changes the blast radius from "copy the credential and use
it later" to "act now" — it does not eliminate it.

### T7 — Someone with a read replica or a database backup

*Wants:* usable credentials.

Stopped by: no plaintext secret is stored. Sessions and OAuth states are
SHA-256. Client IPs are keyed hashes. The App key, webhook secret and API keys
never touch the database.

*Residual:* review findings and repository metadata **are** readable from a
backup. That is what the backup is for; protecting it is an operational
control (encryption at rest, restricted access), documented in
`docs/deployment.md`.

### T8 — A compromised application process

*Wants:* to escalate.

Stopped by: non-root container, dropped capabilities, `no-new-privileges`,
read-only root filesystem. The database role has DML only — no `CREATE`,
`ALTER`, `DROP` or `TRUNCATE` — so it cannot change the schema or disable a
constraint the application relies on.

*Residual:* the process holds the App private key in memory. Anything that can
read that memory can mint installation tokens. Bounding this properly needs a
KMS or a separate signing service, which is out of scope here and named in
Phase 6.

### T9 — A malicious dependency

*Wants:* anything.

Stopped by: a small, justified dependency surface (ADR-0004); `npm ci` and
pinned Python requirements; `pip-audit` in CI on **runtime** dependencies only,
because auditing the dev toolchain reports vulnerabilities in things that never
ship and trains people to ignore the output. The runtime image no longer
contains pytest, ruff or mypy.

*Residual:* no lockfile hash pinning for Python, and no SBOM. Named in Phase 6.

### Explicitly out of scope

- **Repository code execution.** Nothing in this system ever executes, clones
  or shells out to repository content. Tree-sitter and Python's `ast` parse; they
  do not evaluate.
- **A malicious GitHub.** If GitHub is hostile, it already has the repositories.
- **Physical and insider access to the database host.**
- **Denial of service at network scale.** A CDN or a WAF's job, not this
  application's.

---

## 11. Security invariants

Twenty properties, each with where it is enforced and how it was verified. The
status vocabulary is strict, and **NOT VERIFIED never becomes PASS**:

- **PASS** — verified by an executed test or an executed command in this environment.
- **NOT VERIFIED** — the control exists in code and could not be exercised here.
  Says why.

| # | Invariant | Enforced in | Status | Evidence |
|---|---|---|---|---|
| 1 | No credential is logged, at any nesting depth, including inside a rendered traceback | `core/logging.py` | **PASS** | `test_phase5_regressions.py` (6 cases), `test_logging_redaction.py` |
| 2 | No credential is committed | CI secret scan, `.gitignore`, `.dockerignore` | **PASS** | repo scan clean; CI job present |
| 3 | No AI key reaches the frontend; all AI calls are server-side | `ai/providers/`, settings schema | **PASS** | `test_scope_and_security.py` |
| 4 | Repository code is never executed | `analysis/` | **PASS** | `test_security_and_adapters.py` |
| 5 | Repositories are never cloned; whole repos are never sent to an AI provider | `review/context.py` budgets | **PASS** | `test_context_builder.py` |
| 6 | Webhook signature is verified on raw bytes, constant-time, before parsing | `api/v1/webhooks.py`, `github/signature.py` | **PASS** | `test_webhook_signature.py`, `test_security_invariants.py` |
| 7 | A missing webhook secret fails closed | `github/signature.py` | **PASS** | `test_webhook_signature.py` |
| 8 | Webhook bodies are size-capped **before** buffering | `core/middleware.py` | **PASS** | `test_phase5_regressions.py` |
| 9 | No AI finding is published without passing every structural gate | `review/publisher.py` type signature | **PASS** | `test_publisher.py`, `test_phase3_invariants.py` |
| 10 | Every dashboard query applies a `ViewerScope` in SQL | `db/repositories/dashboard.py` | **PASS** | `test_scope_and_security.py::test_every_query_builder_applies_the_scope` |
| 11 | A caller cannot supply an installation id to widen scope | query-builder signatures | **PASS** | `test_authorization_boundary.py` |
| 12 | An empty scope returns nothing, not everything | `_scope_clause` emits `FALSE` | **PASS** | `test_authorization_boundary.py` (4 builders) |
| 13 | A cross-tenant id returns 404, not 403 | dashboard routes | **PASS** | `test_authorization_boundary.py` |
| 14 | Production refuses to start without session secret, trusted hosts, public base URL and https | `core/config.py` | **PASS** | `test_scope_and_security.py` (7 cases) + CI |
| 15 | There is no fallback session secret constant | `core/config.py` | **PASS** | `test_phase5_regressions.py` |
| 16 | The session cookie is `HttpOnly`; no token is held in browser storage | `auth/cookies.py`, `api/client.ts` | **PASS** | `credentials.test.ts` (14 cases) |
| 17 | State-changing requests require a session-bound CSRF token | `api/authn.py` | **PASS** | `test_tokens_and_csrf.py` |
| 18 | `X-Forwarded-For` is counted from the right, by trusted hop count | `core/proxy.py` | **PASS** | `test_phase5_regressions.py` |
| 19 | An installation token is only ever sent to the configured GitHub host | `github/client.py` | **PASS** | `test_phase5_regressions.py` |
| 20 | The runtime container runs as non-root with no dev toolchain | `Dockerfile` | **NOT VERIFIED** | Docker is not available in this environment. The Dockerfile sets `USER codeguard` and installs runtime deps only; a CI job asserts both. Not executed here. |

---

## 12. Reporting a vulnerability

This is a final-year academic project, not a hosted service. There is no bug
bounty and no security contact. If you are running it yourself, treat the
residual risks in §10 as yours, and read `docs/deployment.md` before exposing
it to a network.
