# Local quickstart

Getting CodeGuard AI running on your own machine, assuming you have never seen
this repository before.

**No GitHub App and no Gemini API key are needed for any of this.** What you can
and cannot see without them is spelled out in §6.

---

## 1. Prerequisites

| | Version | Needed for |
|---|---|---|
| Node | 22 or newer | the frontend |
| Python | **3.12 or newer** | the backend |
| PostgreSQL | 16 | the backend |
| Docker | optional | only if you want it to run PostgreSQL for you |

Check:

```bash
node --version      # v22.x or newer
npm --version
python3.12 --version
psql --version      # 16.x
```

Docker is **not** required. Every step below has a non-Docker path.

---

## 2. The frontend on its own — start here

This is the fastest way to see the interface. It needs no database, no Python
and no credentials.

```bash
cd frontend
npm install
npm run dev
```

Open **<http://localhost:5173>**.

You will see the CodeGuard AI shell and a panel saying **"Cannot reach the
API"**, with the command to start the backend. That is the correct and intended
screen — the dashboard's data all comes from the backend, and the frontend says
so rather than pretending otherwise.

To go further, keep this terminal running and do §3 and §4 in another.

### A production-style build

```bash
cd frontend
npm ci          # exact install; requires package-lock.json — see §7
npm run build   # typecheck, then a production bundle into frontend/dist
npm run preview # serve that bundle on http://localhost:4173
```

### Frontend environment

One optional variable, in `frontend/.env`:

```bash
cp frontend/.env.example frontend/.env
```

```
VITE_API_BASE_URL=
```

**Leave it empty.** Empty means "use the Vite dev server's proxy", which
forwards `/api/*` and `/health` to `http://localhost:8000`. Set it only if your
backend runs somewhere else. Nothing secret may ever go in a `VITE_` variable —
everything with that prefix is compiled into the browser bundle and is public.

---

## 3. PostgreSQL

Pick one.

### With Docker

```bash
cd codeguard-ai
docker compose up -d db
docker compose ps        # wait until db is "healthy"
```

This gives you `postgresql://codeguard:codeguard@localhost:5432/codeguard`.

> This database is a development convenience with a single superuser and a
> plain-text password in `docker-compose.yml`. It is not a production topology;
> see `docs/deployment.md` §5 for the roles a real deployment needs.

### Without Docker, using a PostgreSQL you already have

```bash
createuser codeguard --pwprompt      # enter: codeguard
createdb codeguard --owner codeguard
```

Then check it:

```bash
psql "postgresql://codeguard:codeguard@localhost:5432/codeguard" -c "select version();"
```

If your PostgreSQL uses a different host, port, user or password, put the real
URL in `DATABASE_URL` in the next step.

---

## 4. The backend

```bash
cd codeguard-ai
cp .env.example .env
```

`.env` works as-is for local development. The four lines that matter:

```
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://codeguard:codeguard@localhost:5432/codeguard
FRONTEND_URL=http://localhost:5173
DASHBOARD_ALLOW_UNAUTHENTICATED_READS=false
```

Note the driver: **`postgresql+asyncpg://`**, not `postgresql://`. SQLAlchemy
picks the async driver from that prefix and fails at startup without it.

Install and migrate:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[dev]"

alembic upgrade head
alembic current                    # expect: 0005_phase5_auth_and_fixes
```

Run it — two processes, two terminals:

```bash
# terminal A — the API
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# terminal B — the worker
cd backend && source .venv/bin/activate
python -m app.worker
```

Check the API:

```bash
curl http://localhost:8000/healthz                    # liveness, no database
curl http://localhost:8000/readyz  | python -m json.tool
curl http://localhost:8000/health  | python -m json.tool
```

Interactive API docs, development only: <http://localhost:8000/docs>

Now reload <http://localhost:5173>. The "Cannot reach the API" panel is gone.

---

## 5. Seeing the dashboard pages without GitHub sign-in

With the backend up you will now see a **sign-in screen**, because the dashboard
is authenticated and you have no session.

Two ways forward.

### A. Sign in with GitHub (needs an OAuth app)

Create a GitHub OAuth app, then set in `.env`:

```
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
SESSION_SECRET=<32+ random bytes>
PUBLIC_BASE_URL=http://localhost:8000
```

Generate a secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The callback URL to register is `http://localhost:8000/api/v1/auth/github/callback`.
Restart the API and the sign-in button will work.

### B. Development-only: serve reads with no session

