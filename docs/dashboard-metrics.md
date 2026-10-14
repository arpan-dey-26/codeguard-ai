# Dashboard metric definitions

Every number the dashboard shows is defined here, with its source table, its
calculation, the population it describes, and what it does **not** mean.

This file exists because the metrics in this product are unusually easy to
misread. "Findings" could reasonably mean four different quantities, and the
difference between "we verified where this points" and "we verified this is a
bug" is the difference between an honest tool and an overclaiming one.

The rule that governs the whole file:

> A **validated finding** is structurally anchored to the reviewed source.
> A **rejected claim** failed a structural gate.
> Neither is a statement about whether the issue described is real.

No metric here is derived from a benchmark, because no benchmark exists. The
dashboard therefore publishes no accuracy, precision or false-positive rate.

---

## The model-output funnel

These five stages describe one review run. They are separate quantities and
the dashboard never substitutes one for another.

| Metric | Definition | Source | Calculation | Population | Known limitation |
|---|---|---|---|---|---|
| **Claims emitted** | Items the model put in its findings array. | `review_runs.findings_emitted` | `SUM` over runs in range | Runs created in the window | Counts what the provider received, not what the model "thought". Runs created before the Phase 4 funnel fix under-report this — they counted only parseable items. |
| **Claims dropped** | Items the provider could not coerce into a claim at all (wrong enum, missing required field, not an object). | `review_runs.findings_dropped` | `SUM` over runs in range | Same | No row exists for these, by definition: they are counted, not stored. |
| **Validated findings** | Claims that passed all fourteen structural gates. | `findings` | `COUNT(*) WHERE rejection_reason IS NULL` | Findings created in the window | **Not** independently proven defects. Means: the file was one we showed the model, the line is in this pull request's diff, and the quoted source is genuinely at that line in the reviewed commit. |
| **Rejected claims** | Claims that failed a structural gate. | `findings` | `COUNT(*) WHERE rejection_reason IS NOT NULL` | Same | **Not** confirmed false positives. The gate describes a problem with the claim's *location or shape*. Whether the issue is real is a semantic question this system does not answer. |
| **Published comments** | Review comments GitHub confirmed. | `review_comments` | `COUNT(*)` joined to runs in range | Comments for runs in the window | Read from stored publication state only. Never inferred from which lines the LineMap considers commentable — that is our opinion, not GitHub's. |

**The identity that must hold:**

```
claims_emitted = claims_dropped + validated_findings + rejected_claims
```

and

```
findings_inline + findings_summary ≤ validated_findings
```

The shortfall in the second is findings suppressed because an earlier run
already commented on them — neither posted again nor demoted.

---

## Counting reviews

The single most common way a review dashboard misleads is by conflating a
pull request with an attempt to review one.

| Metric | Definition | Source | Calculation | Population | Known limitation |
|---|---|---|---|---|---|
| **Pull requests reviewed** | Distinct pull requests with at least one *completed* review. | `review_runs` | `COUNT(DISTINCT pull_request_id) WHERE status IN ('succeeded','partial_success')` | Runs created in the window | Three attempts at one pull request count **once**. A pull request reviewed in an earlier window and retried in this one counts here. |
| **Review attempts** | Every review run started, of any outcome. | `review_runs` | `COUNT(*)` | Same | Deliberately a different number from the row above. Includes retries and attempts a newer commit overtook. |
| **Superseded** | Attempts overtaken by a newer commit before finishing. | `review_runs` | `COUNT(*) WHERE status = 'superseded'` | Same | Excluded from the completion rate: being pushed over is not a reliability problem. |
| **Completion rate** | Clean completions ÷ terminal runs. | `review_runs` | `succeeded / COUNT(status IN ('succeeded','partial_success','failed','skipped'))` | Same | Terminal runs only. A queued, running or superseded attempt is not evidence about reliability, and including it would make the rate drop whenever somebody pushes twice in a minute. |

---

## Review outcomes

