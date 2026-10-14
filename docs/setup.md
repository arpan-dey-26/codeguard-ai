# Setup notes

Complements the README's quick start with the parts that are easy to get
wrong. Phase 0 needs only the first two sections; the rest is here because it
blocks Phase 1 and is better read before you need it.

## PostgreSQL without Docker

```bash
# macOS (Homebrew)
brew install postgresql@16 && brew services start postgresql@16

# Debian / Ubuntu
sudo apt install postgresql-16 && sudo systemctl start postgresql

# then, as a superuser:
createuser codeguard --pwprompt          # password: codeguard
createdb codeguard --owner codeguard
```

Verify:

```bash
psql "postgresql://codeguard:codeguard@localhost:5432/codeguard" -c "SELECT version();"
```

## Common problems

**`MissingGreenlet` or a driver error on startup.** `DATABASE_URL` is using
the synchronous driver. It must start with `postgresql+asyncpg://`. Settings
validation catches this at startup with an explicit message.

**`/readyz` returns 503 with `migration_revision: null`.** The database is
reachable but has never been migrated. Run `alembic upgrade head`.

**`/health` returns 503.** PostgreSQL is not reachable. `/healthz` will still
return 200 — that is the intended difference between the two probes.

**Frontend shows "Unable to reach the backend".** The API is not running on
port 8000, or `VITE_API_BASE_URL` is set to something unreachable. Leave it
empty in development so the Vite proxy handles it.

**CORS error in the browser console.** `FRONTEND_URL` in `.env` does not match
the origin you are browsing from. It must match exactly, including the port.

## GitHub App — needed from Phase 1, not now

A GitHub App has exactly one webhook URL, so **create three separate Apps** —
development, staging, production. Sharing one means development events hit
production. This costs nothing and catches most first-time GitHub App
projects.

Create at <https://github.com/settings/apps/new>:

| Setting | Value |
|---|---|
| Webhook URL | your tunnel URL + `/webhooks/github` |
| Webhook secret | generate one; store as `GITHUB_WEBHOOK_SECRET` |
| Repository permissions | Metadata: **read** · Contents: **read** · Pull requests: **read & write** |
| Subscribe to events | Pull request · Installation · Installation repositories |

Never request Contents: **write**. The platform never commits, pushes or
creates branches, and not holding that permission removes the entire "the bot
broke my repository" class of risk.

Generate a private key, download it once, and store it in `GITHUB_PRIVATE_KEY`
(base64-encode it for staging and production — multi-line PEMs get mangled by
many environment systems). It is never committed and never written to the
database.

## Exposing the webhook endpoint locally

GitHub must reach your machine over the public internet; `localhost` is
unreachable to it. This blocks every GitHub App project at least once.

```bash
# Option A — smee.io (no account needed)
npx smee-client --url https://smee.io/<your-channel> \
                --target http://localhost:8000/webhooks/github

# Option B — cloudflared
cloudflared tunnel --url http://localhost:8000
```

Use the public URL as the App's webhook URL.

## AI provider

Phase 0 needs no key. `AI_PROVIDER=gemini` is the default; the application
starts, reports `configured: false` on `/health`, and makes no call.

When the AI integration phase begins, obtain a Gemini API key, set
`GEMINI_API_KEY` and `GEMINI_MODEL` in `.env` (choosing the model against
Google's current documentation), and restart. Nothing else changes.
