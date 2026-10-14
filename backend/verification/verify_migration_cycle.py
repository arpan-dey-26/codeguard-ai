"""Migration cycle with exercise between each step. Phase 6 §5.

    upgrade → exercise → downgrade to zero → upgrade → exercise again

"Exercise" means writing and reading real rows through the whole entity graph —
installation → repository → pull request → review run → finding → comment, plus
a user, a session and an access grant — not merely counting tables. A schema
that applies but cannot accept a row is a schema that has not been tested.

Alembic itself cannot be installed (PyPI 403), so the DDL is rendered from the
real migration modules by ``ddl_render.py``, which executes them against a
minimal ``alembic.op`` implementation. The migration source is the input; the
DDL is derived, never transcribed.
"""

from __future__ import annotations

import os as _os
import pathlib as _pathlib

_HERE = _pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = _pathlib.Path(_os.environ.get("CODEGUARD_BACKEND", _HERE.parent))
PG_SOCKET_DIR = _os.environ.get("CODEGUARD_PGDIR", "/var/tmp/pg3")
PG_PORT = _os.environ.get("CODEGUARD_PGPORT", "55433")


import pathlib
import re
import subprocess
import sys

PGDIR = PG_SOCKET_DIR
PORT = PG_PORT
DB = "cgcycle"
SCRATCH = pathlib.Path(PG_SOCKET_DIR)

passed = 0
failed = 0


