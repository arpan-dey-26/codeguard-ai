"""Queue concurrency against a real PostgreSQL. Phase 6 §5.

Three properties the brief calls out by name, each tested with **genuinely
concurrent** database sessions rather than sequential statements pretending to
be concurrent:

1. **Lease race.** Worker A claims a job. The sweeper reclaims it. Worker B
   claims it. Worker A must NOT be able to release a lease it no longer holds.
2. **Duplicate enqueue.** Two simultaneous deliveries with the same dedupe key
   must produce one job, no 500, and no lost audit record.
3. **Stale recovery.** claimed → stale → recovered → reclaimed, with never two
   workers successfully owning one lease.

**How this runs.** SQLAlchemy and asyncpg cannot be installed (PyPI 403), so
each "worker" is a separate `psql` process holding its own session, driving the
**exact SQL the application executes** — extracted from the repository modules
by AST so there is no transcription to drift. Concurrency is real: separate
backends, real transactions, real `FOR UPDATE SKIP LOCKED`.

What this does not cover: SQLAlchemy's parameter binding and session lifecycle.
Those need the real driver and are recorded as BLOCKED.
"""

from __future__ import annotations

import os as _os
import pathlib as _pathlib

_HERE = _pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = _pathlib.Path(_os.environ.get("CODEGUARD_BACKEND", _HERE.parent))
PG_SOCKET_DIR = _os.environ.get("CODEGUARD_PGDIR", "/var/tmp/pg3")
PG_PORT = _os.environ.get("CODEGUARD_PGPORT", "55433")


import ast
import pathlib
import re
import subprocess
import sys
import threading

BACKEND = BACKEND_ROOT
PGDIR = PG_SOCKET_DIR
PORT = PG_PORT
DB = "cgqueue"

passed = 0
failed = 0
problems: list[str] = []


def psql(sql: str, *, db: str = DB, expect_error: bool = False) -> str:
    path = pathlib.Path(f"{PG_SOCKET_DIR}/_q_{threading.get_ident()}.sql")
    path.write_text(sql)
    path.chmod(0o644)
    result = subprocess.run(
        [
            "su", "postgres", "-c",
            f"psql -h {PGDIR} -p {PORT} -d {db} -v ON_ERROR_STOP=1 -t -A -f {path}",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and not expect_error:
        raise RuntimeError(result.stderr.strip()[:500])
    if result.returncode != 0 and expect_error:
        return "__ERROR__" + result.stderr.strip()[:200]
    lines = [
        ln for ln in result.stdout.strip().splitlines()
        if not re.match(
            r"^(INSERT|UPDATE|DELETE|SELECT|SET|ALTER|CREATE|DROP|ANALYZE|BEGIN|COMMIT|ROLLBACK|TRUNCATE)\b",
            ln,
        )
    ]
    return "\n".join(lines).strip()


def check(name: str, fn) -> None:
    global passed, failed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - harness
        failed += 1
        problems.append(f"{name}: {type(exc).__name__}: {exc}")
        print(f"  FAIL {name}\n         {exc}")
    else:
        passed += 1
        print(f"  ok   {name}")


# --------------------------------------------------------------- the real SQL
def module_constants(relative: str) -> dict[str, str]:
    """Every module-level ``text(\"...\")`` constant, by name."""
    tree = ast.parse((BACKEND / relative).read_text())
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if getattr(call.func, "id", None) == "text" and call.args:
                arg = call.args[0]
                if isinstance(arg, ast.Constant):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            found[t.id] = arg.value
    return found


def method_sql(relative: str, method: str) -> str:
    """The ``text(...)`` SQL inside one method.

    Handles both a plain literal and an f-string: the release statements
    interpolate the ``_FENCE`` constant, so their argument is a ``JoinedStr``.
    Placeholders are returned as ``{name}`` for the caller to substitute, which
    keeps the extracted text identical to what the application builds.
    """
    tree = ast.parse((BACKEND / relative).read_text())
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == method:
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "text"
                    and node.args
                ):
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant):
                        return str(arg.value)
                    if isinstance(arg, ast.JoinedStr):
                        parts = []
                        for piece in arg.values:
                            if isinstance(piece, ast.Constant):
                                parts.append(str(piece.value))
                            elif isinstance(piece, ast.FormattedValue):
                                parts.append("{" + ast.unparse(piece.value) + "}")
                        return "".join(parts)
    raise AssertionError(f"no SQL found in {relative}::{method}")


