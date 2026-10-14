# CodeGuard AI

**AI-Powered GitHub Code Review and Code Quality Monitoring Platform**

## Deployment status

The React frontend is deployed at [codeguard-ai-five.vercel.app](https://codeguard-ai-five.vercel.app/).
The production FastAPI backend and its GitHub/AI integrations are not configured yet;
the hosted frontend currently displays an API-unavailable state. This is not a fully
operational production deployment.

Local verification used a separate development QA database. The latest supplied
verification run passed 956 backend tests and 75 frontend assertions, plus lint,
type checking, and a real Vite build. Populated dashboard pages were exercised in
Chromium at desktop, tablet, and mobile sizes. Live GitHub OAuth and AI publishing
remain unverified. Local secrets, databases, and generated dependencies are excluded
from this repository.

The release notes below describe the original release baseline; their earlier
environment limitations do not describe the subsequent local QA work.

CodeGuard AI is a GitHub App plus web dashboard that reviews Pull Requests
automatically and turns the results into a longitudinal code-quality record.
When a PR is opened or updated, the platform verifies the webhook, extracts
only the changed files and their Git diff, parses the changed source with
Tree-sitter and Python's native AST, sends a tightly scoped context to an AI
provider, **validates every finding against the real diff before publishing
anything**, posts inline review comments, and stores the result in PostgreSQL
for analytics.

> ### Current status: Phase 6 complete, release-cleaned · **v0.6.1**
>
> ### Release classification: **DEPLOYABLE-SHAPED / NOT PRODUCTION-VERIFIED**
>
> That phrase is doing real work, so here is exactly what it means.
>
> **Verified by execution.** The backend suite runs: **871 tests passed**. The
> database layer is proven against a real PostgreSQL 16 — **170 checks**
> covering migrations up *and* down, the job queue under genuinely concurrent
> workers, the review-run lifecycle under a concurrent supersede/finish race,
> every dashboard query and the numbers it returns, and the authorization
> boundary in SQL. The middleware stack is exercised over real HTTP (41 tests).
> The AI → GitHub path is attacked by 113 adversarial tests. `ruff`, `mypy` and
> `tsc` are clean.
>
> **Never executed.** No GitHub App credential, no GitHub OAuth credential and
> no Gemini API key has existed in any environment this project has been built
> in — so no webhook has ever been verified from GitHub, no installation token
> has ever been minted, no model has ever been called, and no comment has ever
> been posted. The container has never been built (no Docker daemon). 72 test
> functions covering the FastAPI request path and DB integration have never run
> (fastapi and sqlalchemy are uninstallable here — PyPI returns 403). The
> frontend has never rendered (npm returns 403); its pure logic is tested with
> real `tsc` and real Node, and its components are not.
>
> **Mock verified is not integration verified. A configured credential is not a
> working integration.** The full picture is in
> [`docs/final-verification-matrix.md`](docs/final-verification-matrix.md);
> what must happen before this touches a real repository is in
> [`docs/release-checklist.md`](docs/release-checklist.md) section B.
>
> **The governing rule of the system: the model is never trusted.** Its output
> is a set of claims, each checked against our own diff parse and the real file
> at the real commit. Only a `ValidatedFinding` can reach GitHub, and the
> publisher is structurally incapable of accepting anything else — Phase 6
> found and fixed a way for model *prose* to subvert that downstream, which is
> written up as finding H-1 in
> [`docs/final-security-review.md`](docs/final-security-review.md).
>
> **No accuracy claim is made anywhere.** There is no labelled benchmark
> dataset, so there is no valid methodology for an accuracy, precision, recall
> or false-positive figure, and an audit check fails the build if any
> user-visible string claims one. What the dashboard reports is what this
> pipeline's own gates did — emitted, validated, published, and which gate
> rejected the rest.
>
> **Intentionally not implemented:** the finding-status triage workflow. The
> schema anticipates it; no endpoint writes it; the reasoning is in
> [`docs/phase-6-final-report.md`](docs/phase-6-final-report.md) §12.

`architecture.md` in the repository root is the approved architectural
baseline and the primary reference for every phase.

---

## Core architecture

```
GitHub → Webhook → FastAPI → validation → GitHub API → diff extraction
       → AST analysis → AI review provider → finding validation and line mapping
       → GitHub inline review → PostgreSQL → analytics API → React dashboard
```

A **modular monolith**: one codebase, two processes (an API and a worker), one
PostgreSQL database. No microservices, no Redis, no Celery, no Kubernetes —
PostgreSQL is also the durable job queue, using `FOR UPDATE SKIP LOCKED`
(`architecture.md` §5.2).

The design principle that shapes everything: **the AI's output is a set of
proposals, not facts.** A dedicated validation layer checks every claimed file
path and line number against the real diff, and against a verbatim snippet the
model must quote, before anything is posted to GitHub.

## Technology stack

| Area | Choice |
|---|---|
| Backend | Python 3.12+ · FastAPI · Pydantic v2 · SQLAlchemy 2.x (async) · Alembic · httpx |
| Database | PostgreSQL 16 |
| Frontend | React 19 · TypeScript (strict) · Vite · Tailwind CSS |
| GitHub | GitHub App · webhooks · REST API · PyJWT (RS256) |
| Queue | PostgreSQL, `FOR UPDATE SKIP LOCKED` — no Redis, no Celery |
| Code analysis | Python native `ast` (stdlib) · Tree-sitter for JS/TS/Java (optional extra) |
| AI | Provider abstraction — **Gemini initial**, Claude optional future |
| Tooling | ruff · mypy · pytest · ESLint · Prettier · GitHub Actions |
| Containers | Docker Compose (db + api + worker) |

## Repository structure

```
codeguard-ai/
├── architecture.md          Approved architecture baseline (the reference)
├── docker-compose.yml       db + api + worker
├── Makefile                 Every documented command, as a target
├── .env.example             Environment template (no real secrets, ever)
│
├── backend/
│   ├── pyproject.toml       Dependencies, ruff, mypy, pytest config
│   ├── Dockerfile           Multi-stage; one image for api and worker
│   ├── alembic/             Migrations (URL comes from app settings)
│   ├── app/
│   │   ├── main.py          FastAPI application factory
│   │   ├── worker.py        Worker entrypoint — the claim loop
│   │   ├── core/            config · logging · correlation · errors · middleware
│   │   ├── db/              base · session · models/ · repositories/
│   │   ├── github/          signature · events · payloads · app_auth · client
│   │   ├── analysis/        diff/ · source/ · languages/ · mapping/ · pipeline
│   │   ├── jobs/            queue · runner · backoff · handlers/
│   │   ├── services/        webhook ingest · GitHub sync
│   │   ├── ai/              contracts · provider · registry · providers/
│   │   ├── api/             deps · health · problem · v1/
│   │   └── schemas/         Pydantic request/response models
│   ├── tests/               unit/ · api/ · integration/ · review/ · security/ · dashboard/ · auth/ · analysis/
│   └── verification/        PostgreSQL harnesses + benchmark (run_all.py)
│
├── frontend/
│   ├── src/                 app · layouts · pages · components · charts
│   │                        api · hooks · types · utils · styles
│   ├── scripts/             run-tests.mjs — the Vitest-free test runner
│   └── verification/        type stubs, for typechecking without npm
│
├── infrastructure/
│   └── scripts/             replay_webhook.py + sample_payloads/
├── docs/
│   ├── setup.md             GitHub App creation, tunnel, troubleshooting
│   └── decisions/           ADRs — including the AI provider decision
└── .github/workflows/ci.yml
```

---

## Local setup

> **Just want to see it running?** [`docs/local-quickstart.md`](docs/local-quickstart.md)
> is the shortest path from a fresh clone to the dashboard in a browser, written
> for someone who has never seen this repository. It starts with the frontend
> alone, which needs no database, no Python and no credentials.

### Prerequisites

Python 3.12+, Node 22+, Docker (or a local PostgreSQL 16), and Git.

### 1. Clone and configure

```bash
git clone <your-fork-url> codeguard-ai
cd codeguard-ai
cp .env.example .env
cp frontend/.env.example frontend/.env
```

`.env` works as-is for offline development — **no API key and no GitHub App
are required** to run the stack, the replay harness or the tests. Real GitHub
credentials are needed only for live end-to-end integration (§ *Live GitHub
integration*).

### 2. Start PostgreSQL

```bash
docker compose up -d db        # or: make db-up
docker compose ps              # wait for db to be "healthy"
```

Without Docker, create the database manually:

```bash
createuser codeguard --pwprompt      # password: codeguard
createdb codeguard --owner codeguard
```

### 3. Install dependencies

```bash
# Backend
cd backend
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[dev]"

# Optional: Tree-sitter grammars for JavaScript, TypeScript and Java.
# Python analysis works without them (`ast` is in the standard library);
# without them those three languages report PARSER_UNAVAILABLE.
pip install -e ".[dev,parsers]"

# Frontend
cd ../frontend
npm install
```

Or from the repository root: `make install`.

### 4. Run migrations

```bash
cd backend
alembic upgrade head            # or: make migrate
alembic current                 # expect: 0005_phase5_auth_and_fixes (the current head)
alembic downgrade -1            # roll back one, to verify reversibility
alembic upgrade head
```

### 5. Start the backend

```bash
cd backend
uvicorn app.main:app --reload --port 8000        # or: make api
```

Check it:

```bash
curl http://localhost:8000/health   | python -m json.tool
curl http://localhost:8000/healthz                 # liveness, no DB
curl http://localhost:8000/readyz   | python -m json.tool
```

Interactive API docs (development only): <http://localhost:8000/docs>

### 6. Start the worker

```bash
cd backend
python -m app.worker            # or: make worker
```

It polls the `jobs` table, claims work with `FOR UPDATE SKIP LOCKED`, and
sweeps jobs abandoned by workers that died. Several may run at once against one
database with no coordination beyond the claim query.

### 6b. Replay a webhook offline

No GitHub, no tunnel, no network beyond localhost:

```bash
export GITHUB_WEBHOOK_SECRET="any-local-value"   # also put it in .env
# restart the API so it picks the secret up, then:
make replay                 # a pull_request.opened delivery  -> 202 queued
make replay                 # the same delivery again          -> handled once
make replay-sync            # a pull_request.synchronize       -> debounced
make replay-bad-signature   # a tampered body                  -> 401
```

Watch the worker log. The Phase 1 success line is:

```
job.sync_pull_request.succeeded  repository=... pull_request_number=42 changed_files=N
```

The harness imports the *production* signing code, so a replayed delivery is
signed by exactly the code that verifies it. It bypasses no security check and
has no default secret.

### 7. Start the frontend

```bash
cd frontend
npm run dev                     # or: make web
```

Open <http://localhost:5173>. The **System** page shows live backend health,
which is the end-to-end proof that both halves are talking to each other.

### Everything with Docker

```bash
docker compose up --build       # db + api + worker
docker compose exec api alembic upgrade head
```

---

## Environment configuration

Full reference: [`.env.example`](.env.example).

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development` · `test` · `staging` · `production` |
| `DEBUG` | Must be `false` outside development (enforced at startup) |
| `LOG_LEVEL` | `DEBUG` … `CRITICAL` |
| `DATABASE_URL` | **Must** use the `postgresql+asyncpg://` driver |
| `FRONTEND_URL` | The single browser origin allowed by CORS |
| `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET` | GitHub App. Without them `/health` reports `github.app_configured=false` and deliveries are rejected |
| `JOB_MAX_ATTEMPTS`, `JOB_BACKOFF_*`, `JOB_VISIBILITY_TIMEOUT_SECONDS` | Retry and stale-job recovery |
| `SYNCHRONIZE_DEBOUNCE_SECONDS` | Delay on `synchronize` only, so a push burst produces one sync |
| `AI_PROVIDER` | `gemini` (default) or `claude` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Initial provider; unused in Phase 0 |
| `CLAUDE_API_KEY`, `CLAUDE_MODEL` | Optional future provider |

**Never commit:** `GEMINI_API_KEY`, `CLAUDE_API_KEY`, `GITHUB_PRIVATE_KEY`,
`GITHUB_WEBHOOK_SECRET`, GitHub access tokens, or a `DATABASE_URL` containing
a real password. `.env` is git-ignored and CI fails the build if one appears.

**No AI API key is ever exposed to the frontend.** Only `VITE_`-prefixed
variables reach the browser, and everything that reaches the browser is public.
All AI calls are made server-side by the backend.

---

## Database migrations

```bash
cd backend
alembic upgrade head                            # apply
alembic downgrade -1                            # roll back one
alembic current                                 # show applied revision
alembic revision --autogenerate -m "add x"      # create (Phase 1 onwards)
```

The database URL comes from application settings, never from `alembic.ini`, so
credentials live in exactly one place.

## Testing

```bash
cd backend
pytest tests/ --ignore=tests/api --ignore=tests/integration   # 871 passed
pytest tests/api tests/integration                            # needs fastapi + sqlalchemy
python3 verification/run_all.py                               # 170 checks, real PostgreSQL 16
python3 verification/benchmark.py                             # local timings — not an SLA
```

`verification/` holds the harnesses that drive the **exact SQL the application
builds** — extracted from the repository modules by AST, or produced by calling
the real query-builder functions — against a real PostgreSQL. They exist because
SQLAlchemy and asyncpg could not be installed in the release environment, and a
release that says "the SQL is correct" without executing it is asserting rather
than verifying. They prove the statements, **not** SQLAlchemy's binding or
session lifecycle; that distinction is kept in
[`docs/final-verification-matrix.md`](docs/final-verification-matrix.md).

`run_all.py` exits non-zero on any failure, so it works as a release gate.
Override its connection with `CODEGUARD_PGDIR` and `CODEGUARD_PGPORT`.

**No test requires an API key, a GitHub App or any network call.** GitHub HTTP
is served by `httpx.MockTransport` (a real `httpx` client over a fake
transport), and the App private key used in tests is a throwaway RSA key
generated at run time. CI asserts that no credential is present.

## Code quality

```bash
# Backend
cd backend
ruff check app tests          # lint
ruff format --check app tests # format check
mypy app                      # type check

# Frontend
cd frontend
npm run typecheck
npm run lint
npm run format:check
npm run build
```

From the repository root, `make verify` runs everything CI runs.

## CI checks

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and
pull request:

- **Backend** — ruff lint, ruff format check, mypy, `alembic upgrade head`,
  `downgrade base` + re-upgrade, pytest against a real PostgreSQL 16 service.
- **Frontend** — `npm ci`, typecheck, lint, format check, production build.
- **Secret scan** — fails if a `.env` or private key material is committed.

CI requires **no AI credentials** and makes **no external AI call**.

---

## AI provider architecture

```
                    AIReviewProvider            (Protocol — the contract)
                           │
             ┌─────────────┴─────────────┐
             │                           │
      GeminiProvider               ClaudeProvider
   initial provider            future, optional provider
   (integration deferred)      (integration deferred)
```

Nothing in the application depends on a vendor. The review orchestration layer
built in Phase 3 will depend on the `AIReviewProvider` protocol, and switching
provider is a change to one environment variable:

```bash
AI_PROVIDER=gemini    # or: claude
```

| File | Role |
|---|---|
| `app/ai/contracts.py` | Vendor-neutral types: `ReviewRequest`, `RawFinding`, `ReviewResult` |
| `app/ai/provider.py` | `AIReviewProvider` protocol + `BaseAIReviewProvider` |
| `app/ai/registry.py` | Maps `AI_PROVIDER` to a class |
| `app/ai/providers/gemini.py` | Initial provider — **seam only** |
| `app/ai/providers/claude.py` | Future provider — **seam only** |

Two details worth knowing:

- **The structured-output mechanism lives inside each provider.** Gemini
  enforces shape with a response schema; Anthropic uses a forced tool call.
  Both produce the same vendor-neutral `RawFinding`, so the validation and
  line-mapping layer works identically whichever is selected.
- **`RawFinding` means *raw*.** File paths and line numbers in it are the
  model's unverified claims. Turning a claim into a published comment is the
  job of the validation layer (`architecture.md` §13), never the provider's.

Rationale and rejected alternatives: [ADR-0001](docs/decisions/ADR-0001-ai-provider-abstraction.md).

### Gemini as the initial provider

Gemini is selected by default (`AI_PROVIDER=gemini`), chiefly for its free-tier
availability during academic development. `GEMINI_MODEL` has **no default
value**: model identifiers change and are deprecated on the vendor's schedule,
so the supported model is chosen against Google's current documentation during
the AI integration phase rather than guessed now.

Calling `review_code()` today raises `ProviderNotImplementedError` — an honest
failure rather than a stub that pretends an API responded.

### Claude as a future optional provider

Claude was the provider in the original architecture baseline and is retained
as a second implementation of the same contract. It is not required for the
initial implementation. Keeping it in the registry from day one is deliberate:
an abstraction validated by a single implementation is not validated.

---

## Current limitations

Phase 6 was the final engineering phase, so nothing below is waiting on a
later one. Each is a decision, with the reason.

| Not implemented | Why |
|---|---|
| Claude API integration | The provider seam exists and is exercised; a second vendor adds no capability this project needs. |
| Finding-status triage (dismiss, resolve, mark false positive) | The schema anticipates it and no endpoint writes it. Triage is a product workflow — who may dismiss, what it means on re-review, whether it is scoped per repository or per fingerprint — and none of that is decided anywhere here. Building it to make a table look used would be the wrong reason. See [`docs/phase-6-final-report.md`](docs/phase-6-final-report.md) §12. **Consequence, stated rather than hidden: every statistic derived from finding status is structurally zero.** |
| A metrics endpoint and alerting | Operational work that belongs with a real deployment, and there has never been one. |
| A second "critic" validation pass | Would add a provider call per review and cannot be shown to improve anything without the benchmark below. |
| A labelled benchmark | The only thing that would let this publish an accuracy figure. It does not exist, so no accuracy figure is published. |

**What the dashboard deliberately does not show:** an AI accuracy, precision or
false-positive rate; a single coverage percentage; or any claim that a
validated finding is a proven defect. Each omission is explained in
[`docs/dashboard-metrics.md`](docs/dashboard-metrics.md) — none of them can be
computed honestly without a benchmark dataset that does not exist.

### Implemented but not verified against the live services

This distinction matters and is stated plainly rather than blurred.

| Built and tested offline | Never exercised live |
|---|---|
| Gemini request construction, response parsing, error classification | a real call to `generativelanguage.googleapis.com` |
| Review creation, 422 salvage, ambiguous-write reconciliation | a real GitHub App installation posting a real comment |
| Every validator gate, against real diffs and real file content | whether Gemini's findings are *semantically* correct |
| Every dashboard SQL aggregate, against a real PostgreSQL 16 | the FastAPI routes themselves — FastAPI cannot be installed here |
| The frontend's pure modules, compiled by `tsc` and run under Node | React component rendering — React cannot be installed here |
| The whole OAuth flow, its session SQL verified against real PostgreSQL | a real person signing in with a real GitHub account |

The test suite drives both upstreams through `httpx.MockTransport`. That proves
the client handles a given response correctly; it does not prove the live
service returns that response. **Structural validation passing is not evidence
that the model detects real vulnerabilities** — that claim needs a labelled
benchmark and a real API key, and neither was used here.

Also deliberately absent: repository cloning (nothing is ever cloned or
executed), patch-text storage (metadata only — the patch is third-party source
code), and cancellation of an already-running job (supersession cancels queued
jobs; a running job's head-SHA check makes a stale run harmless).

## Future phases

Per `architecture.md` §27:

1. ~~**Phase 1 — GitHub ingress and the durable queue.**~~ **Done.**
2. ~~**Phase 2 — Diff and AST analysis.**~~ **Done.** See [`docs/phase-2.md`](docs/phase-2.md).
3. ~~**Phase 3 — AI review and the trust plane.**~~ **Done.** See [`docs/phase-3.md`](docs/phase-3.md).
4. ~~**Phase 4 — Dashboard API and core pages.**~~ **Done.** See [`docs/phase-4.md`](docs/phase-4.md)
   and [`docs/dashboard-metrics.md`](docs/dashboard-metrics.md).
5. ~~**Phase 5 — Authentication, security hardening and deployment.**~~ **Done.**
   See [`docs/phase-5.md`](docs/phase-5.md),
   [`docs/security-model.md`](docs/security-model.md) and
   [`docs/deployment.md`](docs/deployment.md).
6. ~~**Phase 6 — Final verification, security review and release.**~~ **Done, and final.**
   See [`docs/phase-6-final-report.md`](docs/phase-6-final-report.md),
   [`docs/final-verification-matrix.md`](docs/final-verification-matrix.md),
   [`docs/final-security-review.md`](docs/final-security-review.md) and
   [`docs/release-checklist.md`](docs/release-checklist.md).

There is no Phase 7. What remains is not engineering that was deferred — it is
verification that requires credentials, a package registry and a container
runtime, none of which existed here. `docs/release-checklist.md` section B is
that list, in order.

## Live GitHub integration

Optional, and not required for development, tests or the offline demo.

1. Create a GitHub App (see [`docs/setup.md`](docs/setup.md)). Use a
   **separate App per environment** — an App has exactly one webhook URL.
2. Permissions: Metadata **read**, Contents **read**, Pull requests
   **read & write**. Never Contents: write.
3. Events: Pull request, Installation, Installation repositories.
4. Webhook URL: `https://<your-tunnel>/api/v1/webhooks/github`
5. Put `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY` and `GITHUB_WEBHOOK_SECRET` in
   `.env`, restart the API and worker, and confirm
   `/health` shows `github.app_configured: true`.
6. Expose the endpoint publicly — GitHub cannot reach `localhost`:

```bash
npx smee-client --url https://smee.io/<channel> \
                --target http://localhost:8000/api/v1/webhooks/github
# or: cloudflared tunnel --url http://localhost:8000
```

Open a pull request on an installed repository and watch the worker log.

## Authentication

Sign-in is **GitHub OAuth**, and it is required: the dashboard serves nothing
without a session, and production refuses to start if it cannot authenticate.

- Server-side sessions — so logout actually invalidates, rather than waiting
  for a token to expire on its own.
- The cookie is `HttpOnly`, `Secure` and `SameSite=Lax`. **No token is ever
  held in JavaScript**, so an XSS bug cannot copy a credential out.
- CSRF is a double-submit token HMAC-bound to the session, so a token minted
  for one session does not verify for another.
- Every dashboard query is scoped in SQL to the installations in the caller's
  `user_installation_access`. The caller never supplies an installation id.
- A cross-tenant id returns **404, not 403** — a 403 confirms the row exists.

Set `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` and
`SESSION_SECRET`; see [`docs/deployment.md`](docs/deployment.md) §2. Without
them the app still runs, `/health` reports `oauth_configured: false`, and the
SPA says sign-in is unavailable rather than failing in a way that looks like a
bug.

## Security

- Repository code is **never executed** — analysis is parsing only.
- No secret is ever logged. The redaction processor runs *after* traceback
  rendering, recurses through lists, and scrubs credential-shaped values from
  free text — all covered by tests.
- No AI API key reaches the browser; all AI calls are server-side.
- All database access goes through parameterised constructs; a caller's text
  never becomes SQL.
- Model-produced text is never rendered as HTML.
- The GitHub App requests least-privilege permissions: Contents **read**, Pull
  requests **read & write**, Metadata **read**. Never Contents: write.
- Production **fails closed**: no session secret, a wildcard host list, a
  plain-HTTP origin or the development database URL all stop the process at
  start-up rather than serving.

Full security model, threat model and the 20 security invariants with their
verification status: [`docs/security-model.md`](docs/security-model.md).

## License

Academic project. Add a license before publishing.