```
DASHBOARD_ALLOW_UNAUTHENTICATED_READS=true
```

Restart the API. Every page now renders, with a permanent warning banner on each
one saying there is no session and reads are unauthenticated.

> **This is a development switch and only a development switch.** It serves every
> monitored repository's review data to anyone who can reach the port. The
> backend **refuses to start** with it enabled when `APP_ENV=production` — that
> is a start-up failure, not a warning. Turn it back off when you are done.

Either way, expect the pages to be **empty**: the dashboard shows real review
data, and no reviews exist until a real pull request has been analysed, which
needs a GitHub App.

---

## 6. What you can and cannot see locally

### Works with no credentials at all

- The whole interface: shell, navigation, theming, all eleven pages, responsive
  layout, loading, empty and error states.
- `/health`, `/healthz`, `/readyz` and the System page reading them live.
- The Settings page, showing the deployment's non-secret configuration.
- The full backend test suite, the database verification harnesses and the
  benchmark (§8).
- The offline webhook replay harness, which drives the ingress path with no
  network beyond localhost:

  ```bash
  GITHUB_WEBHOOK_SECRET=any-local-value make replay
  ```

  That secret is a value you pick, not a GitHub credential — the harness signs
  the payload with it and the API verifies the signature with the same one, so
  the real HMAC path is exercised end to end without GitHub.

### Needs a GitHub App

Receiving webhooks, minting installation tokens, synchronising pull requests,
publishing review comments — and therefore **any real data on the dashboard**.

### Needs a Gemini API key

Producing findings. Without it a review run reaches the provider call and fails
with `ai_unavailable`; everything before that point still works.

### Has never been done, by anyone, in this project

No GitHub App, GitHub OAuth app or Gemini key has existed in any environment
this project was built or verified in. Those integrations are tested against
fakes and have never run against the real services. `docs/final-verification-matrix.md`
records exactly which claims rest on what.

---

## 7. Dependency lockfiles

**There is no `frontend/package-lock.json` or `backend/uv.lock` in this
repository.** Neither could be generated: the environment this project was built
in has no access to the npm registry or PyPI (both return HTTP 403).

`npm install` therefore resolves the version *ranges* in `package.json`, so two
people can get different trees. To fix that on your machine:

```bash
cd frontend
npm install                 # resolves ranges, writes package-lock.json
git add package-lock.json
git commit -m "Add frontend lockfile"
```

From then on use `npm ci`, which installs exactly the lockfile and fails if it
disagrees with `package.json`.

The backend declares its dependencies in `backend/pyproject.toml` with no lock.
If you want one:

```bash
cd backend
pip install uv
uv lock                     # writes uv.lock from pyproject.toml
```

Neither lockfile may be hand-written. A fabricated lockfile is worse than none:
it looks authoritative and pins hashes nobody verified.

---

## 8. Running the checks

```bash
# backend
cd backend && source .venv/bin/activate
ruff check app tests && ruff format --check app tests
mypy app
pytest tests/ --ignore=tests/api --ignore=tests/integration    # 870 tests
pytest tests/api tests/integration                             # needs the full deps
python3 verification/run_all.py                                # 170 checks, real PostgreSQL
python3 verification/benchmark.py                              # local timings, not an SLA

# frontend
cd frontend
npm run typecheck
npm run test          # 67 tests
npm run lint
npm run build
```

`verification/run_all.py` needs a PostgreSQL it can reach. Point it at yours:

```bash
CODEGUARD_PGDIR=/var/run/postgresql CODEGUARD_PGPORT=5432 python3 verification/run_all.py
```

---

## 9. When something does not work

| Symptom | Cause | Fix |
|---|---|---|
| Frontend shows "Cannot reach the API" | the backend is not running | §4 |
| Frontend is completely blank | the JS bundle did not load | check the browser console; the page has a static fallback that should say the same |
| `uvicorn` exits at startup | configuration validation failed | read the message — it names the setting |
| `sqlalchemy.exc.InvalidRequestError` about the driver | `DATABASE_URL` lacks `+asyncpg` | §4 |
| `alembic upgrade head` cannot connect | PostgreSQL is not running, or `DATABASE_URL` is wrong | §3 |
| Dashboard pages are empty | no reviews exist yet | expected — see §6 |
| Sign-in screen with no button | no OAuth credentials configured | §5 |
| 401 on every dashboard call | no session | §5 |
| `npm ci` fails | no `package-lock.json` | use `npm install` once, then §7 |
