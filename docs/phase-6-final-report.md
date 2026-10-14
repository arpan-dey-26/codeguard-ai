# Phase 6 — Final verification report

**Version 0.6.0 · 2026-09-18 · the final engineering phase**

*This report covers Phase 6, which produced v0.6.0. A subsequent release-cleanup
pass produced v0.6.1 — no new features, no architecture change; see
[`final-release-cleanup-report.md`](final-release-cleanup-report.md). Where a
count here differs from that report, the later one is current.*

---

## 1. What Phase 6 was for

Not to add anything. To find out what was actually true about a system that had
been described, at the end of five phases, in language considerably more
confident than the evidence supported.

The objective, in the phase brief's words: *prove as much of the existing system
as technically possible, eliminate remaining correctness defects, execute real
integrations where credentials and dependencies are available, perform final
security, performance and reliability verification, and produce a reproducible
final release* — without claiming production readiness unless the required
evidence actually exists.

It does not. The release is classified accordingly in §20.

## 2. The finding that changed the phase

Phase 5 recorded pytest, ruff, mypy and Starlette as unavailable. The brief
required re-probing rather than inheriting that. They were available.

That one check converted large parts of the system from *read* to *executed*,
and the difference was not cosmetic. Running the project's own configured lint
step produced **210 violations**, and `ruff format --check` wanted to reformat
**81 of 153 files**. Both are CI steps. Every commit since Phase 0 would have
failed CI, which means **CI had never run.**

Then the test suite: `tests/conftest.py` imported FastAPI at module scope, so
one missing optional dependency aborted the entire pytest session — the suite
could not run at all. And 96 of the runnable tests are coroutines against an
uninstallable `pytest-asyncio`, which pytest 9 refuses to execute.

So Phase 6 began by discovering that the quality gates described in five phases
of documentation had never been executed once. Everything after that follows
from fixing that and then running them.

## 3. Environment

| | |
|---|---|
| Python | 3.11.15, with httpx, pydantic, pydantic-settings, PyJWT, starlette, uvicorn |
| Tooling | pytest 9.0.3, ruff 0.15.11, mypy 1.20.2 (uv-managed) |
| Database | PostgreSQL 16.13, real, local |
| Node | 22.22.2 with `tsc` |
| PyPI | **403 Forbidden** |
| npm registry | **403 Forbidden** |
| Docker daemon | **unavailable** — CLI present, socket absent |
| GitHub / Gemini credentials | **none, in any environment, in any phase** |

Uninstallable and therefore unexercised: fastapi, sqlalchemy, alembic, asyncpg,
structlog, pytest-asyncio, React, Vite, Vitest.

## 4. Defects found and fixed

Nine found. Eight fixed, one accepted with reasons. Each fix has a regression
test that **fails against the Phase 5 tree and passes against this one** —
verified by extracting the Phase 5 tarball and running the file against it:
**11 of 11 defect regressions failed there, and all pass here.**

(`tests/unit/test_phase6_regressions.py` holds a twelfth test. It pins the §12
scope decision — that nothing writes a finding-status transition — rather than a
defect, so it passes on both trees. That is the correct behaviour for a scope
pin and is noted here so the "fails on the old tree" claim is not overstated.)

