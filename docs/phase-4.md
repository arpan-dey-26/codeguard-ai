# Phase 4 — Dashboard API and the developer-facing UI

## Objective

Turn the review data Phases 1–3 persist into something a developer can use,
without overstating what any of it means.

```
PostgreSQL
  ↓ query builders     pure: SQL + params, no session         app/db/repositories/dashboard.py
  ↓ scope              fails closed outside development       app/api/scope.py
  ↓ filters            allow-listed, typed, validated         app/api/filters.py
  ↓ keyset pagination  cursor = position, never an offset     app/api/pagination.py
  ↓ routes             explicit response schemas              app/api/v1/dashboard/
  ↓ React + TypeScript URL-held filters, per-section states   frontend/src
```

## The two decisions that shape everything else

### 1. Building a query is separate from running one

Each function in `app/db/repositories/dashboard.py` returns a `Query` — SQL
text plus a parameter dict — and `app/db/read.py` executes it. That is not
indirection for its own sake. It means every aggregate can be verified by
running the *exact* SQL the application runs, against a real PostgreSQL,
without an ORM or an ASGI server. An analytics layer whose correctness rests on
"the ORM probably emitted what I meant" is not verifiable, and analytics that
cannot be verified should not be shown.

Values are always bound. Query *structure* — which WHERE clause, which ORDER
BY, which bucket width — is selected from literals written in that file, keyed
by a token `app/api/filters.py` has already checked against a closed list.
There is no path where caller text becomes SQL.

### 2. The dashboard fails closed

Architecture.md §17.5 requires every dashboard endpoint to be scoped to the
caller's installations, derived from a session. Phase 4 does not build that:
`users`, `user_installation_access` and GitHub OAuth are Phase 5.

Shipping a *placeholder* session — a hard-coded user, a dev token, a header the
client sets — would be worse than shipping none, because the next phase would
have to find and remove a mechanism that *looks* like security.

So `app/api/scope.py` provides the seam and refuses to serve without it:

- `development` / `test` → an unrestricted scope;
- anything else → **503**, unless the operator explicitly sets
  `DASHBOARD_ALLOW_UNAUTHENTICATED_READS`.

A deployment that forgets authentication returns an error that says so, rather
than quietly publishing every monitored repository's review data. The flag's
state is reported in `safe_summary()` and on the settings page, so "we turned
it on and forgot" is visible rather than assumed.

Every query takes a `ViewerScope` and applies it in SQL, so Phase 5 changes how
a scope is *built* and nothing about how it is *used*.

## API surface

