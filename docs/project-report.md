# CodeGuard AI — project report

**AI-Powered GitHub Code Review and Code Quality Monitoring Platform**

Final-year B.Tech project · Computer Science and Engineering
Narula Institute of Technology, Kolkata (MAKAUT)

**Version 0.6.1 · September 2026**

---

## Abstract

CodeGuard AI is a GitHub App and web dashboard that reviews pull requests
automatically. When a pull request is opened or updated, the platform verifies
the webhook signature, extracts only the changed files and their diff, parses the
changed source with Tree-sitter and Python's native AST, sends a tightly bounded
context to a large language model, and then — the part that distinguishes this
project from a wrapper around an API — **validates every claim the model makes
against the real diff and the real file at the real commit before anything is
published.** Only claims that survive fourteen gates become inline review
comments. Everything else, including the reason it was rejected, is persisted for
analysis.

The engineering thesis is a narrow one: *a language model is a source of claims,
not of facts, and the value of a review tool is in the layer that refuses them.*

This report describes what was built, what was verified, and — with equal
weight — what was not. The system's final classification is
**deployable-shaped, not production-verified**, and §9 explains precisely what
that phrase means and why a weaker word was not chosen.

---

## 1. Problem

Code review is the highest-leverage quality practice in software engineering and
the first thing a team under deadline abandons. The reasons are structural: it is
synchronous, it competes with feature work, and its benefit is invisible when it
works.

Automated review tools exist, and most of them fail in one of two ways. Static
analysers have excellent precision on the narrow class of defects they encode and
say nothing about the rest. Tools built directly on language models have broad
coverage and no precision guarantee at all: the model will confidently cite a
line number that does not exist, quote source that is not in the file, and report
a bug in code the pull request did not touch. Posted to a real repository, those
comments destroy trust faster than the true ones build it.

**The problem this project addresses is not "can a model find bugs?" It is "given
that a model will sometimes lie, how do you build a system that cannot publish
the lie?"**

## 2. Objectives

1. Receive GitHub pull-request events reliably, exactly once, and survive
   redelivery, restart and concurrency.
2. Analyse only what changed — never clone a repository, never execute its code,
   never send a whole file to a third party.
3. Obtain findings from a language model behind an abstraction that no vendor's
   concepts leak through.
4. **Verify every finding against ground truth before publication**, and record
   every rejection with the specific reason.
5. Publish inline comments that land on the right line of the right commit, once.
6. Turn the results into a longitudinal quality record that states only what it
   can prove.

## 3. Architecture

A **modular monolith**: one codebase, one deployable image, two processes.

```
GitHub ──webhook──▶ API process ──▶ PostgreSQL (jobs table = the queue)
                                          │
                                          ▼
                                   Worker process
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              ▼                           ▼                           ▼
      diff + AST analysis        bounded context ──▶ AI provider   validation
                                                                        │
                                                            ┌───────────┴──────────┐
                                                            ▼                      ▼
                                                   GitHub inline comments    PostgreSQL
                                                   (validated only)          (all claims)
```

### Why a monolith

Microservices were rejected explicitly. At this scale they would add network
boundaries, distributed failure modes and deployment complexity in exchange for
independent scaling that nothing needs. The seams that would allow the split
later — the provider protocol, the repository layer, the job handler registry —
exist and are exercised. **Complexity must be earned by evidence** is the
governing principle of the design, and it is the reason there is no Redis, no
Celery, no Kubernetes and no message broker.

### Why PostgreSQL is the queue

A dedicated broker would be a second piece of infrastructure to operate, secure
and back up. PostgreSQL's `SELECT … FOR UPDATE SKIP LOCKED` provides exactly the
semantics needed, inside the transaction that already has to be correct. The
resulting queue is verified under genuinely concurrent clients: lease fencing so
a slow worker cannot release a job a sweeper has reassigned, partial unique
indexes for idempotent enqueue, a stale-job sweeper, and dead-lettering.

### The trust plane

This is the core contribution and is worth stating precisely.

The model returns `RawFinding` objects. The word *raw* is load-bearing: file
paths and line numbers in one are **claims**, and using them to post anything
would be a defect. A separate type, `ValidatedFinding`, is what the publisher
accepts — and the publisher's module does not import `RawFinding` at all, which
a test asserts. The gap between the two types is fourteen gates:

1. structure — the claim parses and its fields are in range
2. secret leakage — the model's own prose must not quote a credential
3. path safety — a plain relative in-repository path, both separators understood
4. file membership — the path must be one we actually showed the model
5. commit identity — three independent records of "which commit is this?" must agree
6. side — a LEFT-side claim is rejected, never silently coerced to RIGHT
7. line in file — the line must exist in the file at that commit
8–11. anchor — the model must quote source that is genuinely there, at that line
   or within ±3 lines, unambiguously; relocation is disclosed in the comment
12. declaration enrichment — the finding is attributed to the enclosing function or class
13. confidence — severity-aware threshold
14. duplicate — a stable fingerprint over validated properties, not the model's own id

Gate 8–11 is what makes the rest meaningful. Without an anchor check, every
other gate is defeated by a plausible-looking line number.

Rejections are **persisted with their specific reason**, not discarded. That is
what makes "is the model getting better or worse?" an answerable question, and it
is why the reason is a closed enumeration rather than free text.

## 4. Implementation

| Layer | Technology | Note |
|---|---|---|
| API | FastAPI + Pydantic v2 | async throughout |
| Worker | plain asyncio | no Celery |
| Database | PostgreSQL 16, SQLAlchemy 2 Core, Alembic | the queue lives here too |
| Diff analysis | hand-written unified-diff parser | GitHub's `position` is deprecated; `line` + `side` is used |
| AST | Tree-sitter, plus Python's native `ast` | both parse; neither executes |
| AI | Gemini behind an `AIReviewProvider` protocol | vendor-neutral by construction |
| Frontend | React 18, TypeScript strict, Vite, Tailwind | eleven pages, keyset pagination |
| Auth | GitHub OAuth, opaque server-side sessions | not JWTs, so logout truly invalidates |

Roughly 109 application modules and 50 test modules, across six phases.

### Design decisions worth defending in a viva

**Server-side sessions rather than JWTs.** A JWT cannot be revoked before it
expires, so "log out" becomes a client-side courtesy. The session token is 256
bits of opaque randomness; only its SHA-256 is stored, so a database read does
not yield a usable token.

**Authorization as a SQL predicate.** `ViewerScope` joins through
`user_installation_access` *inside* every dashboard query, rather than filtering
results afterwards. A cross-tenant id returns 404, not 403 — because 403 confirms
the row exists.

**Rejected findings are persisted (ADR-0012).** The obvious design discards
them. Keeping them is what turns the validator from a filter into an instrument.

**The model may claim LEFT (ADR-0010).** The alternative — not modelling the
deletion side at all — would mean silently coercing such a claim to RIGHT, which
publishes a comment about deleted code onto whatever now occupies that line.

**Anchor relocation is bounded at ±3 lines and disclosed (ADR-0011).** Models
miscount by one or two. A window wide enough to absorb that is useful; one wide
enough to find the quote anywhere in the file would make the anchor meaningless.
When a comment is relocated, it says so.

## 5. Verification

Verification was a full phase, not a step, and it is where this project's claim
to rigour actually rests.

| What | Result |
|---|---|
| Backend test suite | **871 passed** |
| PostgreSQL harnesses, real database | **170 passed** |
| Middleware, over real HTTP | 41 tests |
| Adversarial model output | 113 tests |
| Frontend pure logic, real `tsc` + real Node | 67 passed |
| Lint, format, backend types, frontend types | clean |

### The harnesses

SQLAlchemy, asyncpg and pytest-asyncio could not be installed in the release
environment (the package index returned 403). Rather than declare the database
layer unverifiable, purpose-built harnesses extract the **exact SQL the
application builds** — by AST from the repository modules, or by calling the real
query-builder functions — and execute it against a real PostgreSQL 16, with
genuinely concurrent `psql` backends where concurrency is the property under
test. They prove the statements; they do not prove SQLAlchemy's binding, and the
report says so in those words.

### Adversarial testing

The validator is tested by attacking it. Every case asserts the **specific**
rejection reason rather than merely that the claim was refused, because a claim
rejected for the wrong reason means the intended gate is untested — a defect of
exactly that shape shipped in an earlier phase. The suite includes seventeen path
traversal payloads, eight credential shapes across five model-controlled prose
fields, unicode and control-character payloads, and a barrage in which every
attack arrives at once. It is paired with a test that a *truthful* claim still
survives, so the suite cannot be satisfied by a validator that refuses
everything.