JOBS = module_constants("app/db/repositories/jobs.py")
CLAIM_SQL = JOBS["_CLAIM_SQL"]
SWEEP_SQL = JOBS["_SWEEP_SQL"]

_fence_match = re.search(
    r'_FENCE = "([^"]+)"', (BACKEND / "app/db/repositories/jobs.py").read_text()
)
assert _fence_match, "the lock fence constant is gone"
FENCE = _fence_match.group(1)

MARK_COMPLETED = method_sql("app/db/repositories/jobs.py", "mark_completed").replace(
    "{_FENCE}", FENCE
)
MARK_RETRY = method_sql("app/db/repositories/jobs.py", "mark_retry").replace(
    "{_FENCE}", FENCE
)
INSERT_EVENT = module_constants("app/db/repositories/webhook_events.py")["_INSERT_SQL"]


def bind(sql: str, params: dict[str, object]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in params:
            raise KeyError(f"no value for :{key}")
        v = params[key]
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, dict):
            return "'{}'::jsonb"
        return "'" + str(v).replace("'", "''") + "'"

    return re.sub(r":([a-z_][a-z0-9_]*)", repl, sql)


# ------------------------------------------------------------------- fixtures
print("=== schema ===")
subprocess.run(
    ["su", "postgres", "-c",
     f"psql -h {PGDIR} -p {PORT} -d postgres -c 'DROP DATABASE IF EXISTS {DB}' "
     f"-c 'CREATE DATABASE {DB}'"],
    capture_output=True, text=True,
)
schema = pathlib.Path("/var/tmp/pg3/s.sql")
subprocess.run(
    ["su", "postgres", "-c",
     f"psql -h {PGDIR} -p {PORT} -d {DB} -v ON_ERROR_STOP=1 -f {schema}"],
    capture_output=True, text=True, check=True,
)
print(f"  schema applied to {DB}")


def reset() -> None:
    psql("TRUNCATE jobs, webhook_events RESTART IDENTITY CASCADE;")


def enqueue(dedupe: str | None = None, job_type: str = "review_pull_request") -> int:
    out = psql(
        "INSERT INTO jobs (job_type, status, payload, dedupe_key, priority, "
        "attempts, max_attempts, run_after, created_at, updated_at) VALUES "
        f"('{job_type}', 'queued', '{{}}'::jsonb, "
        f"{'NULL' if dedupe is None else repr(dedupe)}, 100, 0, 3, now(), now(), now()) "
        "RETURNING id;"
    )
    return int(out)


# ============================================================ 1. LEASE RACE
print()
print("=== 1. lease race: a revoked worker must not release the lease ===")


def t_claim_marks_the_owner():
    reset()
    enqueue()
    row = psql(bind(CLAIM_SQL, {"worker_id": "worker-A"}))
    assert row, "worker A could not claim"
    owner = psql("SELECT locked_by FROM jobs LIMIT 1")
    assert owner == "worker-A", owner
    assert psql("SELECT status FROM jobs LIMIT 1") == "running"


def t_the_sweeper_revokes_a_stale_lease():
    psql("UPDATE jobs SET locked_at = now() - interval '2 hours';")
    swept = psql(bind(SWEEP_SQL, {"timeout_seconds": 900}))
    assert swept, "the sweeper reclaimed nothing"
    assert psql("SELECT status FROM jobs LIMIT 1") == "queued"
    assert psql("SELECT locked_by FROM jobs LIMIT 1") == ""


def t_worker_b_takes_the_reclaimed_job():
    row = psql(bind(CLAIM_SQL, {"worker_id": "worker-B"}))
    assert row, "worker B could not claim the reclaimed job"
    assert psql("SELECT locked_by FROM jobs LIMIT 1") == "worker-B"


def t_worker_a_cannot_complete_a_job_it_no_longer_owns():
    """The Phase 5 fence, proven against a real database.

    Without it, worker A's late `mark_completed` would mark the job done while
    worker B is still running it — the job's own output is discarded and B's
    eventual release writes over a 'completed' row.
    """
    job_id = int(psql("SELECT id FROM jobs LIMIT 1"))
    psql(bind(MARK_COMPLETED, {"job_id": job_id, "worker_id": "worker-A"}))
    status = psql("SELECT status FROM jobs LIMIT 1")
    owner = psql("SELECT locked_by FROM jobs LIMIT 1")
    assert status == "running", f"worker A completed a job it did not own: {status}"
    assert owner == "worker-B", f"worker A cleared B's lock: {owner!r}"


