# Phase 4 Completion Report

**CodeGuard AI — Dashboard API, Web Dashboard, Review History, Quality Metrics**

Date: 2026-09-16 · Baseline: `architecture.md` (30 sections + appendices A–F)

---

## 1. Files created and modified

### Backend — new

| File | Lines | Purpose |
|---|---:|---|
| `app/api/pagination.py` | 198 | Keyset cursors: encode/decode, clamping, page assembly. Framework-free. |
| `app/api/filters.py` | 325 | Typed allow-listed filters, half-open UTC ranges, bucket snapping. Framework-free. |
| `app/api/scope.py` | 119 | `ViewerScope` seam + the production fail-closed guard. |
| `app/db/repositories/dashboard.py` | 1,148 | **Every dashboard query.** Pure: returns SQL + params. |
| `app/db/read.py` | 74 | The only place a dashboard query meets a session. Pins the session to UTC. |
| `app/schemas/dashboard.py` | 654 | Explicit response models; the metric vocabulary. |
| `app/api/v1/dashboard/__init__.py` | 35 | Router aggregation. |
| `app/api/v1/dashboard/common.py` | 82 | Scope dependency, page args, error redaction. |
| `app/api/v1/dashboard/analytics.py` | 364 | Overview, trends, severity/category/rejection/anchor, health, coverage, activity. |
| `app/api/v1/dashboard/repositories.py` | 112 | A2, A3. |
| `app/api/v1/dashboard/reviews.py` | 332 | A5–A8: pull requests and review runs. |
| `app/api/v1/dashboard/findings.py` | 284 | A11, A12. |
| `app/api/v1/dashboard/system.py` | 94 | A20, A23: integration status and settings. |
| `alembic/versions/…0004_dashboard_indexes.py` | 86 | Four justified indexes. |

### Backend — modified

| File | Change |
|---|---|
| `app/ai/contracts.py` | `ReviewResult.raw_claim_count` / `.dropped_claim_count` — **funnel-integrity fix**. |
| `app/ai/providers/gemini.py` | `_coerce_findings` now *returns* what it dropped instead of only logging it. |
| `app/db/repositories/reviews.py` | `persist_findings` returns `PersistedFindings` (rows written, not claims offered). |
| `app/review/service.py` | Funnel now records six distinct stages that sum. |
| `app/core/config.py` | `dashboard_allow_unauthenticated_reads`, `dashboard_max_page_size`; both surfaced in `safe_summary()`. |
| `app/api/v1/router.py` | Mounts the dashboard routers. |
| `tests/review/test_gemini_provider.py` | Two funnel-integrity tests. |

### Frontend — new

`types/api.ts` (632) · `api/endpoints.ts` (194) · `hooks/useApiQuery.ts` (116) ·
`hooks/useUrlFilters.ts` (121) · `utils/format.ts` (111) · `utils/status.ts` (164) ·
`components/ui/Table.tsx` (110) · `components/ui/primitives.tsx` (427) ·
`components/data/AsyncSection.tsx` (55) · `components/domain/badges.tsx` (96) ·
`charts/{ChartFrame,BarList,TrendChart,ValidationFunnel}.tsx` (485 total) ·
nine new pages (2,287 total) · `tsconfig.test.json` · `scripts/run-tests.mjs` ·
three test files + harness.

### Frontend — modified

`styles/tokens.css` and `tailwind.config.ts` (state + series tokens) ·
`app/routes.tsx` · `layouts/AppShell.tsx` · `pages/DashboardPage.tsx` ·
`pages/SystemPage.tsx` · `api/client.ts` · `package.json`.
Removed: `components/data/StateView.tsx`, `components/domain/SeverityBadge.tsx`
(superseded by `primitives.tsx` and `badges.tsx`).

### Documentation

`docs/phase-4.md` · `docs/dashboard-metrics.md` · `README.md` ·
`.github/workflows/ci.yml`.

---

## 2. Phase 0–3 audit

### Issues found and fixed

**1. The validation funnel did not add up (Phase 3).** `findings_emitted` was
`len(result.findings)` — the claims that *parsed*. Items the Gemini provider
could not coerce were logged and then invisible. Separately,
`findings_dropped` was set to the same value as `findings_dashboard_only`, so
two columns held one number and the funnel's stages could not sum.

This is a dashboard-integrity bug, not a cosmetic one: a funnel whose stages do
not add up looks authoritative and is wrong. Fixed by having the provider
report `raw_claim_count` and `dropped_claim_count`, and the orchestrator record
six stages that satisfy
`emitted = dropped + validated + rejected`.