| | Defect | Severity | How it was found |
|---|---|---|---|
| P6-1 | A superseded review run was resurrected as a success by its own worker | Medium | real PostgreSQL, concurrent backends |
| P6-2 | Model prose could forge a published comment's identity | **High** | attacking the AI → GitHub path |
| P6-3 | Open-redirect check rejected control characters only by accident | Medium | targeted payload sweep |
| P6-4 | Path-traversal check understood `/` but not `\` | Medium | targeted payload sweep |
| P6-5 | Throttled requests were answered 500 with no `Retry-After` | Medium | running the stack over real HTTP |
| P6-6 | The context byte ceiling was not a ceiling | Medium | parametrizing a test across scales |
| P6-7 | The backend test suite could not run at all | Medium | trying to run it |
| P6-8 | `installations.installed_by_user_id` was in the database, not the model | Low | two-directional schema parity |
| P6-9 | Three configured limits were read by nothing | Informational | AST sweep of every setting |

Two are worth stating at length.

### P6-2 — the one that mattered

Every review comment carries a hidden `<!-- codeguard-finding:<fingerprint> -->`
marker, and republication reads it back from GitHub to answer "have we already
said this?". The model's own description, impact, suggested fix and title were
interpolated into that same body — and `extract_marker` returned the **first**
match, while ours is appended last.

A model emitting a marker-shaped string therefore forged a comment's identity.
The forged fingerprint is then recorded as present on the pull request, so the
genuine finding that owns it is treated as already posted and is **never
published again**. Model output deciding which real findings a reviewer gets to
see — in a system whose entire premise is that model output decides nothing.

On a public repository the diff is attacker-controlled, and the diff is what the
model reads. This was reachable, not hypothetical.

Fixed in two independent layers: model-authored text has its HTML-comment
delimiters escaped before interpolation (rendering as literal text, so an
attempt stays visible rather than being silently deleted), and `extract_marker`
now reads the last marker, which is always ours. The second layer also repairs
reconciliation for any comment posted before the first shipped.

### P6-6 — a ceiling that was not one

`AI_MAX_CONTEXT_BYTES` bounded the header and the file blocks. The manifest —
one line for every changed file, included *and* excluded — was never counted.
The excluded half grows as the inverse of the budget: the tighter the ceiling,
the more files it excludes and the longer the list naming them.

Measured against the Phase 5 tree: a 3000-file pull request at the default
180,000-byte budget produced a **221,017-byte** document, 1.2× the ceiling, at an
estimated 63,148 tokens against a 60,000-token limit. The provider would have
rejected the request after charging for it — the exact outcome the setting
exists to prevent. The original test passed for months because it used a small
enough input for the manifest to hide inside the slack; it is now parametrized
across five scales.

## 5. What Phase 6 deliberately did not do

- **No new features.** No mutation endpoints, no new providers, no new
  infrastructure, no Redis, no Celery, no Kubernetes, no microservices. The
  modular monolith is unchanged.
- **No Phase 7.** Everything the existing architecture needs to function
  correctly was fixed in this phase.
- **No PEP 695 generics rewrite.** A linter's unsafe fix rewrote
  `class Page(BaseModel, Generic[T])` into the Python 3.12 form. The only
  interpreter in this environment that has the project's dependencies is 3.11,
  so the construct could not be executed or tested here. Shipping a construct
  the release process cannot exercise, on a linter's suggestion alone, is the
  kind of change this phase exists to prevent. Reverted, with the reasoning in
  the source.
- **No server defaults added to 26 NOT NULL columns.** For `head_sha`, `title`,
  `html_url` and `author_login` the absence *is* the guard: it catches a
  forgotten field rather than storing a plausible empty string.
- **No finding-status workflow.** See §12.

## 6. Tests

| Suite | Count | Status |
|---|---|---|
| Backend, excluding api and integration | **871** | **REAL LOCAL VERIFIED** — passed |
| `tests/api` + `tests/integration` | 72 functions | **BLOCKED — dependency unavailable** |
| PostgreSQL verification harnesses | **170** | **REAL LOCAL VERIFIED** — passed |
| Middleware over real HTTP | 41 (in the 871) | **REAL LOCAL VERIFIED** |
| Adversarial model output (§13) | 113 (in the 871) | **REAL LOCAL VERIFIED** |
| Frontend pure logic | 67 | **REAL LOCAL VERIFIED** (real `tsc` + real Node; Vitest BLOCKED). Phase 6 first reported 17 — an undercount from a scratch harness that compiled two of five test files; corrected in v0.6.1. |
| Frontend components | — | **BLOCKED — dependency unavailable** |

Net new in this phase: 141 backend tests (113 adversarial, 13 regression,
15 resource-limit) and 12 PostgreSQL race checks.

## 7. Verification classification by subsystem

| Subsystem | Classification |
|---|---|
| Diff parsing, LineMap, AST analysis | **REAL LOCAL VERIFIED** |
| Context builder and its budgets | **REAL LOCAL VERIFIED** |
| Finding validator, all 14 gates | **REAL LOCAL VERIFIED** |
| Comment rendering and marker identity | **REAL LOCAL VERIFIED** |
| Job queue and its concurrency | **REAL LOCAL VERIFIED** (real PostgreSQL, concurrent backends) |
| Review-run lifecycle and supersession | **REAL LOCAL VERIFIED** (real PostgreSQL) |
| Migrations, both directions | **REAL LOCAL VERIFIED** |
| Every dashboard SQL query and its numbers | **REAL LOCAL VERIFIED** |
| Authorization boundary (`ViewerScope`) | **REAL LOCAL VERIFIED** (in SQL) |
| Middleware: headers, CORS, CSRF, proxy, host, body size, rate limit | **REAL LOCAL VERIFIED** (over HTTP) |
| Webhook HMAC verification | **REAL LOCAL VERIFIED** (synthetic payloads) |
| Session lifecycle and CSRF tokens | **REAL LOCAL VERIFIED** |
| GitHub client: retries, error classification, pagination | **MOCK VERIFIED** |
| GitHub publisher: stale head, 422 salvage, reconciliation | **MOCK VERIFIED** |
| Gemini provider: request shaping, parsing, errors | **MOCK VERIFIED** |
| SQLAlchemy binding and session lifecycle | **BLOCKED — dependency unavailable** |
| FastAPI routes, DI, RFC 9457 rendering | **BLOCKED — dependency unavailable** |
| `alembic revision --autogenerate` | **BLOCKED — dependency unavailable** (parity checked by AST instead) |
| Frontend rendering | **BLOCKED — dependency unavailable** |
| Container build and two-process deployment | **BLOCKED — Docker unavailable** |
| GitHub App auth, token minting, real webhooks, real publication | **BLOCKED — credential unavailable** |
| Gemini: any real call | **BLOCKED — credential unavailable** |
| CI workflow execution | **BLOCKED — credential unavailable** |

**No row reads LIVE VERIFIED.** Nothing in this system has ever spoken to GitHub
or to an AI provider.

## 8. Security

Nine findings, classified in `docs/final-security-review.md`: 0 critical, 1 high,
5 medium, 2 low, 4 informational (of which one is accepted unchanged, one is
documented as a deliberate fail-open, and one is the scope decision in §12).

The high and the two most consequential mediums were all in the AI → GitHub
path, and all three were found by attacking it rather than reading it.

What holds structurally, and is asserted rather than assumed: the application
never executes user code (zero `eval`/`exec`/`compile`/`__import__`/shell calls,
verified across the whole tree); no repository is cloned; no whole file reaches
a model; the publisher's type signature admits only `ValidatedFinding` and the
module does not import `RawFinding`; authorization is a SQL join, not a filter,
and a cross-tenant id returns 404; sessions are opaque server-side tokens stored
as SHA-256.

## 9. Performance

Local timings only, median of 15 runs after warm-up, on one shared cloud
container. **These are not SLA numbers.**

| Stage | small | mid | large |
|---|---|---|---|
| `parse_patch` + `build_line_map` | 0.07 ms | 0.38 ms | 3.59 ms (100 hunks) |
| Python AST analysis | 0.19 ms | 4.93 ms | 37.61 ms (92 KB) |
| `build_review_context` | 0.04 ms | 0.96 ms (40 files) | 4.72 ms (3000 files) |
| `validate` | 0.03 ms | 0.66 ms (25 claims) | 4.64 ms (200 claims) |

Every stage is linear in its input. The complete CPU-bound pipeline for a
3000-file pull request is under 50 ms.

**Not measured and not estimated:** the GitHub round trips and the AI provider
call, which dominate a real review's wall-clock time. No end-to-end latency
figure appears anywhere in this release, and none should be inferred from the
table above.

## 10. Reliability

Verified by execution: the job queue's lease fencing under 8 concurrent workers;
duplicate enqueue under 6 concurrent inserts producing one job and one audit
row; ten stale-recovery cycles never yielding two owners; the sweeper racing a
claim; dead-lettering; and — new in this phase — the review-run lifecycle under
a 20-round concurrent supersede/finish race.

Verified by mock: GitHub 5xx and timeout retried with jittered exponential
backoff; 403-with-rate-limit-headers classified as throttling and 403-without as
permanent; 422 on review creation salvaged to a safer subset and then to a
summary that loses no finding; an ambiguous write reconciled against GitHub's own
comment list rather than blindly retried.

Not verified: anything involving a real network, a real container, or a real
orchestrator.

## 11. Reproducibility

The verification harnesses were moved out of scratch space into
`backend/verification/` and parameterized by environment variable, because a
verification you cannot re-run is an assertion.

```bash
cd backend
ruff check app tests && ruff format --check app tests
mypy app --ignore-missing-imports
pytest tests/ --ignore=tests/api --ignore=tests/integration      # 871 passed
python3 verification/run_all.py                                   # 170 passed
python3 verification/benchmark.py                                 # local timings
cd ../frontend && npx tsc --noEmit
```

`run_all.py` exits non-zero if any harness fails, so it is usable as a gate.

One harness bug was found and fixed while doing this: `verify_dashboard_sql.py`
pinned its time window to a hardcoded date while inserting fixtures with the
database's `now()`. Two days after it was written, eleven correctness checks
silently returned zero. It now follows the real clock. A verification harness
that reports a false failure on a Thursday is a harness nobody trusts on a
Friday.

## 12. Scope decision: the finding-status workflow

`findings.status` has four values. `finding_events` is a complete audit trail
with an actor and a user/system source. The API accepts all four as a filter.
**Nothing writes any of it.** Every finding is created `open` and stays there.

Phase 6 did not build the mutation endpoints. Triage is a product workflow — who
may dismiss a finding, what a dismissal means on re-review, whether it is scoped
per repository or per fingerprint — and none of that is decided anywhere in this
project's documentation. Building the endpoint because the schema anticipates it
would be adding a feature to make a table look used.

The cost, stated rather than hidden: **any statistic derived from finding status
is structurally zero.** A standing test fails if a writer appears without this
note being updated.

## 13. On accuracy claims

This system makes none, and must not.

There is no labelled benchmark dataset of real vulnerabilities in real pull
requests, and without one there is no valid methodology for an accuracy,
precision, recall or false-positive figure. An audit check fails if any
user-visible string claims one.

What can honestly be reported is what this pipeline's own gates did: how many
claims the model emitted, how many survived validation, how many were published,
and which gate rejected each of the rest. That is a statement about the
validation layer, not about the model's correctness, and the dashboard's wording
was written to keep the two apart — a rejected claim is never labelled a false
positive, because the validator rejects claims that are *unverifiable*, which is
a different thing from claims that are *wrong*.

## 14. Documentation

- `architecture.md` — updated for the Phase 6 changes
- `README.md` — updated; states the release classification plainly
- `docs/final-verification-matrix.md` — what ran, where, and with what result
- `docs/final-security-review.md` — threat model and all nine findings
- `docs/release-checklist.md` — what was verified, and what must be before production
- `docs/project-report.md` — the academic write-up
- `docs/deployment.md` — corrected (it documented a limit that nothing enforced)
- `backend/verification/` — every harness, re-runnable

## 15. Files changed

**Production code:** `app/review/publisher.py`, `app/review/context.py`,
`app/review/service.py`, `app/review/validator.py`, `app/ai/budget.py` (new),
`app/core/errors.py`, `app/core/middleware.py`, `app/core/correlation.py`,
`app/core/config.py`, `app/main.py`, `app/api/health.py`, `app/api/v1/auth.py`,
`app/api/scope.py`, `app/db/models/installation.py`,
`app/db/repositories/reviews.py`, `app/analysis/diff/parser.py`,
`app/auth/oauth.py`, `app/worker.py`, and small type fixes elsewhere.

**Tests:** `tests/conftest.py` (made the suite runnable at all),
`tests/review/test_adversarial_model_output.py` (new, 113),
`tests/unit/test_phase6_regressions.py` (new, 12),
`tests/security/test_resource_limits.py` (new, 15),
`tests/security/test_middleware_over_http.py` (new, 41),
`tests/review/test_phase3_invariants.py` (an invariant that asserted a setting's
value now asserts that something enforces it).

**Verification:** `backend/verification/` — 9 harnesses plus `run_all.py`.

## 16. Remaining risks

1. **Nothing has spoken to GitHub or Gemini.** Every claim about those
   integrations rests on fakes. The most likely place for a surprise is the
   exact shape of GitHub's inline-comment positioning, which is notoriously
   particular and which no mock can settle.
2. **SQLAlchemy sits between the verified SQL and the application.** The
   statements are proven; the ORM's binding and session lifecycle around them
   are not. `text()` with named parameters is a thin layer, but it is a layer.
3. **The container has never been built.** A Dockerfile that reads correctly and
   a Dockerfile that builds are different claims.
4. **72 test functions have never executed.** They are the ones covering the
   FastAPI request path and DB integration — precisely the seams the other tests
   cannot reach.
5. **`_observed_head_sha` fails open.** A failed pre-publication head read lets
   publication proceed. Accepted, because failing closed converts a GitHub blip
   into a total review outage; documented so it is a decision rather than an
   oversight.
6. **The prompt is unvalidated against a real model.** No amount of local
   testing establishes that the instructions produce useful findings.

## 17. Remaining blockers

- **BLOCKED — dependency unavailable:** PyPI 403, npm 403. fastapi, sqlalchemy,
  alembic, asyncpg, structlog, pytest-asyncio, React, Vite, Vitest.
- **BLOCKED — Docker unavailable:** no daemon socket. No image has been built.
- **BLOCKED — credential unavailable:** no GitHub App, no GitHub OAuth app, no
  Gemini API key. No webhook, no token, no model call, no comment.
- **BLOCKED — dependency unavailable:** no browser tooling for the frontend.

## 18. What a reader should take from this

The parts of this system that are its own work — diff parsing, line mapping, AST
analysis, context budgeting, the fourteen validation gates, the queue, the
schema, the authorization boundary, the middleware — have been executed and
verified, several of them against a real database with genuinely concurrent
clients, and they are correct as far as execution can show.

The parts that depend on somebody else's service have never run. That is not a
small remainder. It is the half of the system where the interesting failures
live.

## 19. Honest summary of the phase

Phase 6 found that the quality gates described across five phases had never
executed, ran them, and then found nine defects — two of them in the path that
decides what a model is allowed to say on someone's pull request. It fixed
eight, accepted one with reasons, removed two configuration knobs that turned
nothing, declined to build a feature the schema was waiting for, declined to
ship a language construct the release process could not exercise, and corrected
one of its own earlier claims after testing disproved it.

It did not make the system production-ready, because nothing available in this
environment could.

## 20. Final classification

### DEPLOYABLE-SHAPED / NOT PRODUCTION-VERIFIED

The architecture is coherent, the code its own tests can reach is verified, the
database layer is proven against real PostgreSQL under real concurrency, and the
security-relevant paths that could be attacked locally were attacked.

**Not production-verified**, because:

- no GitHub integration has ever been exercised — **BLOCKED — credential unavailable**
- no AI provider call has ever been made — **BLOCKED — credential unavailable**
- no container has ever been built — **BLOCKED — Docker unavailable**
- 72 test functions have never run — **BLOCKED — dependency unavailable**
- the frontend has never rendered — **BLOCKED — dependency unavailable**

Mock verified is not integration verified. A configured credential is not a
working integration. Deployable-shaped is not production-proven.

`docs/release-checklist.md` section B is what closes the gap. Until every box in
it is ticked in a real environment, this classification stands.
