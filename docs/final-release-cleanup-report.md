# Final release cleanup — v0.6.1

**2026-09-18 · no new features, no architecture change**

This pass had one job: make the repository actually work when someone clones it
and runs it, and stop it telling them things that are not true. Everything below
is either a correctness fix, a reproducibility fix, or a correction to a claim
the source does not support.

---

## 1. Summary

**Eleven issues found. Ten fixed; one — the missing lockfiles — could not be,
and is documented with the exact command for you to run (§5).**

Five are defects in running code. The most consequential: **the frontend, run on
its own, told the developer their deployment had no OAuth credentials
configured** — a claim about their configuration that nothing had checked, and
which was almost never the real problem. A sixth would have stopped the project
building at all on a current TypeScript toolchain.

Two long-standing claims were corrected: Phase 0's exit criteria were ticked
without ever being run, and the frontend test count had been under-reported by a
factor of four — by me, in the Phase 6 report, from a scratch harness that
compiled two of five test files.

**The release classification does not change: DEPLOYABLE-SHAPED / NOT
PRODUCTION-VERIFIED.** Nothing in this pass touched GitHub, Gemini, Docker or a
browser, because none of them were available.

---

## 2. Issues found

| | Issue | Kind |
|---|---|---|
| C-1 | `npm run typecheck`, `test` and `build` all exit 2 on TypeScript 6 | **build-breaking** |
| C-2 | Backend unreachable was reported to the user as "OAuth not configured" | **correctness** |
| C-3 | No error boundary anywhere — a render throw produced a blank white page | **correctness** |
| C-4 | Every data-table row click was a full page reload | **correctness** |
| C-5 | The dev-only unauthenticated-reads mode was unreachable from its own frontend | **correctness** |
| C-6 | Phase 0's exit criteria claimed checks that had never run | **false claim** |
| C-7 | The frontend test count was reported as 17; it is 67 | **false claim** |
| C-8 | `README` expected migration `0002`; the head is `0005` | stale doc |
| C-9 | The release checklist named a `CORS_ORIGINS` variable that does not exist | false claim |
| C-10 | Compose invited the dev database to be read as a production topology | misleading doc |
| C-11 | No lockfile for either frontend or backend | reproducibility |

---

## 3. Issues fixed

### C-1 · The project did not build on a current TypeScript

`tsconfig.json` set `baseUrl`, which TypeScript 6 reports as deprecated **and
exits 2 on**. `npm run typecheck`, `npm run test` and `npm run build` (which
typechecks first) therefore all failed outright — on the machine this was
verified on, with TypeScript 6.0.3.

Fixed by removing `baseUrl`. Since TypeScript 5.0 a relative `paths` entry
resolves against the directory holding the config, so `"@/*": ["./src/*"]`
behaves identically on 5.x and 6.x with no deprecated option. `vite.config.ts`
declares the same alias separately for the bundler and was already correct.

*Verified:* `tsc --noEmit` and the test compile now exit 0.

### C-2 · "Sign-in is not configured" — a claim nothing had checked

`apiFetch` let `fetch`'s rejection escape as a bare `TypeError`. The auth
provider caught everything and resolved to *anonymous, sign-in unavailable*, so
running `npm run dev` with no backend produced:

> **Sign-in is not configured** — This deployment has no GitHub OAuth
> credentials set.

That is a statement about the reader's configuration, made on no evidence. The
true and actionable fact was that nothing was listening on port 8000.

Fixed in three places:

- `NetworkError` in `api/client.ts` — a connection that never happened is now a
  distinct class from an answer the server gave.