**2. `findings_persisted` counted claims, not rows (Phase 3).** Two rejected
claims can collapse onto one row via the `(review_run_id, fingerprint)` unique
index, so the stored count could exceed the number of rows a query can find —
a number that cannot be reconciled with the table it describes. Fixed:
`persist_findings` now returns `PersistedFindings` with `validated_rows` and
`rejected_rows` counted from actual insertions.

**3. `Sequence` imported but unused** in the new query module — removed.

### Issues checked and found correct

| Brief item | Finding |
|---|---|
| §4A LineMap vs GitHub commentability | Publication state is read **only** from `review_comments` rows. No query or schema field infers it from LineMap. Asserted by a test. |
| §4B anchor relocation | `claimed_line_start`, `resolved_line` and `anchor_offset` are all persisted and all surfaced separately. No percentage is derived. |
| §4C rejected ≠ false positive | Enforced in schema field names, API vocabulary, UI labels, and a wording test. |
| §4D semantic detection | No endpoint or label claims a finding is proven. |
| §4E partial success | First-class status everywhere; never rendered as "Passed". |
| §4F stale reviews | "Current review" is anchored on `head_sha`, not time. Verified against real PostgreSQL. |
| §4G duplicate attempts | `COUNT(DISTINCT pull_request_id)` for PR metrics, `COUNT(*)` for attempts. Verified with the §42 fixture. |
| Schema vs migration `0003` | Applied to a real PostgreSQL 16 and verified — 20 constraint/SQL probes pass. |

### Intentionally deferred

| Deferred | Why |
|---|---|
| **Dashboard authentication** | `users`, `user_installation_access` and GitHub OAuth are Phase 5 schema and endpoints. Shipping a placeholder session would be worse than none — the next phase would have to find and remove a mechanism that *looks* like security. The seam exists (`ViewerScope`), every query applies it, and the API **fails closed** (503) outside development. |
| **Dashboard mutations** | `PATCH /findings/{id}`, `POST /re-review` and settings writes are actions attributed to a user. Without users there is no one to attribute them to. |
| **OpenAPI type generation** | The generator cannot be installed (npm 403). Replaced with a contract test that compares the TypeScript interfaces against the Pydantic models field by field. |
| **`quality_score`** | The column exists; nothing computes it. Showing a null or a placeholder would be worse than omitting the tile. |

---

## 3. Backend APIs

All read-only, all keyset-paginated where a collection can grow, all scoped.

| Method | Endpoint | Purpose | Filters | Pagination | Response |
|---|---|---|---|---|---|
| GET | `/api/v1/overview` | KPI tiles | `from`, `to`, `repository_id` | — | `OverviewResponse` |
| GET | `/api/v1/analytics/findings-by-severity` | Severity chart | range, `repository_id` | — | `CountBucket[]` |
| GET | `/api/v1/analytics/findings-by-category` | Category chart | range, `repository_id` | — | `CountBucket[]` |
| GET | `/api/v1/analytics/rejection-reasons` | Rejections by gate | range, `repository_id` | — | `CountBucket[]` |
| GET | `/api/v1/analytics/anchor-distribution` | Claimed vs resolved | range, `repository_id` | — | `CountBucket[]` |
| GET | `/api/v1/analytics/trends` | Gap-filled series | range, `bucket`, `repository_id` | — | `TrendResponse` |
| GET | `/api/v1/analytics/processing-health` | Reliability | range | — | `ProcessingHealthResponse` |
| GET | `/api/v1/analytics/coverage` | Files analysed/skipped | range, `repository_id` | — | `CoverageResponse` |
| GET | `/api/v1/analytics/recent-activity` | Latest attempts | `limit` ≤ 50 | — | `RecentActivityEntry[]` |
| GET | `/api/v1/repositories` | Repository list | `q`, `is_active` | `(full_name, id)` | `Page<RepositorySummary>` |
| GET | `/api/v1/repositories/{id}` | Detail | — | — | `RepositoryDetailResponse` |
| GET | `/api/v1/pull-requests` | PR list | `repository_id`, `state[]`, `run_status[]`, `q`, range | `(updated_at, id)` DESC | `Page<PullRequestSummary>` |
| GET | `/api/v1/pull-requests/{id}` | Detail + every attempt | — | — | `PullRequestDetailResponse` |
| GET | `/api/v1/review-runs` | Run list | `repository_id`, `pull_request_id`, `run_status[]`, `include_superseded`, range | `(created_at, id)` DESC | `Page<ReviewRunSummary>` |
| GET | `/api/v1/review-runs/{id}` | Review deep-dive | — | — | `ReviewRunDetailResponse` |
| GET | `/api/v1/findings` | Findings explorer | `repository_id`, `pull_request_id`, `review_run_id`, `severity[]`, `category[]`, `status[]`, `mapping_status[]`, `validation`, `publication`, `rejection_reason`, `q`, range, `include_total` | `(created_at, id)` DESC | `Page<FindingSummary>` |
| GET | `/api/v1/findings/{id}` | Finding detail | — | — | `FindingDetailResponse` |
| GET | `/api/v1/integration/status` | Installations | — | — | `IntegrationStatusResponse` |
| GET | `/api/v1/settings` | Non-secret config | — | — | `SettingsResponse` |

