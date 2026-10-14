# Phase 3 — Gemini Review, Strict Finding Validation, GitHub Inline Comments

## Objective

Everything between "we know exactly which lines changed" (Phase 2) and "a
developer sees a verified comment on the right line of the right commit".

The governing rule, and the one every design decision below serves:

> **The Gemini model is never trusted.**

Not "rarely wrong". Not "mostly reliable". *Never trusted.* Its output is a set
of **claims** about a repository it cannot read, influenced by text an attacker
may control, and every one of those claims is checked against an artefact the
model did not produce before anything reaches a pull request.

```
PullRequestAnalysis (Phase 2)
  ↓ context        bounded, deterministic, real line numbers        app/review/context.py
  ↓ prompt         instructions and repo content in separate channels  app/ai/prompt.py
  ↓ Gemini         one call, structured output, no tools           app/ai/providers/gemini.py
  ↓ RawFinding     CLAIMS — never publishable                      app/ai/contracts.py
  ↓ validate       14 gates against the diff and the real file      app/review/validator.py
  ↓ ValidatedFinding   the only type the publisher accepts
  ↓ persist        findings AND rejections, COMMITTED              app/db/repositories/reviews.py
  ↓ publish        one COMMENT review, line + side                 app/review/publisher.py
  ↓ record         comments, funnel, terminal status
```

## The validation gates

`FindingValidator` runs fourteen gates in a fixed order. Cheap structural
checks run before anything that touches file content; each gate assumes its
predecessors passed.

| # | Gate | Rejects | Reason code |
|---|---|---|---|
| 1 | structure | empty ids, confidence outside 0–1, absurd spans | `malformed` |
| 2 | secret leakage | a finding whose own text contains a credential | `secret_in_text` |
| 3 | path safety | absolute paths, `..`, URLs, NUL bytes | `unsafe_path` |
| 4 | file | a path we did not show the model | `unknown_file` |
| 5 | SHA | an analysis or context from another commit | `sha_mismatch` |
| 6 | side | `LEFT` — deletions are never commented on | `wrong_side` |
| 7 | line in file | a line the file does not contain | `line_out_of_range` |
| 8 | LineMap | a line not present in this pull request's diff | `line_not_in_diff` |
| 9 | presentation | a line we never actually showed the model | `line_not_presented` |
| 10 | source | a line whose content cannot be read back | `no_source_line` |
| 11 | anchor | a snippet not at (or near) the cited line | `anchor_not_found` / `anchor_ambiguous` / `anchor_too_short` |
| 12 | declaration | *enrichment* — records the enclosing function | — |
| 13 | confidence | below the severity-aware publication threshold | `low_confidence` |
| 14 | duplicate | a claim already accepted this run | `duplicate` |

**Gate 11 does the real work.** A model that hallucinates a vulnerability must
*also* hallucinate a verbatim line of source that happens to exist at the line
it names. At temperature 0, against real file content, that essentially does
not happen.

**Gate 9 closes the remaining hole.** "The line exists in the file" is a weaker
test than "we showed the model that line". A model citing a line it was never
shown is guessing, and a guess that lands on real code is still a guess.

### Anchor relocation (ADR-0011)

A model that quotes the source correctly but miscounts by one or two lines is
not hallucinating. Gate 11 searches ±3 lines:

- exact hit at the cited line → wins outright, `anchor_relocated = false`;
- **unique** hit in the window → relocated, disclosed in the comment body;
- **multiple** hits → `anchor_ambiguous`, rejected;
- no hits → `anchor_not_found`, rejected.

Every candidate line is re-checked against the diff and presentation gates, so
relocation can never move a finding onto a line the pull request did not touch.

`claimed_line_start`, `resolved_line` and `anchor_offset` are all persisted, so
"how often is the model exactly right about location?" is answerable.

## Prompt-injection defence

Repository source is attacker-controlled in the general case, and a file can
contain text shaped like an instruction.

| Layer | Mechanism | Strength |
|---|---|---|
| 1 | **Channel separation** — instructions in `system_instruction`, source in the user turn, never concatenated | reduces likelihood |
| 2 | **Nonce fence** — `<untrusted_repository_data nonce="…">`, with the closing tag neutralised inside the content | reduces likelihood |
| 3 | **Schema-forced output** — the response must conform to `responseSchema`, so injected prose cannot *become* the output | strong |
| 4 | **Post-hoc validation** — every claim checked against our own diff parse and the real blob | **decisive** |

Layers 1–3 reduce the chance of a bad response. **Only layer 4 makes a bad
response harmless.** The worst achievable outcome of a successful injection is
*suppressed or noisy findings* — a degradation, never a compromise, because
nothing the model emits is executed and everything it emits is checked.