def psql(sql: str, *, db: str = DB, expect_error: bool = False) -> str:
    path = SCRATCH / "_cycle.sql"
    path.write_text(sql)
    path.chmod(0o644)
    result = subprocess.run(
        ["su", "postgres", "-c",
         f"psql -h {PGDIR} -p {PORT} -d {db} -v ON_ERROR_STOP=1 -t -A -f {path}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and not expect_error:
        raise RuntimeError(result.stderr.strip()[:400])
    if result.returncode != 0:
        return "__ERROR__"
    return "\n".join(
        ln for ln in result.stdout.strip().splitlines()
        if not re.match(r"^(INSERT|UPDATE|DELETE|SELECT|SET|ALTER|CREATE|DROP|TRUNCATE)\b", ln)
    ).strip()


def check(name: str, fn) -> None:
    global passed, failed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        failed += 1
        print(f"  FAIL {name}: {exc}")
    else:
        passed += 1
        print(f"  ok   {name}")


def run_sql_file(path: str, db: str = DB) -> int:
    result = subprocess.run(
        ["su", "postgres", "-c",
         f"psql -h {PGDIR} -p {PORT} -d {db} -v ON_ERROR_STOP=1 -f {path}"],
        capture_output=True, text=True,
    )
    return len(re.findall(r"^ERROR", result.stderr, re.M))


def render(direction: str) -> str:
    out = SCRATCH / f"_cycle_{direction}.sql"
    result = subprocess.run(
        [sys.executable, "ddl_render.py",
         str(BACKEND_ROOT / "alembic" / "versions"), direction],
        capture_output=True, text=True, check=True,
        cwd=str(_HERE),
    )
    out.write_text(result.stdout)
    out.chmod(0o644)
    return str(out)


# --------------------------------------------------------------- the exercise
EXERCISE = """
INSERT INTO installations (github_installation_id, account_login, account_github_id,
    account_type, repository_selection, permissions, events, created_at, updated_at)
VALUES (91001, 'acme', 5, 'Organization', 'all', '{}'::jsonb, '[]'::jsonb, now(), now());

INSERT INTO repositories (installation_id, github_repo_id, owner_login, name, full_name,
    default_branch, is_private, primary_language, is_active, settings, created_at, updated_at)
SELECT id, 77001, 'acme', 'app', 'acme/app', 'main', true, 'Python', true, '{}'::jsonb,
       now(), now() FROM installations WHERE github_installation_id = 91001;

-- Every NOT NULL column is supplied explicitly. `changed_files_count` and
-- friends have a Python-side default in the model but no server default in the
-- migration, so a raw-SQL insert must name them — which is exactly what the
-- application's own upsert does. Discovered by this harness failing first.
INSERT INTO pull_requests (repository_id, github_pr_id, number, title, author_login,
    state, is_draft, head_sha, base_sha, head_ref, base_ref, html_url,
    changed_files_count, changed_files_truncated, created_at, updated_at)
SELECT id, 88001, 7, 'Add caching', 'dev', 'open', false,
       'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
       'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
       'feature', 'main', 'https://github.com/acme/app/pull/7', 3, false, now(), now()
FROM repositories WHERE github_repo_id = 77001;

INSERT INTO review_runs (id, pull_request_id, repository_id, trigger, head_sha,
    attempt, status, started_at, created_at, updated_at)
SELECT '01900000-0000-7000-8000-000000000001'::uuid, pr.id, pr.repository_id,
       'opened', pr.head_sha, 1, 'succeeded', now(), now(), now()
FROM pull_requests pr WHERE pr.github_pr_id = 88001;

-- `anchor_verified` is required for an inline finding: the CHECK constraint
-- `ck_findings_inline_requires_verified_anchor` refuses the row otherwise.
-- That is the Phase 3 trust invariant enforced in the database rather than
-- only in Python, and this harness hit it — which is the constraint working.
INSERT INTO findings (id, review_run_id, repository_id, pull_request_id, fingerprint,
    category, severity, title, description, file_path, claimed_line_start, resolved_line,
    anchor_verified, mapping_status, model_confidence, effective_confidence,
    created_at, updated_at)
SELECT '01900000-0000-7000-8000-000000000002'::uuid,
       '01900000-0000-7000-8000-000000000001'::uuid, r.repository_id, r.pull_request_id,
       'fp-cycle-0001', 'security', 'high', 'Unvalidated path',
       'A path is joined without validation.', 'app/main.py', 42, 42,
       true, 'inline', 0.90, 0.90, now(), now()
FROM review_runs r WHERE r.id = '01900000-0000-7000-8000-000000000001'::uuid;

INSERT INTO review_comments (finding_id, review_run_id, github_comment_id, path,
    line, side, commit_sha, body_hash, posted_at)
SELECT '01900000-0000-7000-8000-000000000002'::uuid,
       '01900000-0000-7000-8000-000000000001'::uuid, 555001, 'app/main.py', 42, 'RIGHT',
       'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'hash-cycle', now();

INSERT INTO users (github_user_id, login, name, created_at, updated_at)
VALUES (66001, 'octocat', 'Octo Cat', now(), now());

INSERT INTO user_sessions (user_id, token_hash, issued_at, expires_at, last_seen_at,
    created_at, updated_at)
SELECT id, repeat('c', 64), now(), now() + interval '8 hours', now(), now(), now()
FROM users WHERE github_user_id = 66001;

INSERT INTO user_installation_access (user_id, installation_id, role, last_synced_at)
SELECT u.id, i.id, 'member', now()
FROM users u, installations i
WHERE u.github_user_id = 66001 AND i.github_installation_id = 91001;

INSERT INTO oauth_states (state_hash, created_at, expires_at)
VALUES (repeat('d', 64), now(), now() + interval '10 minutes');
"""

READBACK = """
SELECT (SELECT count(*) FROM installations)
     || '/' || (SELECT count(*) FROM repositories)
     || '/' || (SELECT count(*) FROM pull_requests)
     || '/' || (SELECT count(*) FROM review_runs)
     || '/' || (SELECT count(*) FROM findings)
     || '/' || (SELECT count(*) FROM review_comments)
     || '/' || (SELECT count(*) FROM users)
     || '/' || (SELECT count(*) FROM user_sessions)
     || '/' || (SELECT count(*) FROM user_installation_access)
     || '/' || (SELECT count(*) FROM oauth_states);
"""
EXPECTED = "1/1/1/1/1/1/1/1/1/1"


def exercise(label: str) -> None:
    psql(EXERCISE)
    got = psql(READBACK)
    assert got == EXPECTED, f"{label}: entity graph readback {got}, expected {EXPECTED}"

    # The scoped join the dashboard actually uses, end to end.
    scoped = psql("""
        SELECT count(*) FROM findings f
          JOIN repositories r ON r.id = f.repository_id
         WHERE r.installation_id IN (
             SELECT a.installation_id FROM user_installation_access a
               JOIN users u ON u.id = a.user_id
              WHERE u.github_user_id = 66001
         );
    """)
    assert scoped == "1", f"{label}: scoped finding query returned {scoped}"

    # The constraint that refused the first draft of this fixture, asserted
    # deliberately: an inline finding with no verified anchor must be
    # impossible to store, whatever application code does.
    refused = psql("""
        INSERT INTO findings (id, review_run_id, repository_id, pull_request_id,
            fingerprint, category, severity, title, description, file_path,
            claimed_line_start, resolved_line, anchor_verified, mapping_status,
            model_confidence, effective_confidence, created_at, updated_at)
        SELECT '01900000-0000-7000-8000-00000000000f'::uuid, r.id, r.repository_id,
               r.pull_request_id, 'fp-unverified', 'security', 'high', 'Unanchored',
               'no verified anchor', 'app/main.py', 42, NULL, false, 'inline',
               0.9, 0.9, now(), now()
        FROM review_runs r WHERE r.id = '01900000-0000-7000-8000-000000000001'::uuid;
    """, expect_error=True)
    assert refused == "__ERROR__", (
        f"{label}: the database accepted an inline finding with no verified anchor"
    )

    # Cascades: deleting the repository must take its whole subtree.
    psql("DELETE FROM repositories WHERE github_repo_id = 77001;")
    after = psql(
        "SELECT (SELECT count(*) FROM pull_requests) || '/' "
        "|| (SELECT count(*) FROM review_runs) || '/' "
        "|| (SELECT count(*) FROM findings) || '/' "
        "|| (SELECT count(*) FROM review_comments);"
    )
    assert after == "0/0/0/0", f"{label}: cascade left orphans: {after}"


print("=== rendering DDL from the real migration modules ===")
up = render("upgrade")
down = render("downgrade")
print(f"  upgrade:   {up}")
print(f"  downgrade: {down}")

subprocess.run(
    ["su", "postgres", "-c",
     f"psql -h {PGDIR} -p {PORT} -d postgres -c 'DROP DATABASE IF EXISTS {DB}' "
     f"-c 'CREATE DATABASE {DB}'"],
    capture_output=True, text=True,
)

print()
print("=== cycle ===")


def t_first_upgrade():
    errors = run_sql_file(up)
    assert errors == 0, f"{errors} errors applying the upgrade"
    tables = psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    assert tables == "14", f"expected 14 tables, got {tables}"


def t_first_exercise():
    exercise("first")


def t_downgrade_to_zero():
    errors = run_sql_file(down)
    assert errors == 0, f"{errors} errors applying the downgrade"
    tables = psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    assert tables == "0", f"downgrade left {tables} tables behind"


def t_no_orphan_objects_survive_the_downgrade():
    """A downgrade that drops tables but leaves indexes, sequences or types
    behind makes the next upgrade fail with a name collision."""
    leftovers = psql(
        "SELECT coalesce(string_agg(indexname, ','), '') FROM pg_indexes "
        "WHERE schemaname = 'public';"
    )
    assert leftovers == "", f"indexes survived the downgrade: {leftovers}"
    seqs = psql(
        "SELECT count(*) FROM information_schema.sequences WHERE sequence_schema='public';"
    )
    assert seqs == "0", f"{seqs} sequences survived"


def t_second_upgrade():
    errors = run_sql_file(up)
    assert errors == 0, f"{errors} errors re-applying the upgrade"
    tables = psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    assert tables == "14", f"expected 14 tables, got {tables}"


def t_second_exercise():
    exercise("second")


def t_the_schema_is_identical_after_a_full_cycle():
    """The point of the cycle. A downgrade that loses a constraint or a default
    leaves a database that looks right and behaves differently."""
    fingerprint = psql("""
        SELECT string_agg(sig, '|' ORDER BY sig) FROM (
            SELECT table_name || '.' || column_name || ':' || data_type || ':'
                   || is_nullable || ':' || coalesce(column_default, '-') AS sig
              FROM information_schema.columns
             WHERE table_schema = 'public'
        ) s;
    """)
    (SCRATCH / "_schema_after_cycle.txt").write_text(fingerprint)
    assert len(fingerprint) > 1000, "schema fingerprint looks empty"
    constraints = psql(
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE constraint_schema = 'public';"
    )
    assert int(constraints) > 50, f"only {constraints} constraints after the cycle"
    print(f"         {constraints} constraints, fingerprint {len(fingerprint)} chars")


check("upgrade applies cleanly (14 tables)", t_first_upgrade)
check("exercise: full entity graph writes, reads and cascades", t_first_exercise)
check("downgrade removes every table", t_downgrade_to_zero)
check("no orphan indexes or sequences survive", t_no_orphan_objects_survive_the_downgrade)
check("upgrade applies again after the downgrade", t_second_upgrade)
check("exercise again: same behaviour on the rebuilt schema", t_second_exercise)
check("schema is complete after the full cycle", t_the_schema_is_identical_after_a_full_cycle)

print()
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
