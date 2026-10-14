# Final security review

**Date:** 2026-09-18 · **Version:** 0.6.1 · **Reviewer:** Phase 6 verification pass

*Findings H-1 through I-4 were found in the Phase 6 pass at v0.6.0. The v0.6.1
release-cleanup pass added no new security findings; what it changed is recorded
in [`final-release-cleanup-report.md`](final-release-cleanup-report.md).*

This review covers the system as built. It is a review of code that has never
run against GitHub or an AI provider, because no credentials for either have
ever existed in this project's environments. That limits what can be claimed
and is stated wherever it matters.

## 1. Threat model

### What the system holds that is worth taking

| Asset | Where it lives | Consequence of loss |
|---|---|---|
| GitHub App private key | environment variable, in memory | full impersonation of the App on every installation |
| GitHub App installation tokens | in-memory cache, 1 h lifetime | write access to the installation's repositories until expiry |
| Webhook shared secret | environment variable | attacker can forge deliveries and drive arbitrary reviews |
| OAuth client secret | environment variable | attacker can complete sign-in flows as the application |
| Session signing secret | environment variable | session forgery for any user |
| User session tokens | server-side table (SHA-256 of the token), cookie on the client | account takeover |
| Users' source code | never stored; bounded excerpts pass through memory and go to the AI provider | disclosure of private code to a third party |
| Findings and review history | PostgreSQL | disclosure of what is wrong with someone's private code |

### Who might attack it

1. **A pull-request author on a public repository.** The strongest position, and
   the one most often underrated: they control the diff, and the diff is what the
   model reads. Anything the model can be induced to emit is attacker-controlled
   input to everything downstream.
2. **An unauthenticated internet client.** Can reach the webhook endpoint, the
   OAuth callback, the health endpoints and the dashboard API.
3. **An authenticated user of another tenant.** Wants another installation's
   findings.
4. **A compromised or malfunctioning AI provider.** Returns whatever it likes.
5. **Someone with the logs.** Wants the credentials or the source code in them.

### What is deliberately not defended against

- A compromised host or a leaked environment file. If the private key is read
  from the process's environment, the process is already lost.
- A malicious GitHub App administrator on the installing organisation's side.
- Denial of service beyond per-key rate limits and body-size ceilings.

## 2. The structural decisions that do most of the work

These are properties of the architecture, not of any one check, which is why
they are listed before the findings.

**The application never executes user code.** Not by design intent — by
verified absence. `final_audit.py` asserts zero occurrences of `eval`, `exec`,
`compile`, `__import__` and every shell-spawning call across the whole
application. Python is parsed with `ast.parse`, which builds a tree and runs
nothing; other languages go through tree-sitter, likewise a parser.

**No repository is ever cloned, and no whole file is ever sent to a model.**
Only the touched declarations and a bounded window around changed lines outside
any declaration. The blast radius of a prompt-injected model is bounded by the
byte budget, and that budget is now genuinely enforced (finding **M-1**).

**Model output cannot become a GitHub comment without passing 14 gates.** The
publisher's type signature accepts only `ValidatedFinding`; the module does not
import `RawFinding`, and a test asserts that. A claim must name a file the model
was shown, at the commit under review, on a line that is in the diff *and* was
presented, quoting source that is actually there — and the rejection reason is
recorded per gate, so "which gate caught it" is answerable.

**Authorization is a SQL predicate, not a filter.** `ViewerScope` joins through
`user_installation_access` inside every dashboard query. A cross-tenant id
returns 404, not 403, because 403 confirms the row exists.

**Sessions are opaque server-side tokens, not JWTs.** Logout genuinely
invalidates. Only the SHA-256 is stored, so a database read does not yield a
usable token.

## 3. Findings

Nine findings, all fixed in Phase 6 unless the row says otherwise.

### Critical

None.

### High

**H-1 · Model prose could forge a published comment's identity · FIXED**

Every review comment carries a hidden `<!-- codeguard-finding:<fingerprint> -->`
marker, and republication reads it back from GitHub to decide what has already
been said. The model's own description, impact, suggested fix and title were
interpolated into the same body, and `extract_marker` returned the **first**
match while ours is appended last. A model emitting a marker-shaped string
therefore stole a comment's identity.

*Impact.* The forged fingerprint is recorded as present on the pull request, so
the genuine finding that owns it is treated as already posted and is **never
published again** — model output silently deciding which real findings a
reviewer sees. Meanwhile the comment's own fingerprint goes unrecorded, so it is
re-posted on every subsequent run. Reconciliation after an ambiguous write reads
the same markers, so the recorded outcome is wrong too.