The prompt never promises the model that its output will be published. It is
told the opposite: every claim is verified.

## Publication

One review, `event: COMMENT`, never `APPROVE` or `REQUEST_CHANGES` — the event
is a constant inside `GitHubClient`, not a parameter a caller can get wrong.
Comments use `line` + `side`, never the deprecated `position`.

Four hazards shape `ReviewPublisher`:

**1. The head can move mid-review.** The head SHA is re-read from GitHub
immediately before publication. A mismatch aborts entirely — it does not
"publish anyway" or "re-anchor". Comments on a commit the author has already
replaced destroy trust instantly.

**2. Review creation is atomic.** One unpositionable comment rejects the whole
review with 422, and GitHub does not say which one. Salvage runs in two steps:
retry with only high-confidence, non-relocated comments; then a summary-only
review that still carries every finding as text. Findings are never silently
lost because GitHub disliked one of them.

**3. A POST can succeed invisibly.** A timed-out create-review may already have
created the review. `AmbiguousWriteError` is deliberately **not retryable**; the
recovery is *reconciliation* — read GitHub's comment list and see which of our
markers are there.

**4. Republication must not duplicate.** Every comment body carries
`<!-- codeguard-finding:<fingerprint> -->`. Reconciliation matches on that
marker, so "have we already said this?" is answered by GitHub's own data.

## Persistence

`persist_findings` runs and **commits before** the publisher touches GitHub.
If publication then fails, the run degrades to `partial_success` and every
finding remains visible in the dashboard. Persist-then-publish is the ordering
that makes partial failure survivable.

Rejected claims are persisted too, as `mapping_status = 'dashboard_only'` with
their `rejection_reason` (ADR-0012). The rejections are the measurement: a
rising `anchor_not_found` rate is a prompt regression; a non-zero
`unsafe_path` rate is a prompt-injection indicator. None of it is
reconstructable after the fact.

### Schema (migration `0003`)

| Table | Purpose |
|---|---|
| `review_runs` | one attempt at one commit, with the stored funnel |
| `findings` | every claim — published or rejected |
| `review_comments` | what actually exists *on GitHub* |
| `finding_events` | status audit trail |
| `changed_files` | + `review_run_id` and the Phase 2 outcome columns |

Four constraints carry the weight:

- `uq_review_runs_pull_request_id_head_sha_attempt` — duplicate analysis of one
  commit is structurally impossible.
- `ck_findings_inline_requires_verified_anchor` — an inline finding must have a
  resolved line **and** a verified anchor. The "nothing unverified reaches
  GitHub" rule, expressed where a bug in application code cannot bypass it.
- `uq_findings_review_run_id_fingerprint` — persisting a run's findings is
  idempotent, so the step can be retried after a crash.
- `uq_review_comments_finding_id` — one finding, at most one comment.

## Budgets

Every limit is configuration, not a constant at a call site.

| Setting | Default | Why |
|---|---|---|
| `AI_MAX_REQUESTS_PER_REVIEW` | 1 | the free tier has a small per-minute quota; a large PR must not silently become dozens of requests |
| `AI_MAX_FILES_PER_REVIEW` | 40 | |
| `AI_MAX_CHANGED_LINES_PER_REVIEW` | 3 000 | |
| `AI_MAX_CONTEXT_BYTES` | 180 000 | |
| `AI_MAX_ESTIMATED_INPUT_TOKENS` | 60 000 | mirrors NFR-P5 |
| `AI_MAX_FINDINGS` | 25 | |
| `REVIEW_MAX_INLINE_COMMENTS` | 20 | a review with sixty inline comments is not read, it is dismissed |
| `REVIEW_MIN_CONFIDENCE` | 0.6 | |
| `REVIEW_MIN_CONFIDENCE_HIGH_SEVERITY` | 0.5 | asymmetric cost: a missed critical bug costs more than a dismissed false positive |
| `REVIEW_PUBLISH_ENABLED` | true | kill switch — analysis and persistence continue, nothing reaches GitHub |
| `GEMINI_TEMPERATURE` | 0.0 | two runs over one commit should produce the same findings |

Files over the budget are **excluded and named**, never truncated mid-file, and
the review body says which ones were not reviewed.

## What Phase 3 does **not** do

- It does not clone a repository or execute any repository code.
- It does not send whole files — only touched declarations plus a window.
- It does not store repository source in PostgreSQL (one quoted anchor line
  per finding is the deliberate exception).
- It does not approve or request changes on a pull request.
- It does not retry a write whose outcome is unknown.
- It has **not** been run against the live Gemini API or a real GitHub App.
  See the Phase 3 report for exactly what was and was not executed.