All read-only. Phase 4 adds no mutation: the mutations architecture.md defines
(`PATCH /findings/{id}`, `POST /re-review`, settings writes) are actions
attributed to a user, and there are no users yet.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/overview` | KPI tiles, one round trip |
| `GET /api/v1/analytics/findings-by-severity` | Validated findings by severity |
| `GET /api/v1/analytics/findings-by-category` | Validated findings by category |
| `GET /api/v1/analytics/rejection-reasons` | Rejected claims, by validation gate |
| `GET /api/v1/analytics/anchor-distribution` | Claimed vs resolved line, in buckets |
| `GET /api/v1/analytics/trends` | Gap-filled time series |
| `GET /api/v1/analytics/processing-health` | Run reliability and error breakdown |
| `GET /api/v1/analytics/coverage` | Files analysed / not analysed, with reasons |
| `GET /api/v1/analytics/recent-activity` | The latest attempts |
| `GET /api/v1/repositories` · `/{id}` | Repository list and detail |
| `GET /api/v1/pull-requests` · `/{id}` | PR list and detail with every attempt |
| `GET /api/v1/review-runs` · `/{id}` | Run list and the review deep-dive |
| `GET /api/v1/findings` · `/{id}` | Findings explorer and finding detail |
| `GET /api/v1/integration/status` | GitHub installations |
| `GET /api/v1/settings` | Non-secret configuration |

### Pagination

Keyset, per architecture.md §17.2: `?limit=&cursor=` → `{items, next_cursor}`.

`OFFSET 10000` makes PostgreSQL walk and discard ten thousand rows, so page 200
costs two hundred times page 1 — and a row inserted while the user pages shifts
every subsequent page, silently skipping an item. A cursor carries the sort key
of the last row seen, so the next query is a range scan the index serves.

The cursor is **a position, not a capability**, and is deliberately unsigned:
every value it carries is re-validated on decode and then bound as a parameter.
A forged cursor can only ask for a different position in a result set the
caller could already read. Signing it would imply a security property it does
not have.

Page size is clamped server-side (default 25, ceiling 100). A client asking for
10,000 gets 100 — clamping rather than rejecting, because the request is
reasonable and the limit is not discoverable.

`total` is opt-in (`include_total=true`). An exact `COUNT(*)` over a filtered
findings table is its own scan, and paying for it on every scroll is how a list
screen becomes the slowest thing in a product.

### Filtering

Allow-lists only; no generic query DSL. An unknown value is a **422 naming the
field**, never silently ignored — dropping `severity=criticl` returns every
severity while the caller believes they filtered.

Free-text search is bound to an `ILIKE` predicate with `%` and `_` escaped, so
a user typing `%` matches a percent sign rather than triggering a full scan.

## Indexes added (migration `0004`)

Four, each tied to one query that exists. Phase 3's indexes are all
repository-first, which serves the filtered views; what was missing is the
**unfiltered, time-ordered list** — the default state of every explorer screen.

| Index | Serves | Verified |
|---|---|---|
| `ix_findings_created_at_id` | Findings explorer, default order | `EXPLAIN` → Index Only Scan |
| `ix_review_runs_created_at_id` | Run list and recent activity | `EXPLAIN` → Index Only Scan |
| `ix_pull_requests_updated_at_id` | PR list across all repositories | `EXPLAIN` → Index Only Scan |
| `ix_findings_rejected_reason` *(partial)* | Rejection breakdown and `validation=rejected` | `EXPLAIN` → Bitmap Index Scan |

The fourth is partial (`WHERE rejection_reason IS NOT NULL`): validated rows
are the majority and neither query ever returns them, so they are not worth
indexing — the same argument as the `status = 'open'` partial index in `0003`.

Seven other dashboard needs were checked and found already served by existing
indexes; none was added speculatively.

## Frontend

Extends the Phase 0 system rather than replacing it: the same CSS custom
properties, the same Tailwind binding, the same `apiFetch`, the same router.
Added tokens for run/publication state and chart series; no second CSS
framework, no component library, no duplicate API client.

**Pages** — `/`, `/repositories`, `/repositories/:id`, `/pull-requests`,
`/pull-requests/:id`, `/review-runs`, `/review-runs/:id`, `/findings`,
`/findings/:id`, `/settings`, `/system`. Paths follow architecture.md §18.3;
`/dashboard` and `/reviews/:id` redirect so links built either way still work.

**Server state** is a small `useApiQuery` hook returning a discriminated union.
TanStack Query was the Phase 0 plan and cannot be installed (npm returns 403);
the hook covers cancellation, an explicit error state, refetch, and
`keepPreviousData` so a filter change does not blank a table. Swapping it for
TanStack Query later touches this file and the call sites' `data` access, and
no page's structure.

**Charts** are inline SVG and flexbox, not a charting library, for the same
reason. Every chart sits inside `ChartFrame`, which supplies `role="img"` with
a summary built from the real data *and* a keyboard-reachable `<details>` table
of every plotted value.

**Filters live in the URL**, so a view can be bookmarked and shared. Changing a
filter resets the cursor — a cursor is a position inside a specific ordered
result set and does not survive the set changing.

**Per-section async states.** The dashboard home calls six aggregates; one slow
or failing query degrades its own card and nothing else.

**Accessibility.** Semantic tables with captions, `aria-pressed` on filter
chips, visible focus rings, skip link, and — throughout — status conveyed by
glyph *and* label *and* colour, never colour alone.

**Responsive.** Dense tables scroll horizontally with low-priority columns
hidden below `md`, rather than being crushed. Navigation collapses to a
disclosure button on narrow screens.

## Wording rules, enforced by tests

The hardest requirement in this phase is not technical. These rules are
asserted in `frontend/src/utils/__tests__/wording.test.ts` so a later edit
cannot quietly break them:

- "Rejected claim", never "false positive".
- "Validated" carries "not that the issue has been independently proven".
- `partial_success` is "Completed, partial", never "Passed".
- A pull request with no review of its current commit reads "Not reviewed",
  explicitly *not* a pass.
- Superseded runs are described as overtaken, never as a result.
- Anchor buckets describe *resolution*; no label contains "accuracy" or
  "error rate".

See [`dashboard-metrics.md`](dashboard-metrics.md) for every metric's
definition, source, calculation and limitation.