*Reachability.* On a public repository the diff is attacker-controlled and the
diff is what the model reads. This required prompt injection to succeed at
making the model emit an exact string — not trivial, but the entire premise of
the validation layer is that model output is untrusted.

*Fix.* Two independent layers in `app/review/publisher.py`: model-authored text
has `<!--` and `-->` HTML-escaped before interpolation (rendering as literal
text, so the attempt stays visible to a reader rather than being deleted), and
`extract_marker` now reads the **last** marker, which is always ours. The second
layer also repairs reconciliation for comments posted before the first shipped.
Covered by 4 parametrized tests plus a structural regression test.

### Medium

**M-1 · The context byte ceiling was not a ceiling · FIXED**

`AI_MAX_CONTEXT_BYTES` bounded the header and the file blocks. The manifest —
two headings plus one line for every changed file, **included and excluded** —
was never counted. The excluded half grows as the inverse of the budget: the
tighter the ceiling, the more files it excludes and the longer the list naming
them.

*Impact.* Measured against the Phase 5 tree: a 3000-file pull request at the
default 180,000-byte budget produced a **221,017-byte** document — 1.2× the
ceiling, and an estimated 63,148 tokens against a 60,000-token limit. The
provider would have rejected the request after charging for it, which is the
exact outcome the setting exists to prevent. At tighter budgets the overshoot
was worse (1.6× at 20,000 bytes with 200 files). Not a disclosure issue — the
extra bytes are file *paths*, which the model was already told about — but a
cost and availability one.

*Fix.* The manifest is reserved up front, before any file is admitted, and the
rendered exclusion list is capped at 50 entries with the remainder summarised.
The structured `excluded` record stays complete, so nothing is lost from the
dashboard. Verified at five scales from 40 to 3000 files.

**M-2 · A superseded review run could be resurrected as a success · FIXED**

`finish` updated `WHERE id = :id` with no status guard. In the §14 interleaving
— push A starts a run, push B supersedes it, worker 1 finishes anyway — the run
moved back out of `superseded` and was recorded as succeeded.

*Impact.* Two succeeded runs for one pull request at two different commits, a
duration measured across the supersession, and a success rate counting work
whose output was discarded. Publication was never at risk, because the publisher
re-reads the head commit from GitHub before posting. This is an integrity defect
in the audit record, not a wrong-comment defect.

*Fix.* `AND status IN ('queued', 'running')` plus `RETURNING status`, and the
service now reports the status the database settled on rather than the one it
asked for. The same guard makes `finish` idempotent against a redelivered
completion. Confirmed against a real PostgreSQL before and after, including a
20-round concurrent supersede/finish race.

**M-3 · Open-redirect check rejected control characters only by accident · FIXED**

`_safe_redirect_path` rejected `\r\n` because the injected payload happened to
contain a colon, which a separate check caught. A payload with a newline and no
colon passed, and the value is interpolated into a `Location` header — response
splitting.

*Fix.* Every control character is rejected as a class: `ch < " " or ch == "\x7f"`.
Verified against 7 payloads; the legitimate `/findings` path still works.

**M-4 · Path-traversal check understood only one separator · FIXED**