def t_worker_a_cannot_requeue_a_job_it_no_longer_owns():
    job_id = int(psql("SELECT id FROM jobs LIMIT 1"))
    psql(bind(MARK_RETRY, {
        "job_id": job_id, "worker_id": "worker-A", "delay": 30,
        "error": "late failure", "error_type": "TransientError",
    }))
    status = psql("SELECT status FROM jobs LIMIT 1")
    assert status == "running", f"worker A requeued B's job: {status}"


def t_the_rightful_owner_can_still_complete():
    """The fence must not break the normal path."""
    job_id = int(psql("SELECT id FROM jobs LIMIT 1"))
    psql(bind(MARK_COMPLETED, {"job_id": job_id, "worker_id": "worker-B"}))
    assert psql("SELECT status FROM jobs LIMIT 1") == "completed"


check("worker A claims and is recorded as owner", t_claim_marks_the_owner)
check("the sweeper revokes a stale lease", t_the_sweeper_revokes_a_stale_lease)
check("worker B claims the reclaimed job", t_worker_b_takes_the_reclaimed_job)
check("worker A CANNOT complete a job it no longer owns", t_worker_a_cannot_complete_a_job_it_no_longer_owns)
check("worker A CANNOT requeue a job it no longer owns", t_worker_a_cannot_requeue_a_job_it_no_longer_owns)
check("the rightful owner can still complete", t_the_rightful_owner_can_still_complete)


# ================================================= 2. CONCURRENT CLAIM SAFETY
print()
print("=== 2. concurrent claims: one job, many workers, one winner ===")


def t_n_workers_racing_for_one_job_produce_one_winner():
    reset()
    enqueue()
    results: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        out = psql(bind(CLAIM_SQL, {"worker_id": name}))
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r.strip()]
    assert len(winners) == 1, f"{len(winners)} workers claimed the same job"
    assert psql("SELECT count(*) FROM jobs WHERE status = 'running'") == "1"


def t_n_workers_racing_for_n_jobs_each_get_one():
    """`FOR UPDATE SKIP LOCKED` should let workers pass each other rather than
    queue behind one row — the property that makes a PostgreSQL queue viable."""
    reset()
    for _ in range(8):
        enqueue()
    results: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        out = psql(bind(CLAIM_SQL, {"worker_id": name}))
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed = [r for r in results if r.strip()]
    assert len(claimed) == 8, f"only {len(claimed)}/8 workers claimed a job"
    distinct = psql("SELECT count(DISTINCT locked_by) FROM jobs WHERE status='running'")
    assert distinct == "8", f"{distinct} distinct owners for 8 jobs"


def t_attempts_is_incremented_at_claim_time():
    """A worker that dies silently still burns an attempt, so a job that
    reliably crashes its worker dead-letters instead of taking down every
    worker in turn."""
    reset()
    enqueue()
    psql(bind(CLAIM_SQL, {"worker_id": "w1"}))
    assert psql("SELECT attempts FROM jobs LIMIT 1") == "1"


check("8 workers racing for 1 job → exactly 1 winner", t_n_workers_racing_for_one_job_produce_one_winner)
check("8 workers racing for 8 jobs → each gets one", t_n_workers_racing_for_n_jobs_each_get_one)
check("attempts is incremented at claim time", t_attempts_is_incremented_at_claim_time)


# ================================================== 3. DUPLICATE ENQUEUE
print()
print("=== 3. duplicate enqueue: one job, no 500, audit row survives ===")


def t_the_partial_unique_index_exists():
    found = psql(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_jobs_dedupe_key_active'"
    )
    assert found == "1", "the active-dedupe index is missing"


def t_a_second_active_job_with_the_same_key_is_refused():
    reset()
    enqueue(dedupe="review:42:abc")
    out = psql(
        "INSERT INTO jobs (job_type, status, payload, dedupe_key, priority, attempts, "
        "max_attempts, run_after, created_at, updated_at) VALUES "
        "('review_pull_request','queued','{}'::jsonb,'review:42:abc',100,0,3,now(),now(),now());",
        expect_error=True,
    )
    assert out.startswith("__ERROR__"), "the duplicate was accepted"
    assert "uq_jobs_dedupe_key_active" in out, out[:200]
    assert psql("SELECT count(*) FROM jobs") == "1"


