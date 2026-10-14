# Phase 3 Completion Report

**CodeGuard AI — Gemini Code Review, Strict Finding Validation, GitHub Inline Review Comments**

Date: 2026-09-16 · Baseline: `architecture.md` (30 sections + appendices A–F)

---

## 1. Objective and scope

Phase 3 closes the gap between "we know exactly which lines changed" (Phase 2)
and "a developer sees a verified comment on the right line of the right
commit".

The governing rule, stated in the phase brief and treated as the design
constraint from which everything else follows:

> **The Gemini model is never trusted.**

Its output is a set of **claims** about a repository it cannot read, produced
under the influence of text an attacker may control. Every claim is checked
against an artefact the model did not produce — our own diff parse, and the
real file at the real commit — before anything reaches a pull request.

---

## 2. What was built

| Area | Module | Lines |
|---|---|---|
| Versioned prompt + trust boundary + response schema | `app/ai/prompt.py` | 231 |
| Gemini provider (real integration) | `app/ai/providers/gemini.py` | 431 |
| Budgeted deterministic context builder | `app/review/context.py` | 564 |
| **`FindingValidator` — 14 gates** | `app/review/validator.py` | 685 |
| GitHub review publisher | `app/review/publisher.py` | 642 |
| Orchestrator | `app/review/service.py` | 427 |
| Persistence (runs, findings, comments) | `app/db/repositories/reviews.py` | 595 |
| ORM models | `app/db/models/{review_run,finding,review_comment,finding_event}.py` | 4 files |
| Migration `0003` | `alembic/versions/…0003_phase3_review_entities.py` | 393 |
| UUIDv7 | `app/core/ids.py` | 58 |
| Job handler | `app/jobs/handlers/review_pull_request.py` | 179 |

Plus: `GitHubClient.post_json` / `create_pull_request_review` /
`list_review_comments`, `AmbiguousWriteError`, `GitHubValidationError`,
`FindingSide`, and fourteen new `Settings` fields.

---

## 3. The pipeline

```
PullRequestAnalysis (Phase 2)
  ↓ context        bounded, deterministic, real head-file line numbers
  ↓ prompt         instructions and repo content in separate channels
  ↓ Gemini         one call, structured output, no tools
  ↓ RawFinding     CLAIMS — never publishable
  ↓ validate       14 gates against the diff and the real file
  ↓ ValidatedFinding   the only type the publisher accepts
  ↓ persist        findings AND rejections, COMMITTED
  ↓ publish        one COMMENT review, line + side
  ↓ record         comments, funnel, terminal status
```

---

## 4. The fourteen validation gates