- A fourth auth state, `unavailable`, for a network failure or a 5xx (including
  the dev server's 502/504 when nothing is behind the proxy).
- A dedicated screen that says the API cannot be reached and gives the command
  to start it, with a retry button.

*Verified:* 6 new tests in `src/api/__tests__/network-failure.test.ts`, written
so the assertions cannot run after the test has already been recorded as passing
— the vacuous-async-test failure mode that cost this project 96 unrunnable
backend tests.

### C-3 · A render error was a blank page

There was no error boundary anywhere in the application. React unmounts the whole
tree when a render throws, so any unexpected shape from the API produced an empty
`<body>` and a console stack. "The page is blank" is the least actionable bug
report there is, and it was the default failure mode.

Added three layers:

- `RootErrorBoundary` outside the router, catching the auth provider, the gate
  and the router itself.
- `RouteErrorBoundary` as the router's `errorElement`, so a page-level failure
  renders *inside the shell* with the navigation still working.
- A static boot fallback in `index.html`, removed by `main.tsx` only once React
  is ready — so a bundle that fails to load leaves a message rather than nothing.
  Plus a `<noscript>`.

### C-4 · Every row click reloaded the whole application

`Table` rendered its row links as raw `<a href>`. All four list pages pass
internal routes to it, so clicking any row in Repositories, Pull requests,
Reviews or Findings triggered a full document load: bundle re-parsed, session
re-probed from scratch, white flash. Changed to React Router's `Link`. External
links — GitHub, the published comment — remain anchors, which is correct.

### C-5 · The one mode built for running this dashboard without OAuth was unreachable

`DASHBOARD_ALLOW_UNAUTHENTICATED_READS` makes `resolve_scope` return an
unrestricted scope, so every dashboard endpoint serves data with no session.
`/api/v1/auth/session` never mentioned it, so the SPA saw `authenticated: false`,
showed a sign-in page, and never rendered a single dashboard page against a
backend that was serving them.

- `/auth/session` now returns `unauthenticated_reads_enabled` — a fact `/health`
  and `/api/v1/system/settings` already publish, so nothing new is disclosed.
- A fifth auth state, `open`, renders the application with **a permanent,
  undismissable banner on every page** stating that there is no session and that
  reads are unauthenticated.

**This grants nothing and bypasses nothing.** The client holds no credential and
claims no session; the server reads the same setting itself when deciding what
any query may return. Production still **refuses to start** with the flag on, and
a test now pins that — the fact that the frontend renders in this mode makes that
guard more important, not less.

### C-6 · Phase 0 ticked nine checks that had never run

`docs/phase-0.md` listed nine exit criteria, all `[x]`: docker compose, alembic,
uvicorn, the worker, pytest, ruff/mypy/tsc/eslint, `npm run build`, "the SPA
renders", and CI. Phase 6 established by trying them that none had been executed.

Per the instruction not to erase history, the criteria are unchanged. They are now
preceded by a correction and followed by a table with a second column —
*defined in Phase 0* against *actually executed, as of v0.6.1* — with the real
status of each. `docs/phase-1.md` gets a shorter note of the same kind; its
criteria are mostly verified now, several more strongly than originally intended.

### C-7 · The frontend test count was wrong, and it was my error

Phase 6 reported "17 frontend tests". The repository's own runner —
`npm run test`, which is `tsc -p tsconfig.test.json && node scripts/run-tests.mjs`
— runs **67**, across five files. The 17 came from a scratch harness that
compiled only two of them. Corrected in the matrix, the checklist, the project
report and the Phase 6 report, each saying what the number was and why it was
wrong.

### C-8, C-9, C-10 · Documentation that did not match the source

- README's migration example expected `0002_phase1_github_ingress`; the head is
  `0005_phase5_auth_and_fixes`.
- The release checklist told an operator to set `CORS_ORIGINS`. **There is no
  such variable.** `FRONTEND_URL` → `Settings.cors_origins` → one entry passed to
  `CORSMiddleware`; a wildcard is impossible to configure by accident, which is
  the point of the derivation. `docs/deployment.md` already had this right.
- `docker-compose.yml` opened by saying it matched "the production topology".
  Its `db` service is a single superuser with a plain-text password in the file.
  It now carries a prominent header saying so and pointing at the three-role
  model in `docs/deployment.md` §5, which nothing in this repository applies for
  you. `docs/deployment.md` §5 gained the same warning from the other direction.

  *A claim in the first draft of this fix was itself wrong* — it said
  `docker-compose.prod.yml` has no `db` service. It does. Checked, and corrected
  before shipping.

### Also fixed

- Every setting is now documented. The Phase 6 checklist had said `.env.example`
  covered all of them; it covered 47 of 68. Sixteen were documented nowhere —
  including **`REVIEW_PUBLISH_ENABLED`, the switch that stops CodeGuard writing
  to GitHub**. An operator needing to pause publication during an incident had no
  documented way to find it. Now in `docs/deployment.md`, with a standing test.
- `useHealth`'s docstring claimed server state had moved to TanStack Query in
  Phase 4. It never did and could not — npm returns 403. Corrected to describe
  `useApiQuery`, which is what actually exists.

---

## 4. Issues intentionally left unchanged

**The `requires-python = ">=3.12"` declaration**, even though the test suite runs
on 3.11 here. Lowering it to make the current environment pass would be changing
the project to fit the verification rather than the reverse. See §6.

**No dashboard mutations.** `PATCH /findings/{id}` and a re-review endpoint were
not added. The schema has `status` and `finding_events`; nothing writes them;
triage is a product workflow this project has never specified. That scope
decision stands, and its cost — every statistic derived from finding status is
structurally zero — is stated in `docs/phase-6-final-report.md` §12.

**Nine mypy `unused-ignore` errors** on tree-sitter. The ignores are correct where
tree-sitter's stubs are installed and redundant where they are not; this
environment is the latter. Removing them breaks the former.

**26 NOT NULL columns without server defaults.** For `head_sha`, `title`,
`html_url` and `author_login` the absence *is* the guard.

**`_observed_head_sha` fails open.** A failed pre-publication head read lets
publication proceed, so a GitHub blip does not become a total review outage.
Documented as a decision in the security review (I-3).

---

## 5. Dependency reproducibility

### Frontend — **BLOCKED: npm registry returns 403**

```
$ curl -o /dev/null -w '%{http_code}' https://registry.npmjs.org/react
403
```

`npm install` cannot run here, so **`frontend/package-lock.json` was not
generated**. It has not been faked. A hand-written lockfile is worse than none:
it looks authoritative and pins hashes nobody verified.

`package.json` is unchanged — no version was altered, nothing was upgraded.

**Run this on your machine:**

```bash
cd frontend
npm install                  # resolves the ranges, writes package-lock.json
npm ci                       # verify: exact install from the lockfile
npm run build                # verify: typecheck + production bundle
git add package-lock.json && git commit -m "Add frontend lockfile"
```

### Backend — **BLOCKED: PyPI returns 403**

The project declares dependencies in `backend/pyproject.toml` and uses no lock
tool today. `uv` is not currently a declared dependency of this project, so
adding `uv.lock` is a choice, not a correction — and it could not be generated
here regardless.

**If you want one:**

```bash
cd backend
pip install uv
uv lock                      # writes uv.lock from the existing pyproject.toml
```

Do not let it change versions. If `uv lock` proposes different versions than
`pip install -e ".[dev]"` resolves, investigate before committing.

---

## 6. Python verification

| Check | Python 3.11.15 | Python 3.12.3 |
|---|---|---|
| `python -m compileall app` | pass | **pass** |
| `python -m compileall tests` | pass | **pass** |
| `mypy app --python-version 3.12` | 9 errors (tree-sitter only) | same 9, no new |
| `ruff` (already `target-version = "py312"`) | pass | pass |
| **pytest** | **871 passed** | **BLOCKED — dependency unavailable** |

Python 3.12.3 is present but has none of this project's third-party
dependencies, and PyPI returns 403, so **the test suite could not be executed
under 3.12**. It ran under 3.11.15, which is the interpreter that has them.

`requires-python = ">=3.12"` is preserved. **No claim is made anywhere that
runtime behaviour was verified on Python 3.12** — what was verified there is
that every module compiles and that mypy targeting 3.12 reports nothing new.
No Python 3.12-only syntax was introduced by this pass.

---

## 7. Frontend verification

| Check | Result |
|---|---|
| `tsc --noEmit` against hand-written type stubs | **REAL LOCAL VERIFIED** — 0 errors |
| `npm run test` (`tsc -p tsconfig.test.json` + `scripts/run-tests.mjs`, real Node 22.22.2) | **REAL LOCAL VERIFIED** — **67 passed** |
| Routes: all 12 page components exist, all three legacy redirects (`/dashboard`, `/reviews`, `/reviews/:id`) resolve, no nav item points anywhere missing | **STATICALLY VERIFIED** |
| Vite dev proxy `/api` and `/health` → `http://localhost:8000` | **STATICALLY VERIFIED** |
| `npm install` / `npm ci` / `npm run build` / `npm run dev` | **BLOCKED — dependency unavailable** (npm 403) |
| Rendering anything in a browser | **BLOCKED — browser tooling and dependencies unavailable** |

The stubs moved from scratch space into `frontend/verification/`, with a README
that says plainly what they do and do not prove. They catch a wrong prop, a wrong
hook argument, a missing field, an unhandled `null` — under the same strict flags
as the real config, including `noUncheckedIndexedAccess` and
`exactOptionalPropertyTypes`. They cannot catch a mistake in the stubs
themselves, and they prove nothing about rendering.

**No component in this project has ever been mounted, in a browser or anywhere
else.** The UX and accessibility work in this pass was done by reading the source.

### What the accessibility pass found

Mostly that it had already been done: semantic `<table>` with `scope="col"` and
an `sr-only` caption; a skip link; `aria-label` on the icon-free theme toggle;
`aria-expanded`/`aria-controls` on the mobile menu; `role="alert"` and
`role="status"` where they belong; a global `:focus-visible` outline;
`prefers-reduced-motion` honoured; charts as `viewBox` SVG that cannot overflow,
each with a `<details>` data table so no information is carried by colour alone;
`min-w-0` on the main column so flex children cannot force horizontal scroll.

What this pass added: `role="alert"` on the two new error screens, `role="status"`
on the unauthenticated banner, visible focus rings on the new buttons, and the
`<noscript>` fallback.

---

## 8. Backend verification

| Check | Result |
|---|---|
| `ruff check app tests` | **REAL LOCAL VERIFIED** — 0 violations |
| `ruff format --check app tests` | **REAL LOCAL VERIFIED** — 159 files formatted |
| `mypy app --ignore-missing-imports` | **REAL LOCAL VERIFIED** — 9 errors, all tree-sitter `unused-ignore` |
| `pytest tests/ --ignore=tests/api --ignore=tests/integration` | **REAL LOCAL VERIFIED** — **871 passed** |
| `pytest tests/api tests/integration` | **BLOCKED — dependency unavailable** — 72 test functions |
| Dead-configuration audit, all 68 settings, transitive | **REAL LOCAL VERIFIED** — 0 dead |

The dead-setting audit was widened this pass from limit-shaped names to **every**
setting, and made transitive: a setting passes if the application reads it, or if
it reaches — at any depth — a public property the application reads. Three take
the indirect route and all three genuinely arrive. The check was verified
non-vacuous by planting a dead setting, which it caught.

---

## 9. Database verification

`python3 backend/verification/run_all.py` — **170 passed, 0 failed**, against a
real PostgreSQL 16.13: migrations up/down/up, queue concurrency with genuinely
concurrent backends, the review-run supersede/finish race, review entities and
constraints, sessions and the authorization boundary, every dashboard query and
the numbers it returns, and the security/config/schema/analytics audits.

`alembic` itself remains **BLOCKED — dependency unavailable**; the harness
executes the migration SQL, not Alembic.

---

## 10. Security verification

Re-run this pass, all clean:

- Zero `eval`, `exec`, `compile`, `subprocess`, `os.system`, `shell=True`,
  `pickle` or `__import__` anywhere in `backend/app/`. (`re.compile` is not
  `compile`; the audit distinguishes them.)
- No credential-shaped literal committed in `backend/app`, `frontend/src` or
  `.env.example`.
- `.gitignore` ignores `.env` and `.env.*`, negates `!.env.example`, and covers
  `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*private-key*`, `*.crt`, `secrets/`.
  Verified with `git check-ignore` that both example files are *not* ignored.
- 27 audit checks in `final_audit.py` pass, including injection, traversal, SSRF,
  redirect, config parity, schema parity in both directions and analytics
  integrity.

**No security finding was introduced or discovered by this pass.** The one change
that touches the security surface — C-5, the `open` auth state — grants nothing:
the server was already serving that data, and production refuses to start in that
mode.

---

## 11. GitHub status

**BLOCKED — credential unavailable.** `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`,
`GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_CLIENT_ID` and
`GITHUB_OAUTH_CLIENT_SECRET` are all unset. No webhook has been received, no
installation token minted, no pull request synchronised, no comment posted — not
in this pass and not in any previous one.

## 12. Gemini status

**BLOCKED — credential unavailable.** `GEMINI_API_KEY` is unset. No model call
has ever been made by this project.

## 13. Docker status

**BLOCKED — Docker unavailable.** The CLI is present (29.4.3); `/var/run/docker.sock`
is absent, so there is no daemon. No image has been built, ever.

## 14. Browser status

**BLOCKED — dependency and tooling unavailable.** React, Vite and Vitest cannot
be installed (npm 403), so there is no dev server to open and no bundle to serve.
**Browser rendering remains entirely unverified.** You are about to be the first
person to see this interface, and that is stated here rather than implied away.

---

## 15. Remaining blockers

1. **npm registry 403** — no lockfile, no build, no component tests, no browser.
2. **PyPI 403** — 72 test functions unrun, no Alembic, no Python 3.12 test run.
3. **No Docker daemon** — no image has ever been built.
4. **No GitHub or Gemini credentials** — every integration claim rests on fakes.

None can be lifted from inside this environment. All four are yours to clear, and
`docs/release-checklist.md` section B is the ordered list.

---

## 16. Run this on your machine

### Start here — the frontend alone

Needs no database, no Python and no credentials.

```bash
cd codeguard-ai/frontend
npm install
npm run dev
```

Open **<http://localhost:5173>**

You will see the CodeGuard AI shell and a panel reading **"Cannot reach the
API"**, with the command to start the backend. **That is the correct screen**,
and it is the fix from C-2 doing its job — before this pass it said your OAuth
credentials were missing.

### Then the database

Either:

```bash
cd codeguard-ai
docker compose up -d db
docker compose ps          # wait for "healthy"
```

or, with a PostgreSQL 16 you already have:

```bash
createuser codeguard --pwprompt      # password: codeguard
createdb codeguard --owner codeguard
```

### Then the backend — two terminals

```bash
cd codeguard-ai
cp .env.example .env
```

```bash
# terminal A
cd codeguard-ai/backend
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[dev]"
alembic upgrade head
alembic current                      # expect: 0005_phase5_auth_and_fixes
uvicorn app.main:app --reload --port 8000
```

```bash
# terminal B
cd codeguard-ai/backend
source .venv/bin/activate
python -m app.worker
```

Check it:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/health | python -m json.tool
```

Reload <http://localhost:5173> — the "Cannot reach the API" panel is gone.

### To see the dashboard pages themselves

You will now get a sign-in screen. Without a GitHub OAuth app, use the
development switch: in `.env`,

```
DASHBOARD_ALLOW_UNAUTHENTICATED_READS=true
```

restart the API, and every page renders with a permanent banner saying there is
no session. **Turn it back off when you are done** — the backend refuses to start
with it on in production, and that is deliberate.

Expect the pages to be **empty**. The dashboard shows real review data, and no
reviews exist until a real pull request has been analysed by a real GitHub App.
Empty is the correct state here, not a broken one.

### The checks

```bash
# frontend
cd codeguard-ai/frontend
npm run typecheck
npm run test          # 67 tests
npm run lint
npm run build

# backend
cd codeguard-ai/backend && source .venv/bin/activate
ruff check app tests && ruff format --check app tests
mypy app
pytest tests/ --ignore=tests/api --ignore=tests/integration    # 871 tests
pytest tests/api tests/integration                             # the 72 that have never run here
python3 verification/run_all.py                                # 170 checks, needs PostgreSQL
python3 verification/benchmark.py                              # local timings, not an SLA
```

`docs/local-quickstart.md` has the same path in more detail, including a
troubleshooting table.

---

## 17. Final status by subsystem

| Subsystem | Status |
|---|---|
| Diff parsing, LineMap, AST analysis | **REAL LOCAL VERIFIED** |
| Context builder and its budgets | **REAL LOCAL VERIFIED** |
| Finding validator, all 14 gates | **REAL LOCAL VERIFIED** |
| Comment rendering and marker identity | **REAL LOCAL VERIFIED** |
| Job queue concurrency | **REAL LOCAL VERIFIED** (real PostgreSQL, concurrent backends) |
| Review-run lifecycle and supersession | **REAL LOCAL VERIFIED** (real PostgreSQL) |
| Migrations, both directions | **REAL LOCAL VERIFIED** (SQL executed; Alembic BLOCKED) |
| Every dashboard SQL query and its numbers | **REAL LOCAL VERIFIED** |
| Authorization boundary (`ViewerScope`) | **REAL LOCAL VERIFIED** (in SQL) |
| Middleware: headers, CORS, CSRF, proxy, host, body size, rate limit | **REAL LOCAL VERIFIED** (over HTTP) |
| Webhook HMAC verification | **REAL LOCAL VERIFIED** (synthetic payloads) |
| Session lifecycle and CSRF tokens | **REAL LOCAL VERIFIED** |
| Frontend pure logic and type safety | **REAL LOCAL VERIFIED** (67 tests, real Node; stubs, not real React types) |
| Frontend routes and dev proxy | **STATICALLY VERIFIED** |
| GitHub client, publisher, Gemini provider | **MOCK VERIFIED** |
| SQLAlchemy binding and session lifecycle | **BLOCKED — dependency unavailable** |
| FastAPI routes, DI, RFC 9457 rendering | **BLOCKED — dependency unavailable** |
| Alembic autogenerate | **BLOCKED — dependency unavailable** |
| Frontend rendering, `npm run build`, component tests | **BLOCKED — dependency unavailable** |
| Container build and two-process deployment | **BLOCKED — Docker unavailable** |
| GitHub App auth, webhooks, publication, OAuth sign-in | **BLOCKED — credential unavailable** |
| Gemini: any real call | **BLOCKED — credential unavailable** |
| CI workflow execution | **BLOCKED — credential unavailable** |
| Finding-status triage workflow | **NOT IMPLEMENTED** (scope decision) |

**Nothing is LIVE VERIFIED.**

### Overall

## DEPLOYABLE-SHAPED / NOT PRODUCTION-VERIFIED

Unchanged from v0.6.0, and it must be: this pass fixed correctness and
reproducibility defects, and nothing in it touched GitHub, Gemini, Docker, a
framework runtime or a browser. The classification moves when
`docs/release-checklist.md` section B is complete, and not before.

---

*This is the end of the engineering work. There is no Phase 7.*