### What verification found

Nine defects, two of them in the path that decides what a model may say on
someone's pull request. The most serious: model prose could contain a
marker-shaped string that forged a published comment's identity, causing a
genuine finding to be treated as already posted and **silently suppressed on
every subsequent run** — model output deciding which real findings a reviewer
sees, in a system whose premise is that model output decides nothing.

Verification also found that the project's own CI had never executed: running
the configured lint step for the first time produced 210 violations, meaning
every commit in the project's history would have failed the build.

## 6. Results

The system does what its architecture says it does, for the parts that could be
executed:

- Webhooks are verified by HMAC-SHA256 over raw bytes with a constant-time
  comparison, and a replayed delivery produces one job and one audit row.
- The queue is correct under eight concurrent workers, lease revocation, stale
  recovery and duplicate enqueue.
- Migrations apply, reverse to zero tables, and reapply, with the schema
  identical afterwards.
- Every model claim that does not match ground truth is refused, with the
  specific gate recorded.
- Every dashboard number is a SQL aggregate over real rows, and each one was
  executed and checked against a specified fixture set.

## 7. Honest limitations

Stated at the same volume as the results, because the distinction is the point.

**Nothing in this system has ever spoken to GitHub or to an AI provider.** No
credentials existed in any development or verification environment. So: no
webhook has been received from GitHub, no installation token has been minted, no
model has been called, no comment has been posted. Everything about those
integrations is tested against fakes. *A fake proves the client handles a given
response correctly; it does not prove the service returns that response.*

**The container has never been built** — no Docker daemon was available.

**72 test functions have never run** — they cover the FastAPI request path and
database integration, precisely the seams the other tests cannot reach.

**The frontend has never rendered.** Its pure logic is tested; its components are
not.

**No accuracy claim is made, and none may be.** There is no labelled benchmark
dataset of real vulnerabilities in real pull requests, and without one there is
no valid methodology for a precision, recall or false-positive figure. An
automated check fails the build if any user-visible string claims one. What the
dashboard reports is what the pipeline's own gates did — emitted, validated,
published, and which gate rejected the rest. That is a statement about the
validation layer, not about whether the model is right.

**The finding-status triage workflow is deliberately absent.** The schema
anticipates it; no endpoint writes it. Consequently every statistic derived from
finding status is structurally zero, which is said plainly rather than hidden
behind an empty chart.

## 8. What this project demonstrates

Setting aside the deliverable, the transferable results are three.

**A type boundary is a better guarantee than a policy.** "Validate before
publishing" is a rule someone can forget. `publish(findings: Sequence[ValidatedFinding])`
in a module that cannot import `RawFinding` is a rule the compiler enforces.

**A check that is not executed is not a check.** Five phases of documentation
described quality gates that had never run. The single highest-value act of the
final phase was typing the command.

**Honest reporting is an engineering output.** The difference between
"integration verified" and "mock verified; live integration blocked — credential
unavailable" is the difference between a report that can be trusted and one that
cannot. Every table in this project's documentation distinguishes the two, and
the classification below is chosen on that basis rather than on how it reads.

## 9. Conclusion

**Classification: DEPLOYABLE-SHAPED / NOT PRODUCTION-VERIFIED.**

The architecture is coherent and the code its own tests can reach is verified,
including the database layer against a real PostgreSQL under real concurrency and
the security-relevant paths under deliberate attack. The system is not
production-verified because every external boundary — GitHub, the AI provider,
the container runtime — has never been crossed, for reasons of environment rather
than of design.

A softer phrase was available and was not used. *Mock verified is not integration
verified; a configured credential is not a working integration; deployable-shaped
is not production-proven.* `docs/release-checklist.md` section B is the list of
what closes the gap, in order.

## References and repository documentation

- `architecture.md` — the full architectural baseline, with Phase 6 amendments in §31
- `docs/final-verification-matrix.md` — what was executed, where, with what result
- `docs/final-security-review.md` — threat model and all nine findings
- `docs/release-checklist.md` — what is verified and what must be, before production
- `docs/phase-6-final-report.md` — the final engineering report
- `docs/decisions/` — twelve architecture decision records
- `backend/verification/` — every verification harness, re-runnable