**Pagination contract** (architecture.md §17.2): `?limit=&cursor=` →
`{ items, next_cursor, total? }`. Default 25, ceiling 100, clamped server-side.
`total` is opt-in. Callers over-fetch `limit + 1` so "is there more?" needs no
`COUNT`.

**Error contract**: RFC 9457 problem details throughout. 404 for anything
outside scope (not 403 — a 403 confirms the resource exists), 422 for an
invalid filter or cursor, 503 when the deployment has no authentication.

---

## 4. Database queries

### Aggregation strategy

Every number is a SQL aggregate. Nothing is summed in Python over a page.

- **`overview_summary`** is a single query with independently scoped and ranged
  sub-selects. One round trip, and the tiles cannot describe different
  populations — the bug that makes a dashboard say "12 reviews, 40 findings"
  when the 40 came from a different window.
- **"Current review"** is a `LATERAL` join anchored on `head_sha`, not time, so
  a superseded run can never present itself as the current result.
- **`review_trends`** gap-fills with `generate_series` and a `LEFT JOIN`, so a
  quiet day is a zero rather than a missing point.
- **`findings_count`** is a separate, opt-in query.

### Indexes added — migration `0004`

| Index | Query it serves | `EXPLAIN` verdict |
|---|---|---|
| `ix_findings_created_at_id` | Findings explorer, unfiltered | Index Only Scan |
| `ix_review_runs_created_at_id` | Run list, recent activity | Index Only Scan |
| `ix_pull_requests_updated_at_id` | PR list, unfiltered | Index Only Scan |
| `ix_findings_rejected_reason` (partial) | Rejection chart, `validation=rejected` | Bitmap Index Scan |

Seven other dashboard access paths were checked against the existing schema and
found already served — `uq_repositories_full_name`,
`ix_repositories_installation_id`,
`uq_review_runs_pull_request_id_head_sha_attempt`,
`ix_changed_files_pull_request_id_head_sha`, and the three Phase 3
repository-first finding indexes. Nothing was added speculatively.

### Performance considerations

Keyset over offset; bounded page sizes; opt-in counts; `LATERAL` joins so
per-row aggregates use the existing indexes; partial indexes where a query only
ever touches a subset; and no N+1 — a list page is one query, a detail page is
two or three.

---

## 5. Dashboard pages

| Route | What it does |
|---|---|
| `/` | Six KPI tiles each carrying its own definition · trend chart · validation funnel · severity, category and rejection-gate charts · attention panel for failed/partial/limited-coverage runs · recent activity. |
| `/repositories` | Searchable list with open findings, attempts and last review; keyset paging. |
| `/repositories/:id` | Header stats, details, severity breakdown, coverage with skip reasons, recent pull requests. |
| `/pull-requests` | List showing each PR's **current** review, attempt count, findings and published comments; chip filters for PR state and review outcome. |
| `/pull-requests/:id` | Current review · **full run timeline including superseded attempts** · findings grouped with claimed/resolved lines · PR metadata. |
| `/review-runs` | Reliability tiles, error breakdown, filterable run history. |
| `/review-runs/:id` | The explainability page: status and caveats, validation funnel, coverage, changed-files table with skip reasons, findings from that run. |
| `/findings` | The workhorse: eight filter dimensions, server-side search, opt-in total, keyset paging, URL-held state. |
| `/findings/:id` | Model text · claimed vs resolved location · the one stored anchor line · provenance · event history. |
| `/settings` | Non-secret configuration, installations, and **the metric definitions in the product**. |
| `/system` | Phase 0 health page, retained. |