`is_safe_repository_path` rejected a leading `\` and a `C:\` prefix — so it
clearly intended to understand Windows separators — but split on `/` only.
`..\..\win` passed.

*Impact in practice: none.* Gate 4 requires the path to be one actually shown to
the model, which makes traversal unreachable regardless. Fixed anyway, because
the value of a defence-in-depth check is that it does not depend on the other
check being correct, and a rule applied in two places out of three reads as a
guarantee and is not one.

**M-5 · Throttled requests were answered 500, not 429 · FIXED**

`RateLimitMiddleware` raised `RateLimitedError` expecting the RFC 9457 handler
to render it. Starlette's per-exception handlers live in `ExceptionMiddleware`,
which is *inside* the user middleware stack, so the exception sailed past it into
`ServerErrorMiddleware`.

*Impact.* Every rate-limited client received `500 internal-error` with no
`Retry-After`, which tells a client to report a bug rather than to slow down —
and buries genuine server faults in throttling noise. Found only by running the
stack over real HTTP; no amount of source reading had caught it across two
phases.

*Fix.* The middleware returns a `JSONResponse` with the correct problem+json body
and `Retry-After`. The middleware order was also corrected so that 429 and 413
responses carry `X-Request-ID` — `main.py`'s docstring had asserted this was
already true while the registration order made it false.

### Low

**L-1 · `installations.installed_by_user_id` existed in the database, not in the model · FIXED**

Migration 0005 created the column; the model was never updated. `Base.metadata`
did not describe the deployed schema, so the next `alembic revision
--autogenerate` would have proposed **dropping** it — silently destroying the
attribution of who installed each App. Found by extending the schema-parity audit
to compare both directions; the Phase 5 audit checked one.

**L-2 · Health status literals were widened to `str` · FIXED**

A ternary over two string literals infers `str`, so the `Literal` vocabularies
the response models declare were enforced only by Pydantic at runtime. A typo
would have surfaced as a 500 in production rather than a type error in CI.

### Informational

**I-1 · Three configured limits were read by nothing · FIXED**

`AI_MAX_REQUESTS_PER_REVIEW` — documented to operators in `docs/deployment.md`
as a free-tier guard — `AI_MAX_RETRIES` and `WORKER_BATCH_SIZE` were declared,
documented and never read. The first is now enforced by `ProviderCallBudget`; a
call past the ceiling raises, and a call that *fails* still spends budget,
because a provider that fails repeatedly is exactly the case a free-tier ceiling
is for. The other two were removed: the honest fix for a knob that turns nothing
is to take it off the panel. A standing test now fails if any configured limit
becomes unread.

**I-2 · 26 NOT NULL columns have no server default · ACCEPTED, NOT CHANGED**

Investigated during the migration-cycle work. For `head_sha`, `title`,
`html_url` and `author_login` the absence of a default *is* the guard: it catches
a forgotten field at insert time rather than storing a plausible-looking empty
string. Adding defaults would weaken that. Recorded so the decision is visible
rather than rediscovered.

**I-3 · `_observed_head_sha` fails open · ACCEPTED, DOCUMENTED**

If the pre-publication read of the head commit fails, the publisher proceeds:
`if observed is not None and observed != head_sha`. A transient GitHub failure
therefore does not block every review. The exposure is the window in which a
push landed *and* the head read failed, and the three earlier staleness checks
(analysis, context, validator gate 5) still apply. Accepted rather than changed,
because failing closed here converts a GitHub blip into a total review outage,
and the alternative was not obviously safer. Named here so it is a decision.

**I-4 · The finding-status workflow is unimplemented · INTENTIONAL, SEE §5**

## 4. What was tested, and how hard

- **113 adversarial tests** against the AI → GitHub path, each asserting the
  *specific* rejection reason, because a claim rejected for the wrong reason
  means the intended gate is untested. Path traversal (17 payloads), commit
  identity, line identity, anchor integrity, credential leakage from **every**
  model-controlled prose field (8 credential shapes × 5 fields), unicode and
  control payloads, marker forgery, and a barrage that must publish nothing —
  paired with a truthful claim that must survive, so the suite cannot be
  satisfied by a validator that refuses everything.
- **41 tests over real HTTP** for the middleware stack.
- **170 checks against a real PostgreSQL**, including genuinely concurrent
  backends for every race in the queue and the review-run lifecycle.
- **27 audit checks** for execution primitives, secret handling, injection,
  traversal, SSRF, redirects, configuration parity, schema parity in both
  directions, and analytics integrity.

## 5. Accepted limitations

**The finding-status workflow does not exist.** `findings.status` has four
values, `finding_events` is a complete audit trail with an actor and a
user/system source, and the API accepts all four as a filter — and nothing
writes any of it. Every finding is created `open` and stays there.

Phase 6 deliberately did not build the mutation endpoints. Triage is a product
workflow — who may dismiss a finding, what a dismissal means on re-review,
whether it is scoped per repository or per fingerprint — and none of that is
decided anywhere in this project's documentation. Adding the endpoint because
the schema anticipates it would be building a feature to make a table look used.

The cost, stated plainly: **any statistic derived from finding status is
structurally zero**, and this project must not present a false-positive rate. A
standing test fails if a writer appears without this note being updated, and a
separate audit check fails if any user-visible string claims an accuracy,
precision, recall or false-positive figure.

**No accuracy claim of any kind is made.** There is no labelled benchmark
dataset and no methodology for one. What the system can honestly report is what
its own gates did: how many claims the model emitted, how many survived
validation, how many were published, and which gate rejected the rest. That is a
statement about this pipeline, not about the model's correctness, and the
dashboard is worded accordingly.

## 6. Verdict

The security-relevant code paths that could be executed in this environment
were executed, and nine defects were found and eight fixed (one accepted with
reasoning). The two that mattered most — marker forgery and the unenforced byte
ceiling — were both in the AI → GitHub path, and both were found by attacking it
rather than by reading it.

**No part of this system has been verified against GitHub or an AI provider.**
Signature verification, App authentication, token minting, comment publication
and model behaviour are all **BLOCKED — credential unavailable**. The
container has never been built: **BLOCKED — Docker unavailable**.

This review does not certify the system as production-ready, and nothing in it
should be quoted as doing so. See `docs/phase-6-final-report.md` §20 for the
release classification.