| Status | What it means | How the UI shows it |
|---|---|---|
| `succeeded` | Ran, and every validated finding reached GitHub. | **Completed** |
| `partial_success` | Ran and stored its findings; not all reached GitHub. | **Completed, partial** — never "Passed" |
| `failed` | Did not complete. Findings already stored are still listed. | **Failed** |
| `superseded` | A newer commit replaced this one mid-review. | **Superseded** — history, never a current result |
| `skipped` | Nothing needed review. | **Skipped** |
| *(no run)* | The pull request's current commit has not been reviewed. | **Not reviewed** — explicitly *not* a pass |

**"The current review"** of a pull request is the newest non-superseded run
for the pull request's **current head commit**. It is anchored to `head_sha`
rather than to time, so a run for an older commit can never present itself as
the current result — even if it happened to finish later.

---

## Anchor metrics

| Metric | Definition | Source | Calculation |
|---|---|---|---|
| **Claimed line** | The line the model said the issue was on. | `findings.claimed_line_start` | Stored verbatim |
| **Resolved line** | The line the validator determined by matching the quoted source. | `findings.resolved_line` | `NULL` when nothing could be resolved |
| **Anchor offset** | `resolved_line − claimed_line_start`. | `findings.anchor_offset` | Bucketed as exact / ±1 / ±2 / ±3+ / unresolved |

**What this is not.** The offset measures the relationship between a claimed
location and the location this system's relocation policy settled on — under
*that policy* (ADR-0011: a unique exact source match within ±3 lines). It is a
property of the validator as much as of the model.

It is therefore **never** presented as:

- "AI accuracy"
- "AI error rate"
- a percentage of any kind

Publishing an accuracy figure would require a labelled benchmark dataset and a
stated methodology. Neither exists, so neither is claimed.

---

## Coverage

| Metric | Definition | Source |
|---|---|---|
| **Files changed** | Files in the pull request's diff at the reviewed commit. | `review_runs.files_changed` |
| **Files analysed** | Files the pipeline parsed and could anchor findings inside. | `review_runs.files_analysed` |
| **Files not analysed** | The remainder, with a recorded reason. | `review_runs.files_skipped`, `changed_files.skip_reason` |
| **Limited coverage** | Runs where a budget stopped the context build before every file fit. | `review_runs.context_truncated` |

**Deliberately not a percentage.** A file can be absent from a review because
it is binary, because no parser is installed for its language, because it was
deleted, or because a review budget was reached. Those are not comparable, and
`analysed ÷ changed` would present them as if they were — hiding the only part
worth knowing, which is *why*.

---

## Time and ranges

- All timestamps are stored and transmitted as **UTC** with an explicit
  offset. The browser renders local time and always keeps the canonical value
  in the element's `dateTime` and `title`, so a relative time like "2 hours
  ago" is a convenience and never the only thing the page knows.
- Ranges are **half-open**: `start ≤ t < end`. Two adjacent ranges never both
  contain the boundary instant.
- The default range ends at the **start of tomorrow UTC**, so the current day
  is included in full and a chart's last bucket is not a partial day whose
  value drops on every reload.
- Time-series ranges are **snapped to bucket boundaries** before querying, so
  every column covers the same span and the series length is a function of the
  range alone.
- Series are **gap-filled**: a day with no reviews is a zero, not a missing
  point. A line drawn across omitted days shows a trend that did not happen.
- Superseded runs are excluded from the plotted stack and reported in the
  chart's summary, so a burst of pushes does not look like a busy review day.

---

## What the dashboard deliberately does not show

| Not shown | Why |
|---|---|
| AI accuracy, precision, recall | No benchmark dataset and no methodology exist. |
| A false-positive rate | Would require semantic evaluation of each finding. Structural rejection is not semantic falsity. |
| A single coverage percentage | The exclusion reasons are not comparable. |
| A quality score | The column exists in the schema but nothing computes it yet; showing a null or a placeholder would be worse than omitting it. |
| Any guarantee that a LineMap-commentable line is GitHub-commentable | Publication state is read from `review_comments` rows, which is evidence. Our LineMap is an opinion. |