Every page has loading (dimension-matched skeletons), empty (distinguishing
"nothing exists" from "nothing matches"), and error (message + correlation id +
retry) states. Errors are per-section.

---

## 6. Metrics

Full table in [`docs/dashboard-metrics.md`](dashboard-metrics.md). Summary:

| Metric | Definition | Source | Calculation | Population | Known limitation |
|---|---|---|---|---|---|
| Claims emitted | Items in the model's array | `review_runs.findings_emitted` | `SUM` | Runs in range | Runs predating the funnel fix under-report |
| Claims dropped | Items the provider could not parse | `review_runs.findings_dropped` | `SUM` | Runs in range | No row exists for these |
| Validated findings | Claims passing all 14 gates | `findings` | `COUNT WHERE rejection_reason IS NULL` | Findings in range | **Not proven defects** |
| Rejected claims | Claims failing a gate | `findings` | `COUNT WHERE rejection_reason IS NOT NULL` | Findings in range | **Not false positives** |
| Published comments | Comments GitHub confirmed | `review_comments` | `COUNT` | Runs in range | Stored state only, never inferred |
| Pull requests reviewed | Distinct PRs with a completed review | `review_runs` | `COUNT(DISTINCT pull_request_id)` | Runs in range | Retries count once |
| Review attempts | Runs started | `review_runs` | `COUNT(*)` | Runs in range | Deliberately different from the row above |
| Completion rate | Clean ÷ terminal | `review_runs` | `succeeded / terminal` | Terminal runs | Excludes superseded and in-flight |
| Anchor offset | `resolved − claimed` | `findings.anchor_offset` | Bucketed | Validated findings | **Not accuracy** — a property of the relocation policy |
| Coverage | Files changed / analysed / skipped | `review_runs`, `changed_files` | `SUM`, grouped | Runs in range | **Not a percentage** — reasons are not comparable |

---

## 7. UI architecture

- **Design system**: the Phase 0 CSS-custom-property tokens, extended with
  `--state-*` and `--series-*`. Tailwind consumes the variables, so a theme
  switch stays one attribute on `<html>`.
- **Components**: `ui/primitives.tsx` (page headers, stat tiles, status pills,
  definition lists, controls, skeleton/empty/error), `ui/Table.tsx`,
  `data/AsyncSection.tsx`, `domain/badges.tsx`, `charts/*`.
- **Routing**: the existing `createBrowserRouter`, extended to eleven routes
  plus two redirects. Still eagerly imported.
- **API client**: the existing `apiFetch`; endpoints added as typed wrappers
  with one shared query-string builder.
- **State**: `useApiQuery` — a discriminated union, not three booleans — for
  server state; `useUrlFilters` for filter state in the URL. No global store.
- **Charts**: inline SVG and flexbox, each inside `ChartFrame` which supplies
  `role="img"` with a data-derived summary and a `<details>` fallback table.

---

## 8. Security

| Control | Implementation |
|---|---|
| **Fails closed without auth** | `resolve_scope` raises 503 outside development unless explicitly opted in; asserted by tests and by a CI step. |
| **Scope in SQL** | Every query takes a `ViewerScope`; an empty scope emits `FALSE`, not an absent clause. Asserted for all 14 builders. |
| **No secrets in responses** | Explicit schemas; no `model_validate` on an ORM object. Tests assert no secret-shaped column is selected and no secret-shaped field is declared. |
| **Settings is read-only** | No POST/PATCH/PUT/DELETE in the whole dashboard package — asserted by a test. |
| **SQL injection** | Values bound; structure chosen from module constants. An AST test asserts no f-string interpolates anything but a module constant, and that the two interpolated keyword arguments (`alias`, `column`) only ever receive literals. Verified against live PostgreSQL with `'; DROP TABLE findings; --`. |
| **LIKE metacharacters** | Escaped, so `%` matches a percent sign rather than triggering a full scan. Verified live. |
| **No arbitrary file access** | Finding endpoints take an id, never a path — asserted by an AST test on the route signatures. |
| **Source exposure** | At most the one stored `anchor_snippet`, numbered with the *resolved* line. Phase 3 stores no repository source, so there is nothing else to leak. |
| **Cursor safety** | Opaque, length-capped, strictly re-validated, bound as parameters. Deliberately unsigned, with the reasoning documented. |
| **Error redaction** | `review_runs.error_message` is collapsed and truncated before rendering; no stack trace reaches a browser. |