| # | Gate | Rejects | Reason code |
|---|---|---|---|
| 1 | structure | empty ids, confidence outside 0–1, absurd spans | `malformed` |
| 2 | secret leakage | a finding whose own text contains a credential | `secret_in_text` |
| 3 | path safety | absolute paths, `..`, URLs, NUL bytes, `C:\` | `unsafe_path` |
| 4 | file | a path we did not show the model | `unknown_file` |
| 5 | SHA | an analysis or context from another commit | `sha_mismatch` |
| 6 | side | `LEFT` — deletions are never commented on | `wrong_side` |
| 7 | line in file | a line the file does not contain | `line_out_of_range` |
| 8 | LineMap | a line not present in this PR's diff | `line_not_in_diff` |
| 9 | presentation | a line we never actually showed the model | `line_not_presented` |
| 10 | source | a line whose content cannot be read back | `no_source_line` |
| 11 | anchor | a snippet not at (or near) the cited line | `anchor_not_found` / `anchor_ambiguous` / `anchor_too_short` |
| 12 | declaration | *enrichment* — records the enclosing function | — |
| 13 | confidence | below the severity-aware threshold | `low_confidence` |
| 14 | duplicate | a claim already accepted this run | `duplicate` |

Gate 11 does the real work: a model that hallucinates a vulnerability must
*also* hallucinate a verbatim line of source that exists at the line it names.
Gate 9 closes the remaining hole — a model citing a line it was never shown is
guessing, and a lucky guess is still a guess.

---

## 5. Anchor relocation (ADR-0011)

A model that quotes source correctly but miscounts by one or two lines is
miscounting, not hallucinating. Gate 11 searches ±3 lines and accepts a
**unique** exact match, recording `anchor_relocated` and disclosing it in the
comment body. Two matches in the window is ambiguity, and ambiguity is
rejected — a comment on the wrong one of two identical lines *looks correct*,
which makes the error invisible.

`claimed_line_start`, `resolved_line` and `anchor_offset` are all persisted, so
"how often is the model exactly right about location?" is answerable. A strict
rule would have destroyed that dataset by dropping every non-zero offset.

---

## 6. Prompt-injection defence

| Layer | Mechanism | Strength |
|---|---|---|
| 1 | Channel separation — instructions in `system_instruction`, source in the user turn | reduces likelihood |
| 2 | Nonce fence, with the closing tag neutralised inside the content | reduces likelihood |
| 3 | Schema-forced output (`responseSchema`) — injected prose cannot *become* output | strong |
| 4 | **Post-hoc validation** against artefacts the attacker does not control | **decisive** |

Layers 1–3 reduce the chance of a bad response; only layer 4 makes a bad
response harmless. The worst achievable outcome of a successful injection is
suppressed or noisy findings — a degradation, never a compromise.

---

## 7. Publication hazards and how each is handled

**1. The head can move mid-review.** The head SHA is re-read from GitHub
immediately before publication; a known mismatch aborts entirely. It does not
publish anyway and does not re-anchor.

**2. Review creation is atomic.** One unpositionable comment rejects the whole
review with 422 and GitHub does not say which. Salvage: retry with the
high-confidence non-relocated subset, then a summary-only review carrying every
finding as text.

**3. A POST can succeed invisibly.** `AmbiguousWriteError` is deliberately
**not retryable**; recovery is reconciliation against GitHub's own comment list.

**4. Republication must not duplicate.** Every comment carries
`<!-- codeguard-finding:<fingerprint> -->`, and reconciliation matches on it.

---

## 8. Persistence ordering

`persist_findings` commits **before** the publisher touches GitHub. If
publication then fails, the run degrades to `partial_success` and every finding
stays visible in the dashboard. Persist-then-publish is the ordering that makes
partial failure survivable.

Rejected claims are persisted too, as `dashboard_only` with their
`rejection_reason` (ADR-0012) — the rejections are the measurement.

---

## 9. Schema (migration `0003`)

Four new tables (`review_runs`, `findings`, `review_comments`,
`finding_events`) plus seven columns on `changed_files`.

Four constraints carry the architectural weight:

- `uq_review_runs_pull_request_id_head_sha_attempt` — duplicate analysis of one
  commit is structurally impossible.
- `ck_findings_inline_requires_verified_anchor` — an inline finding must have a
  resolved line **and** a verified anchor.
- `uq_findings_review_run_id_fingerprint` — persistence is idempotent under retry.
- `uq_review_comments_finding_id` — one finding, at most one comment.

---

## 10. TEST RESULTS — what actually ran

**This section is the honest accounting the phase brief demanded.** The
previous phase's report conflated "test functions defined" with "test cases
executed"; those are different units and are separated here.

### 10.1 Test functions defined

| Suite | Functions | Executed? |
|---|---:|---|
| `tests/unit/` | 135 | ✅ yes |
| `tests/analysis/` | 114 | ✅ yes |
| `tests/review/` (new in Phase 3) | 122 | ✅ yes |
| `tests/api/` | 28 | ❌ **no** — needs FastAPI/Starlette TestClient |
| `tests/integration/` | 44 | ❌ **no** — needs SQLAlchemy + asyncpg |
| **Total defined** | **443** | |

### 10.2 Test cases actually executed

371 test functions expand to **481 parametrised cases**.

```
481 passed, 0 failed
```

| Phase 3 suite | Functions | Result |
|---|---:|---|
| `test_gemini_provider.py` | 18 | ✅ all pass (24 cases) |
| `test_context_builder.py` | 14 | ✅ all pass |
| `test_validator.py` | 39 | ✅ all pass (49 cases) |
| `test_publisher.py` | 24 | ✅ all pass |
| `test_review_pipeline_e2e.py` | 10 | ✅ all pass |
| `test_phase3_invariants.py` | 17 | ✅ all pass (20 cases) |

### 10.3 Executed only through shims

**All 481 cases above ran under a minimal test harness, not under pytest.**
PyPI returns **403 Forbidden** in this environment (an organisation egress
policy, not a transient failure), so `pytest`, `sqlalchemy`, `fastapi`,
`alembic`, `structlog`, `asyncpg`, `ruff`, `mypy` and `tree-sitter` **cannot be
installed**.

The harness is a ~70-line runner plus two shims:

- `structlog` — a stub logger. Affects only log emission, not logic under test.
- `pytest` — `raises`, `fixture`, `mark.parametrize`, `MonkeyPatch`.

Everything else — `httpx` 0.28.1 with `MockTransport`, `pydantic` 2.13.3,
`pydantic-settings`, `PyJWT`, `cryptography` — is the **real library**. No
production module was shimmed, mocked or stubbed.

### 10.4 Static checks

| Check | Result |
|---|---|
| `ast.parse` over every `app/`, `tests/`, `alembic/` file | ✅ 0 syntax errors |
| Unused-import scan over the 14 new modules | ✅ 0 |
| Secret-scanner simulation (`BEGIN … PRIVATE KEY` outside `*.md`) | ✅ 0 matches |
| `ruff` / `mypy` | ❌ **not run** — cannot be installed |

### 10.5 Database checks — REAL PostgreSQL 16.13

A live PostgreSQL 16.13 server was started and the schema applied. **The DDL
was not hand-written**: a 235-line shim implements just enough of
`sqlalchemy` and `alembic.op` to execute the real migration modules and emit
the SQL they describe, so the migration source is the input.

| Check | Result |
|---|---|
| All three migrations apply to an empty database | ✅ |
| Full downgrade reverses to **0 tables** | ✅ |
| 16 constraint-behaviour probes (Phase 3 tables) | ✅ 16/16 |
| 20 repository-SQL probes, statements extracted **verbatim** from `reviews.py` via AST | ✅ 20/20 |

Notable proven behaviours: `ck_findings_inline_requires_verified_anchor`
refuses an inline finding with no verified anchor; `ON CONFLICT DO NOTHING`
makes persistence idempotent; supersession marks the older commit's run and
leaves the current one running; `duration_ms` is computed server-side;
`previously_reported` returns only fingerprints that actually became comments;
deleting a run cascades to findings and comments.

**Not verified this way:** the Python glue in the repository classes,
SQLAlchemy parameter binding, and transaction boundaries under concurrency.
Those need the real driver.

### 10.6 Live GitHub checks

**None.** No GitHub App, no installation, no token, no network call to
`api.github.com`. Every GitHub interaction in the suite is served by a fake
client or `httpx.MockTransport`.

### 10.7 Live Gemini checks

**None.** No `GEMINI_API_KEY` exists in this environment and no request was
made to `generativelanguage.googleapis.com`. Every provider test drives
`httpx.MockTransport`.

---

## 11. What is therefore NOT claimed

Stated explicitly, because the difference matters:

1. **It is not claimed that Gemini works against the real API.** The provider
   handles the documented request and response shapes correctly *as tested
   against a mock*. Whether the live service returns those shapes is untested.
2. **It is not claimed that GitHub accepts these payloads.** Review creation,
   422 salvage and reconciliation are proven against a fake client only.
3. **It is not claimed that semantic vulnerability detection is "proven".**
   Structural validation passing means a finding is *anchored where it says it
   is* — nothing about whether the finding is *true*. That claim would need a
   labelled benchmark and a real API key.
4. **It is not claimed that `pytest` passes.** pytest is not installed. 481
   cases ran under a substitute harness.
5. **It is not claimed that `ruff` or `mypy` pass.** Neither could be installed.

---

## 12. Bugs found and fixed during Phase 3

**In Phase 0–1 code (found by the Phase 3 audit):**

1. **`GitHubClient` could not POST at all.** Every method was a GET and
   `_request` passed no body. Publishing a review was impossible. Fixed, and
   non-idempotent methods now raise `AmbiguousWriteError` rather than being
   blindly retried.
2. **`RawFinding` had no `side` field.** A LEFT-side finding would have been
   silently treated as RIGHT and posted on unrelated code. Fixed via
   `FindingSide` + gate 6 (ADR-0010).
3. **Four architecture tables were missing** (`review_runs`, `findings`,
   `review_comments`, `finding_events`). Added in migration `0003`.

**In Phase 3 code (found by its own tests):**

4. **`_to_gemini_schema` stripped `maxItems`.** Verified against Google's
   documentation that `maxItems` *is* supported; stripping it meant the
   caller's `max_findings` never reached the model, so we would have paid for
   output we then discarded. Fixed; `minItems` and `maxLength` remain stripped
   because they are genuinely unsupported.
5. **Rejection reasons were misattributed.** `_resolve_line` reported
   `anchor_not_found` when the real failure was `line_not_presented`, because
   the window search short-circuited the diagnosis. Since rejection reasons are
   the phase's main measurement, this silently corrupted the dataset.
   Restructured to diagnose the *claimed* line after the search fails.
6. **The context builder sent whole classes.** A class is "touched" whenever
   any method inside it changes, and its span is the entire body — so one
   changed method dragged in every sibling. Fixed: container declarations
   contribute a two-line header; functions and methods are still shown in full.

**In the verification harness itself** (not shipped code): a reused loop
variable clobbered the create parameters, and inlined UUID arrays needed an
explicit cast. Both fixed; noted here because they produced six spurious
failures that could have been mistaken for product bugs.

---

## 13. Changed behaviour from earlier phases

| Change | Consequence |
|---|---|
| `route_event` returns `review_pull_request` instead of `sync_pull_request` | a PR event now queues the full review; the handler syncs first, so it is a superset |
| Dedupe key now includes the job type | a sync job and a review job for one commit cannot cancel each other |
| Gemini provider `is_implemented` is now `True` | three Phase 0 tests updated |
| `app/ai/providers` may own an HTTP client | the raw-httpx invariant widened, and a new test asserts the AI package can never reach GitHub |

Four Phase 0/1 test files were updated to match. **No test was weakened to make
CI pass** — each change replaced an assertion that had become factually wrong
with one that is factually right, and two of them (the httpx invariant, the
provider-implemented flag) gained *additional* assertions.

---

## 14. Determinism

Asserted, not hoped for:

- the same analysis produces a **byte-identical** context document;
- file emission order is path order, never selection order;
- validation output is identical regardless of the model's array order;
- publisher ranking is total (severity, −confidence, path, line, fingerprint);
- `GEMINI_TEMPERATURE` defaults to `0.0`;
- fingerprints derive from validated properties, never from the model's
  per-response `finding_id`.

---

## 15. Budgets

`AI_MAX_REQUESTS_PER_REVIEW=1` · `AI_MAX_FILES_PER_REVIEW=40` ·
`AI_MAX_CHANGED_LINES_PER_REVIEW=3000` · `AI_MAX_CONTEXT_BYTES=180000` ·
`AI_MAX_ESTIMATED_INPUT_TOKENS=60000` · `AI_MAX_FINDINGS=25` ·
`REVIEW_MAX_INLINE_COMMENTS=20` · `REVIEW_MIN_CONFIDENCE=0.6` ·
`REVIEW_MIN_CONFIDENCE_HIGH_SEVERITY=0.5` · `REVIEW_PUBLISH_ENABLED=true`.

Files over budget are **excluded and named**, never truncated mid-file, and the
review body tells the developer which files were not reviewed.

---

## 16. The mandated adversarial fixtures

**Malicious-model fixture** (`test_a_fully_malicious_response_produces_zero_publishable_findings`)
— ten hostile claims including `file_path = "../../secret"`, `line = 999999`, a
fabricated anchor, `/etc/passwd`, a URL-as-path, a Windows absolute path, a
LEFT-side claim, a quoted GitHub token, and a one-character anchor.

> **Result: 0 validated, 10 rejected, 0 GitHub comments.**

**Stale-PR race** (`test_a_push_during_the_review_aborts_publication_entirely`)
— findings validated at commit A, head is commit B at publication time.

> **Result: `skipped_stale_head`, zero requests issued.**

This guard also fired for real during development: the end-to-end suite failed
until the fake GitHub was made to agree with the fixture about the head commit.

---

## 17. New ADRs

- **ADR-0010** — the model may claim `side: LEFT`; the validator rejects it.
- **ADR-0011** — anchors relocate within ±3 lines, but only unambiguously.
- **ADR-0012** — rejected claims are persisted, not discarded.

---

## 18. Security posture

Never logged or stored: Gemini/Claude keys, GitHub private keys, installation
tokens, webhook secrets, complete payloads, complete repository source,
complete prompts. The API key travels in the `x-goog-api-key` **header**, never
a URL. The model is given no tools. Nothing is cloned; nothing is executed.
Gate 2 rejects a finding whose own prose contains a credential shape *before*
persistence, so a leaked secret never reaches the database or a comment.

Seventeen structural invariants are asserted in
`tests/review/test_phase3_invariants.py`, including that `ValidatedFinding` is
constructed nowhere outside the validator and that the AI package can never
import the GitHub client.

---

## 19. Honest limitations

- No live Gemini or GitHub verification (see §10.6–10.7).
- `tests/api/` and `tests/integration/` — 72 functions — never executed here.
- Repository-class Python glue untested; only its SQL was executed.
- No concurrency test of two workers racing on the same pull request at the
  ORM level (Phase 1 proved `SKIP LOCKED` at the SQL level).
- Anchor-accuracy statistics cannot be quoted: they need real model output.

---

## 20. What Phase 4 inherits

A populated `review_runs` / `findings` / `review_comments` schema with the
funnel counters and the claimed-vs-resolved line dataset already recorded — so
the dashboard's analytics are live SQL aggregates over real columns rather than
numbers that have to be invented later.

---

## 21. Reproducing the verification

```bash
# offline suites (481 cases)
python3 scratchpad/run_tests_minimal.py tests/unit/*.py tests/analysis/*.py tests/review/*.py

# render DDL from the migrations and apply to a real PostgreSQL
python3 scratchpad/ddl_render.py backend/alembic/versions upgrade | psql -d codeguard

# repository SQL, extracted verbatim from source, against that server
python3 scratchpad/verify_phase3_db.py
```

With network access restored, the real commands are `pytest`, `ruff check`,
`mypy app`, `alembic upgrade head`.

---

## 22. Summary

| | |
|---|---|
| Test functions defined | 443 |
| Test functions executed | 371 |
| Test **cases** executed | **481** |
| Failures | **0** |
| Never executed | 72 (28 API + 44 integration) |
| Real-PostgreSQL checks | 36 (16 constraint + 20 repository-SQL) |
| Live GitHub calls | **0** |
| Live Gemini calls | **0** |
| Bugs found and fixed | 6 (3 pre-existing, 3 new) |
| New ADRs | 3 |
