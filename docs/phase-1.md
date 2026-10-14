# Phase 1 — GitHub Ingress, App Authentication & Durable Job Queue

## Objective

Everything between "a developer pushes to a pull request" and "the metadata is
in PostgreSQL", with no AI anywhere in it.

```
GitHub
  ↓  webhook delivery
POST /api/v1/webhooks/github
  ↓  raw bytes → size guard → HMAC-SHA256 (constant time)
  ↓  parse → route
BEGIN
  INSERT webhook_events  (UNIQUE github_delivery_id, ON CONFLICT DO NOTHING)
  INSERT jobs            (partial UNIQUE on active dedupe_key)
COMMIT                                    ← both, or neither
  ↓  202 Accepted (target p95 < 300 ms)
Worker
  ↓  UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)
  ↓  COMMIT the claim before doing any work
GitHub App auth: private key → RS256 JWT (9 min) → installation token (1 h, cached in memory)
  ↓
repository sync → pull request sync → changed-file sync (paginated)
  ↓
PostgreSQL
```

## Delivered

| Area | Delivered |
|---|---|
| Ingress | `POST /api/v1/webhooks/github`; raw-body HMAC-SHA256 with `compare_digest`; size guard before crypto; required-header validation |
| Routing | Supported events and actions; draft PRs ignored; `ping` acknowledged; ignore reasons recorded, never silent |
| Idempotency | `webhook_events.github_delivery_id` UNIQUE + `ON CONFLICT DO NOTHING RETURNING` |
| Atomicity | Webhook row and job row in one transaction (transactional outbox) |
| Queue | `jobs` table; `FOR UPDATE SKIP LOCKED` claim; partial index over claimable rows; partial unique index for "one active job per key" |
| Retries | Class-driven classification; exponential backoff with full jitter; server-directed `Retry-After` wins; bounded attempts; dead-letter |
| Recovery | Visibility-timeout sweeper; attempts incremented at claim time so a poison job dead-letters |
| Supersession | Newer delivery cancels older *queued* jobs for the same PR; `synchronize` debounced |
| GitHub auth | RS256 App JWT (9 min, `iat` backdated 60 s); installation tokens cached in memory only; per-installation |
| GitHub client | Pagination via `Link`, rate-limit headers, retry classification, typed responses |
| Sync | installations · repositories · pull_requests · changed_files, all idempotent upserts |
| Harness | Offline replay tool reusing production signing; four sample payloads |
| Observability | delivery_id · job_id · worker_id · attempt · installation_id · repository · PR number on every relevant log line |
| Tests | 6 new suites; 115 tests executed in this environment |

## Deliberate boundaries

Phase 1 reads metadata and writes rows. It does **not**:

- clone a repository or execute anything from one;
- store patch text (only `has_patch`) — the patch is third-party source code;
- store webhook payload bodies (only a SHA-256 digest and the extracted ids);
- call any AI provider, or install any AI SDK;
- cancel an already-running job (supersession covers queued jobs; the
  handler's head-SHA check makes a stale run harmless).

## Decisions recorded

| ADR | Decision |
|---|---|
| [0005](decisions/ADR-0005-versioned-webhook-path.md) | Webhook at `/api/v1/webhooks/github`, no unversioned alias |
| [0006](decisions/ADR-0006-changed-files-keyed-by-commit.md) | `changed_files` keyed on the commit, not the review run |
| [0007](decisions/ADR-0007-error-hierarchy-is-framework-free.md) | Error hierarchy split from its HTTP rendering |

Job states follow `architecture.md` §16.11 exactly: `queued · running ·
completed · failed · cancelled · dead`.

## Exit criteria

> **On the ticks below (noted in v0.6.1).** These were marked complete when the
> phase was written, before any of them had been executed — no test suite in
> this project ran until Phase 6. Most of them *are* now verified, several
> against a real PostgreSQL with genuinely concurrent clients, which is a
> stronger check than the one originally intended. But the tick records the
> criterion, not the evidence. The evidence is in
> [`docs/final-verification-matrix.md`](final-verification-matrix.md), and where
> the two disagree the matrix is right.

- [x] A signed delivery produces exactly one `webhook_events` row and one `jobs` row, atomically
- [x] A duplicate delivery produces neither — under concurrency, not just sequentially
- [x] An invalid, missing, malformed or tampered signature is rejected with 401 and no detail
- [x] Unsupported events are acknowledged, recorded with a reason, and create no job
- [x] Concurrent workers never claim the same job
- [x] A job abandoned by a dead worker is reclaimed after the visibility timeout
- [x] Transient failures retry with backoff; permanent ones do not
- [x] An App JWT is valid RS256, backdated, and inside GitHub's 10-minute cap
- [x] Installation tokens are cached, refreshed before expiry, and never printable
- [x] A pull request synchronises: repository, PR and changed files persisted
- [x] The replay harness drives the whole path offline
- [x] No test, and no CI job, needs a GitHub App or an AI key

## Next

**Phase 2 — Git diff extraction and Tree-sitter/AST analysis.**

Exit criterion: *given a synchronised pull request, CodeGuard AI can
deterministically extract the changed lines and files and map changed source to
AST declarations without executing repository code.*