---

## 9. Testing

| Check | Result | Environment |
|---|---|---|
| Backend unit + analysis + review tests | **607 cases, 0 failed** | ✅ executed — custom harness (pytest unavailable) |
| Dashboard pure-layer tests (pagination, filters, scope, security, contract) | **103 cases, 0 failed** (subset of the 607) | ✅ executed — same harness |
| Dashboard SQL + analytics correctness | **55 checks, 0 failed** | ✅ executed — **real PostgreSQL 16.13** |
| Phase 3 repository SQL | **20 checks, 0 failed** | ✅ executed — real PostgreSQL 16.13 |
| Migrations `0001`–`0004` apply | ✅ pass | ✅ executed — real PostgreSQL 16.13 |
| Migration downgrade to zero tables | ✅ pass | ✅ executed — real PostgreSQL 16.13 |
| Index usage (`EXPLAIN`, `enable_seqscan=off`) | ✅ all four used | ✅ executed — real PostgreSQL 16.13 |
| Frontend TypeScript typecheck | **0 errors** (app + tests, project's strict options) | ⚠️ executed with a **newer global `tsc`** against **hand-written ambient stubs** for `react`/`react-router-dom` — the real `@types/*` cannot be installed |
| Frontend tests | **47 cases, 0 failed** | ✅ executed — compiled by `tsc`, run under Node 22 |
| Python syntax (`ast.parse`, all files) | 0 errors | ✅ executed |
| Secret scan (private-key markers) | 0 matches | ✅ executed |
| `pytest` | **BLOCKED** | ❌ cannot install (PyPI 403) |
| `ruff`, `mypy` | **BLOCKED** | ❌ cannot install (PyPI 403) |
| `npm ci`, `npm run build`, `vitest` | **BLOCKED** | ❌ cannot install (npm 403) |
| FastAPI route execution / `TestClient` | **BLOCKED** | ❌ FastAPI cannot be installed |
| SQLAlchemy session and transaction behaviour | **BLOCKED** | ❌ SQLAlchemy cannot be installed |
| React component rendering | **BLOCKED** | ❌ React cannot be installed |
| Live GitHub | **NOT PERFORMED** | no App, no installation, no token |
| Live Gemini | **NOT PERFORMED** | no API key, no request made |

### Test inventory

| Suite | Functions | Executed? |
|---|---:|---|
| `tests/unit/` | 135 | ✅ |
| `tests/analysis/` | 114 | ✅ |
| `tests/review/` | 124 | ✅ |
| `tests/dashboard/` (new) | 75 | ✅ |
| `tests/api/` | 28 | ❌ needs FastAPI |
| `tests/integration/` | 44 | ❌ needs SQLAlchemy + asyncpg |
| **Total defined** | **520** | 448 executed → **607 parametrised cases** |
| Frontend | 47 | ✅ |

### How the SQL was verified

Not by hand-copying a schema. A 235-line shim implements just enough of
`sqlalchemy` and `alembic.op` to execute the real migration modules and emit
their DDL; and `verify_dashboard_sql.py` calls the **real query-builder
functions**, binds their parameters, and runs the resulting SQL against a live
PostgreSQL 16.13.

**Limitation, stated rather than glossed:** parameters are inlined as literals
by that harness because asyncpg is unavailable. The application binds them as
real parameters. So the SQL and the aggregation logic are verified; SQLAlchemy's
binding is not.

### Analytics correctness (brief §42) — all verified live

| Fixture | Expected | Result |
|---|---|---|
| PR A (2 attempts, 2 commits) + PR B (1 attempt) | unique PRs = 2, runs = 3 | ✅ |
| 10 claims → 6 validated, 4 rejected, 5 published, 1 unparseable | four distinct numbers | ✅ 6 / 4 / 5 / 1 |
| Superseded run for PR A's old commit | never the current review | ✅ current = the new-commit run |
| Both attempts in PR history | visible, stale flagged | ✅ |
| Duplicate fingerprint insert | blocked by the unique index | ✅ 1 row |
| Empty time window | zeros, not all-time totals | ✅ |
| Snapped day range | exactly one bucket per day, gap-filled | ✅ |

---

## 10. Known limitations

1. **The dashboard API has no authentication.** By design for this phase, and
   it fails closed: 503 outside development unless an operator explicitly opts
   in. Phase 5's job.
2. **FastAPI routes were never executed.** They are AST-parsed, cross-reference
   checked (every imported name resolves), and their logic lives in pure modules
   that *are* tested — but no request has been served.
3. **SQLAlchemy parameter binding and transaction behaviour are unverified.**
4. **No React component has been rendered.** The pure modules are tested; the
   components are typechecked against ambient stubs only.
5. **The frontend typecheck used hand-written React typings.** It catches wrong
   props, wrong hook arguments, missing fields and bad unions in application
   code. It cannot catch an error inside React's own types.
6. **`npm run build` has not been run.** The bundle has never been produced.
7. **`ruff`, `mypy`, `pytest` and `vitest` all remain unavailable.**
8. **No live GitHub or Gemini call has been made in any phase.**
9. **Semantic correctness of findings remains unproven** — no benchmark exists,
   and the dashboard publishes no accuracy figure as a result.
10. **`quality_score` is unused.** The column exists; nothing computes it.
11. Keyset paging is **forward-only**. "Start over" is offered rather than a
    "Previous" button that would need a client-side cursor stack.

---

## 11. Exit criteria

| Criterion | Status |
|---|---|
| Phase 0–3 audit completed | ✅ §2 |
| Concrete Phase 3 blockers fixed | ✅ 3 fixed (funnel integrity ×2, unused import) |
| Dashboard APIs implemented | ✅ 19 endpoints |
| Real PostgreSQL data used | ✅ verified against live PostgreSQL 16.13 |
| No fake production analytics | ✅ no `Math.random`, no fixtures in `app/` or `src/` |
| Dashboard summary implemented | ✅ `/` |
| Repository page implemented | ✅ `/repositories`, `/repositories/:id` |
| PR list implemented | ✅ `/pull-requests` |
| PR detail implemented | ✅ `/pull-requests/:id` |
| Review history implemented | ✅ `/review-runs` + per-PR timeline |
| Finding explorer implemented | ✅ `/findings` |
| Finding detail implemented | ✅ `/findings/:id` |
| Rejected-claim visibility implemented | ✅ filter, chart, badge, detail panel |
| Review coverage implemented | ✅ with skip reasons, no percentage |
| Validation/publication metrics implemented | ✅ funnel + publication badges |
| Anchor metrics accurately labelled | ✅ buckets, never "accuracy" |
| Partial/stale reviews represented correctly | ✅ distinct statuses + caveats |
| Pagination implemented | ✅ keyset, clamped, tested |
| Filtering implemented | ✅ allow-listed, typed, SQL-backed |
| Backend aggregation implemented | ✅ all numbers are SQL aggregates |
| API response schemas implemented | ✅ explicit, field-by-field mapping |
| Frontend TypeScript types implemented | ✅ no `any`; contract test vs backend |
| Loading states implemented | ✅ dimension-matched skeletons |
| Empty states implemented | ✅ "nothing exists" vs "nothing matches" |
| Error states implemented | ✅ per-section, with correlation id and retry |
| Responsive UI implemented | ✅ scroll + column priority + collapsing nav |
| Accessibility requirements addressed | ✅ semantic HTML, focus, `aria-pressed`, chart tables, never colour alone |
| Security checks implemented | ✅ §8 |
| Analytics correctness tests implemented | ✅ §42 fixtures verified live |
| Frontend behaviour tests implemented | ✅ 47 cases (not snapshots) |
| Backend API tests implemented | ⚠️ **partial** — pure layers fully tested; route handlers not executed (FastAPI unavailable) |
| Existing Phase 0–3 tests remain intact | ✅ 607 cases pass; no test weakened |
| Documentation updated | ✅ `phase-4.md`, `dashboard-metrics.md`, README, CI |
| No secrets exposed | ✅ asserted by tests and a clean scan |
| No semantic AI accuracy claims without benchmark | ✅ asserted by a wording test |

**One criterion is partial and is marked as such** rather than claimed.

---

## 12. Next phase

**Phase 5 — Authentication, analytics hardening, security hardening and
deployment readiness.** The first item is the one Phase 4 deliberately left
open, and the seam is already in place:

1. `users` and `user_installation_access` tables; GitHub OAuth (`/auth/github/*`);
   session cookies.
2. Replace the body of `resolve_scope` — and nothing else — so every existing
   query becomes per-user scoped.
3. Retire `DASHBOARD_ALLOW_UNAUTHENTICATED_READS`.
4. Dashboard mutations, now that there is a user to attribute them to.
5. Rate limiting, security headers review, deployment.

Not implemented now.