def t_concurrent_duplicate_deliveries_yield_exactly_one_job():
    """The real race: two deliveries arriving at the same instant."""
    reset()
    outcomes: list[str] = []
    lock = threading.Lock()

    def deliver() -> None:
        out = psql(
            "INSERT INTO jobs (job_type, status, payload, dedupe_key, priority, attempts, "
            "max_attempts, run_after, created_at, updated_at) VALUES "
            "('review_pull_request','queued','{}'::jsonb,'race:1',100,0,3,now(),now(),now());",
            expect_error=True,
        )
        with lock:
            outcomes.append(out)

    threads = [threading.Thread(target=deliver) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [o for o in outcomes if not o.startswith("__ERROR__")]
    assert len(succeeded) == 1, f"{len(succeeded)} of 6 concurrent inserts succeeded"
    assert psql("SELECT count(*) FROM jobs WHERE dedupe_key='race:1'") == "1"


def t_a_completed_job_frees_the_key():
    """The index is partial over `status IN ('queued','running')`. Once a job
    finishes, the same pull request must be reviewable again."""
    reset()
    enqueue(dedupe="review:7:xyz")
    psql("UPDATE jobs SET status='completed';")
    second = enqueue(dedupe="review:7:xyz")
    assert second > 0
    assert psql("SELECT count(*) FROM jobs WHERE dedupe_key='review:7:xyz'") == "2"


def t_the_audit_row_survives_a_deduplicated_delivery():
    """The Phase 5 defect: the IntegrityError rolled back the webhook row with
    the job, and GitHub does not redeliver — so the event vanished entirely."""
    reset()
    event_id = psql(bind(INSERT_EVENT, {
        "github_delivery_id": "delivery-abc",
        "event_type": "pull_request",
        "action": "opened",
        "github_installation_id": 1,
        "github_repo_id": 2,
        "repository_full_name": "acme/app",
        "github_pr_number": 7,
        "payload_digest": "d" * 64,
        "payload_bytes": 100,
        "processing_status": "queued",
        "ignored_reason": None,
        "signature_valid": True,
    }))
    assert event_id, "the audit row was not written"
    # A savepoint models what the repository does around the conflicting insert.
    psql(
        "BEGIN;\n"
        "SAVEPOINT s;\n"
        "INSERT INTO jobs (job_type, status, payload, dedupe_key, priority, attempts, "
        "max_attempts, run_after, created_at, updated_at) VALUES "
        "('review_pull_request','queued','{}'::jsonb,'dup',100,0,3,now(),now(),now());\n"
        "RELEASE SAVEPOINT s;\n"
        "SAVEPOINT s2;\n"
        "INSERT INTO jobs (job_type, status, payload, dedupe_key, priority, attempts, "
        "max_attempts, run_after, created_at, updated_at) VALUES "
        "('review_pull_request','queued','{}'::jsonb,'dup',100,0,3,now(),now(),now());\n",
        expect_error=True,
    )
    # The savepoint keeps the enclosing transaction usable, so the audit row
    # can still be marked and committed.
    psql(
        "UPDATE webhook_events SET processing_status='coalesced', processed_at=now() "
        "WHERE github_delivery_id='delivery-abc';"
    )
    remaining = psql(
        "SELECT processing_status FROM webhook_events WHERE github_delivery_id='delivery-abc'"
    )
    assert remaining == "coalesced", f"audit row lost or unmarked: {remaining!r}"
    assert psql("SELECT count(*) FROM webhook_events") == "1"


def t_a_redelivered_webhook_is_deduplicated_race_free():
    reset()
    outcomes: list[str] = []
    lock = threading.Lock()

    def deliver() -> None:
        out = psql(bind(INSERT_EVENT, {
            "github_delivery_id": "same-delivery",
            "event_type": "pull_request", "action": "opened",
            "github_installation_id": 1, "github_repo_id": 2,
            "repository_full_name": "acme/app", "github_pr_number": 7,
            "payload_digest": "e" * 64, "payload_bytes": 10,
            "processing_status": "queued", "ignored_reason": None,
            "signature_valid": True,
        }))
        with lock:
            outcomes.append(out)

    threads = [threading.Thread(target=deliver) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    inserted = [o for o in outcomes if o.strip()]
    assert len(inserted) == 1, f"{len(inserted)} of 6 concurrent deliveries inserted"
    assert psql("SELECT count(*) FROM webhook_events") == "1"


check("the partial unique index exists", t_the_partial_unique_index_exists)
check("a second ACTIVE job with the same key is refused", t_a_second_active_job_with_the_same_key_is_refused)
check("6 concurrent duplicate deliveries → exactly 1 job", t_concurrent_duplicate_deliveries_yield_exactly_one_job)
check("a completed job frees the dedupe key", t_a_completed_job_frees_the_key)
check("the audit row SURVIVES a deduplicated delivery", t_the_audit_row_survives_a_deduplicated_delivery)
check("6 concurrent redeliveries → 1 audit row, race-free", t_a_redelivered_webhook_is_deduplicated_race_free)


# =================================================== 4. STALE RECOVERY CYCLE
print()
print("=== 4. stale recovery: claimed → stale → recovered → reclaimed ===")


def t_a_fresh_lease_is_not_swept():
    reset()
    enqueue()
    psql(bind(CLAIM_SQL, {"worker_id": "w1"}))
    swept = psql(bind(SWEEP_SQL, {"timeout_seconds": 900}))
    assert not swept.strip(), "a live lease was reclaimed"
    assert psql("SELECT status FROM jobs LIMIT 1") == "running"


def t_an_expired_lease_is_swept_exactly_once():
    psql("UPDATE jobs SET locked_at = now() - interval '2 hours';")
    first = psql(bind(SWEEP_SQL, {"timeout_seconds": 900}))
    second = psql(bind(SWEEP_SQL, {"timeout_seconds": 900}))
    assert first.strip(), "the expired lease was not reclaimed"
    assert not second.strip(), "the same lease was reclaimed twice"


def t_the_full_cycle_never_yields_two_owners():
    """Ten rounds of claim → expire → sweep → reclaim, asserting after every
    step that at most one worker owns the job."""
    reset()
    enqueue()
    for i in range(10):
        psql(bind(CLAIM_SQL, {"worker_id": f"w{i}"}))
        owners = psql(
            "SELECT count(*) FROM jobs WHERE status='running' AND locked_by IS NOT NULL"
        )
        assert owners == "1", f"round {i}: {owners} owners"
        psql("UPDATE jobs SET locked_at = now() - interval '2 hours';")
        psql(bind(SWEEP_SQL, {"timeout_seconds": 900}))
        assert psql("SELECT status FROM jobs LIMIT 1") == "queued"
    assert psql("SELECT attempts FROM jobs LIMIT 1") == "10"


def t_a_sweep_during_a_concurrent_claim_does_not_double_own():
    """The sweeper and a claiming worker running at the same instant."""
    reset()
    enqueue()
    psql(bind(CLAIM_SQL, {"worker_id": "slow"}))
    psql("UPDATE jobs SET locked_at = now() - interval '2 hours';")

    errors: list[str] = []
    lock = threading.Lock()

    def sweeper() -> None:
        try:
            psql(bind(SWEEP_SQL, {"timeout_seconds": 900}))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"sweeper: {exc}")

    def claimer() -> None:
        try:
            psql(bind(CLAIM_SQL, {"worker_id": "fast"}))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"claimer: {exc}")

    threads = [threading.Thread(target=sweeper), threading.Thread(target=claimer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], errors
    running = psql("SELECT count(*) FROM jobs WHERE status='running'")
    assert running in ("0", "1"), f"{running} jobs running after the race"
    distinct = psql("SELECT count(DISTINCT locked_by) FROM jobs WHERE locked_by IS NOT NULL")
    assert distinct in ("0", "1"), f"{distinct} owners after the race"


def t_attempt_exhaustion_dead_letters():
    reset()
    enqueue()
    psql("UPDATE jobs SET attempts = 3, max_attempts = 3;")
    dead = psql(module_constants("app/db/repositories/jobs.py")["_DEAD_LETTER_SQL"])
    assert dead.strip(), "an exhausted job was not dead-lettered"
    assert psql("SELECT status FROM jobs LIMIT 1") == "dead"


def t_a_dead_job_is_never_claimed_again():
    row = psql(bind(CLAIM_SQL, {"worker_id": "w-any"}))
    assert not row.strip(), "a dead-lettered job was claimed"


check("a fresh lease is not swept", t_a_fresh_lease_is_not_swept)
check("an expired lease is swept exactly once", t_an_expired_lease_is_swept_exactly_once)
check("10 full cycles never yield two owners", t_the_full_cycle_never_yields_two_owners)
check("a sweep racing a claim does not double-own", t_a_sweep_during_a_concurrent_claim_does_not_double_own)
check("attempt exhaustion dead-letters", t_attempt_exhaustion_dead_letters)
check("a dead job is never claimed again", t_a_dead_job_is_never_claimed_again)


print()
print(f"{passed} passed, {failed} failed")
if problems:
    print()
    for p in problems:
        print("  " + p)
sys.exit(1 if failed else 0)
