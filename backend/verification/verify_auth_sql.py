"""Run the Phase 5 authentication SQL against a real PostgreSQL.

The statements in ``app/db/repositories/auth.py`` are the authorization
boundary, so "it probably parses" is not good enough. This harness extracts
each ``text(...)`` literal from the module's AST — the *actual* statement the
application executes — binds plausible parameters and runs it.

Limitation, stated rather than glossed: asyncpg and SQLAlchemy cannot be
installed here, so parameters are substituted as literals by this harness
rather than bound by SQLAlchemy. What is verified is that PostgreSQL accepts
the statement, resolves every table and column, and returns the expected rows.
What is NOT verified is SQLAlchemy's parameter binding, which is exercised only
in CI where the real stack is installed.
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

BACKEND = BACKEND_ROOT
DSN = ["-h", PG_SOCKET_DIR, "-p", PG_PORT, "-d", "cg5"]

passed = 0
failed = 0
problems: list[str] = []


_SCRATCH = pathlib.Path("/var/tmp/pg3/_q.sql")


def psql(sql: str, *, expect_error: bool = False) -> str:
    """Run SQL via a file.

    Not via ``-c``: shelling a multi-line statement through ``su -c`` mangles
    newlines, and the failure looks like a SQL error rather than a quoting one.
    """
    _SCRATCH.write_text(sql)
    _SCRATCH.chmod(0o644)
    result = subprocess.run(
        ["su", "postgres", "-c",
         "psql " + " ".join(DSN) + " -v ON_ERROR_STOP=1 -t -A -f " + str(_SCRATCH)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and not expect_error:
        raise RuntimeError(result.stderr.strip()[:400])
    # psql prints command tags (INSERT 0 1, UPDATE 3) on -f even with -t -A;
    # strip them so a caller reading a scalar gets the scalar.
    lines = [
        ln for ln in result.stdout.strip().splitlines()
        if not re.match(r"^(INSERT|UPDATE|DELETE|SELECT|SET|ALTER|CREATE|DROP|ANALYZE)\b", ln)
    ]
    return "\n".join(lines).strip()


def check(name: str, fn) -> None:
    global passed, failed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - this is the harness
        failed += 1
        problems.append(f"FAIL {name}: {type(exc).__name__}: {exc}")
        print(f"  FAIL {name}: {exc}")
    else:
        passed += 1
        print(f"  ok   {name}")


# ---------------------------------------------------------------- statements
def statements_in(relative: str) -> dict[str, str]:
    """Every ``text("...")`` literal in a module, keyed by enclosing function."""
    tree = ast.parse((BACKEND / relative).read_text())
    found: dict[str, str] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "text"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                found[fn.name] = node.args[0].value
    return found


AUTH_SQL = statements_in("app/db/repositories/auth.py")


def bind(sql: str, params: dict[str, object]) -> str:
    """Substitute named parameters as literals. Harness-only; see the docstring."""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"no value supplied for :{key}")
        value = params[key]
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, list):
            return "'{" + ",".join(str(v) for v in value) + "}'"
        return "'" + str(value).replace("'", "''") + "'"

    return re.sub(r":([a-z_][a-z0-9_]*)", replace, sql)


print("=== fixtures ===")
psql("DELETE FROM user_installation_access; DELETE FROM user_sessions; "
     "DELETE FROM oauth_states; DELETE FROM users; "
     "DELETE FROM repositories; DELETE FROM installations;")
psql("""
INSERT INTO installations (github_installation_id, account_login, account_github_id,
                           account_type, repository_selection, permissions, events,
                           created_at, updated_at)
VALUES (5001, 'acme', 900, 'Organization', 'all', '{}'::jsonb, '[]'::jsonb, now(), now()),
       (5002, 'other', 901, 'Organization', 'all', '{}'::jsonb, '[]'::jsonb, now(), now()),
       (5003, 'gone',  902, 'Organization', 'all', '{}'::jsonb, '[]'::jsonb, now(), now());
UPDATE installations SET uninstalled_at = now() WHERE github_installation_id = 5003;
""")
print("  fixtures loaded")

print()
print("=== upsert_user ===")


def _upsert(github_id: int, login: str, email: str | None = None) -> int:
    sql = bind(AUTH_SQL["upsert_user"], {
        "github_user_id": github_id, "login": login, "name": "Test User",
        "avatar_url": "https://example.invalid/a.png", "email": email,
    })
    return int(psql(sql))


USER_A = None
USER_B = None


def t_upsert_creates():
    global USER_A
    USER_A = _upsert(7001, "alice", "alice@example.invalid")
    assert USER_A > 0


def t_upsert_is_idempotent_on_github_id():
    again = _upsert(7001, "alice", None)
    assert again == USER_A, "a second login must not create a second user"


def t_a_rename_updates_rather_than_duplicating():
    renamed = _upsert(7001, "alice-renamed")
    assert renamed == USER_A
    assert psql("SELECT login FROM users WHERE id = %d" % USER_A) == "alice-renamed"


def t_an_absent_email_does_not_erase_a_stored_one():
    # COALESCE(EXCLUDED.email, users.email): a login where GitHub returns no
    # email must not blank the address we already have.
    assert psql("SELECT email FROM users WHERE id = %d" % USER_A) == "alice@example.invalid"


def t_a_recycled_handle_is_a_different_user():
    global USER_B
    USER_B = _upsert(7002, "alice-renamed")
    assert USER_B != USER_A, "keying on login would have merged two people"


check("upsert_user creates a user", t_upsert_creates)
check("upsert_user is idempotent on github_user_id", t_upsert_is_idempotent_on_github_id)
check("a renamed login updates the same row", t_a_rename_updates_rather_than_duplicating)
check("a null email does not erase a stored one", t_an_absent_email_does_not_erase_a_stored_one)
check("a recycled handle is a different user", t_a_recycled_handle_is_a_different_user)

print()
print("=== replace_installation_access ===")


def _grant(user_id: int, github_ids: list[int]) -> list[int]:
    sql = bind(AUTH_SQL["replace_installation_access"],
               {"user_id": user_id, "gids": github_ids})
    out = psql(sql)
    return sorted(int(x) for x in out.splitlines() if x.strip())


def t_grant_maps_github_ids_to_internal_ids():
    ids = _grant(USER_A, [5001])
    internal = int(psql("SELECT id FROM installations WHERE github_installation_id = 5001"))
    assert ids == [internal], (ids, internal)


def t_replace_removes_access_that_is_gone():
    _grant(USER_A, [5001, 5002])
    assert len(_grant(USER_A, [5002])) == 1
    remaining = psql(
        "SELECT i.github_installation_id FROM user_installation_access a "
        "JOIN installations i ON i.id = a.installation_id WHERE a.user_id = %d" % USER_A
    )
    assert remaining == "5002", f"stale access survived: {remaining!r}"


def t_an_uninstalled_installation_grants_nothing():
    assert _grant(USER_A, [5003]) == [], "access granted to an uninstalled App"


def t_an_unknown_installation_is_skipped_not_invented():
    assert _grant(USER_A, [999999]) == []
    assert psql("SELECT count(*) FROM installations WHERE github_installation_id = 999999") == "0"


def t_an_empty_list_revokes_everything():
    _grant(USER_A, [5001])
    assert _grant(USER_A, []) == []
    assert psql("SELECT count(*) FROM user_installation_access WHERE user_id = %d" % USER_A) == "0"


def t_two_users_do_not_interfere():
    _grant(USER_A, [5001])
    _grant(USER_B, [5002])
    a = psql("SELECT count(*) FROM user_installation_access WHERE user_id = %d" % USER_A)
    b = psql("SELECT count(*) FROM user_installation_access WHERE user_id = %d" % USER_B)
    assert (a, b) == ("1", "1")


check("grant maps GitHub ids to internal ids", t_grant_maps_github_ids_to_internal_ids)
check("replace removes access that is gone", t_replace_removes_access_that_is_gone)
check("an uninstalled installation grants nothing", t_an_uninstalled_installation_grants_nothing)
check("an unknown installation is skipped", t_an_unknown_installation_is_skipped_not_invented)
check("an empty list revokes everything", t_an_empty_list_revokes_everything)
check("one user's sync does not touch another's", t_two_users_do_not_interfere)

print()
print("=== sessions ===")

SESSION_ID = None
TOKEN_HASH = "a" * 64


def t_create_session():
    global SESSION_ID
    sql = bind(AUTH_SQL["create_session"], {
        "user_id": USER_A, "token_hash": TOKEN_HASH, "ttl": 3600,
        "user_agent": "pytest", "ip_hash": "b" * 64,
    })
    SESSION_ID = int(psql(sql))
    assert SESSION_ID > 0


def t_load_session_returns_the_user_and_the_scope():
    _grant(USER_A, [5001])
    sql = bind(AUTH_SQL["load_session"], {"token_hash": TOKEN_HASH, "idle": 7200})
    row = psql(sql)
    assert row, "a live session did not load"
    assert "alice-renamed" in row
    internal = psql("SELECT id FROM installations WHERE github_installation_id = 5001")
    assert internal in row, f"the scope is missing from the session row: {row}"


def t_an_unknown_token_loads_nothing():
    sql = bind(AUTH_SQL["load_session"], {"token_hash": "f" * 64, "idle": 7200})
    assert psql(sql) == ""


def t_an_expired_session_loads_nothing():
    psql("UPDATE user_sessions SET expires_at = now() - interval '1 second' "
         "WHERE id = %d" % SESSION_ID)
    sql = bind(AUTH_SQL["load_session"], {"token_hash": TOKEN_HASH, "idle": 7200})
    assert psql(sql) == ""
    psql("UPDATE user_sessions SET expires_at = now() + interval '1 hour' "
         "WHERE id = %d" % SESSION_ID)


def t_an_idle_session_loads_nothing():
    psql("UPDATE user_sessions SET last_seen_at = now() - interval '10 hours' "
         "WHERE id = %d" % SESSION_ID)
    sql = bind(AUTH_SQL["load_session"], {"token_hash": TOKEN_HASH, "idle": 7200})
    assert psql(sql) == "", "the idle timeout is not enforced"


def t_touch_advances_idle_but_not_absolute_expiry():
    before = psql("SELECT expires_at FROM user_sessions WHERE id = %d" % SESSION_ID)
    psql(bind(AUTH_SQL["touch_session"], {"id": SESSION_ID}))
    after = psql("SELECT expires_at FROM user_sessions WHERE id = %d" % SESSION_ID)
    assert before == after, "activity must not extend the absolute lifetime"
    sql = bind(AUTH_SQL["load_session"], {"token_hash": TOKEN_HASH, "idle": 7200})
    assert psql(sql) != "", "the touch did not revive the idle session"


def t_rotation_changes_which_token_resolves():
    new_hash = "c" * 64
    psql(bind(AUTH_SQL["rotate_session_token"],
              {"id": SESSION_ID, "new_hash": new_hash}))
    old = bind(AUTH_SQL["load_session"], {"token_hash": TOKEN_HASH, "idle": 7200})
    new = bind(AUTH_SQL["load_session"], {"token_hash": new_hash, "idle": 7200})
    assert psql(old) == "", "the pre-rotation token still works — session fixation"
    assert psql(new) != "", "the rotated token does not resolve"


def t_revocation_takes_effect_immediately():
    new_hash = "c" * 64
    psql(bind(AUTH_SQL["revoke_session"], {"id": SESSION_ID, "reason": "logout"}))
    sql = bind(AUTH_SQL["load_session"], {"token_hash": new_hash, "idle": 7200})
    assert psql(sql) == "", "logout did not end the session"


def t_revoked_reason_requires_revoked_at():
    out = psql(
        "INSERT INTO user_sessions (user_id, token_hash, issued_at, expires_at, "
        "last_seen_at, revoked_reason, created_at, updated_at) VALUES "
        "(%d, 'deadbeef', now(), now(), now(), 'logout', now(), now())" % USER_A,
        expect_error=True,
    )
    remaining = psql("SELECT count(*) FROM user_sessions WHERE token_hash = 'deadbeef'")
    assert remaining == "0", "the check constraint did not fire"


def t_revoke_all_ends_every_session():
    for i in range(3):
        psql(bind(AUTH_SQL["create_session"], {
            "user_id": USER_A, "token_hash": f"{i}" * 64, "ttl": 3600,
            "user_agent": None, "ip_hash": None,
        }))
    revoked = psql(bind(AUTH_SQL["revoke_all_sessions"],
                        {"user_id": USER_A, "reason": "logout_all"}))
    assert len([r for r in revoked.splitlines() if r.strip()]) == 3
    live = psql("SELECT count(*) FROM user_sessions WHERE user_id = %d "
                "AND revoked_at IS NULL" % USER_A)
    assert live == "0"


def t_deleting_a_user_cascades_to_their_sessions():
    victim = _upsert(7009, "temp")
    psql(bind(AUTH_SQL["create_session"], {
        "user_id": victim, "token_hash": "e" * 64, "ttl": 3600,
        "user_agent": None, "ip_hash": None,
    }))
    psql("DELETE FROM users WHERE id = %d" % victim)
    assert psql("SELECT count(*) FROM user_sessions WHERE user_id = %d" % victim) == "0"


check("create_session", t_create_session)
check("load_session returns user and scope in one round trip", t_load_session_returns_the_user_and_the_scope)
check("an unknown token loads nothing", t_an_unknown_token_loads_nothing)
check("an expired session loads nothing", t_an_expired_session_loads_nothing)
check("an idle session loads nothing", t_an_idle_session_loads_nothing)
check("touch advances idle, not absolute expiry", t_touch_advances_idle_but_not_absolute_expiry)
check("rotation invalidates the previous token", t_rotation_changes_which_token_resolves)
check("revocation takes effect immediately", t_revocation_takes_effect_immediately)
check("revoked_reason requires revoked_at", t_revoked_reason_requires_revoked_at)
check("revoke-all ends every session", t_revoke_all_ends_every_session)
check("deleting a user cascades to sessions", t_deleting_a_user_cascades_to_their_sessions)

print()
print("=== oauth state ===")


def t_state_is_single_use():
    psql(bind(AUTH_SQL["create_oauth_state"],
              {"state_hash": "s" * 64, "redirect_path": "/findings", "ttl": 600}))
    first = psql(bind(AUTH_SQL["consume_oauth_state"], {"state_hash": "s" * 64}))
    assert first == "/findings", f"the redirect path did not survive: {first!r}"
    second = psql(bind(AUTH_SQL["consume_oauth_state"], {"state_hash": "s" * 64}))
    assert second == "", "a replayed state was accepted"


def t_an_expired_state_is_refused():
    psql(bind(AUTH_SQL["create_oauth_state"],
              {"state_hash": "t" * 64, "redirect_path": None, "ttl": 600}))
    psql("UPDATE oauth_states SET expires_at = now() - interval '1 second' "
         "WHERE state_hash = '%s'" % ("t" * 64))
    assert psql(bind(AUTH_SQL["consume_oauth_state"], {"state_hash": "t" * 64})) == ""


def t_duplicate_state_is_rejected_by_the_unique_index():
    psql(bind(AUTH_SQL["create_oauth_state"],
              {"state_hash": "u" * 64, "redirect_path": None, "ttl": 600}))
    psql(bind(AUTH_SQL["create_oauth_state"],
              {"state_hash": "u" * 64, "redirect_path": None, "ttl": 600}),
         expect_error=True)
    count = psql("SELECT count(*) FROM oauth_states WHERE state_hash = '%s'" % ("u" * 64))
    assert count == "1", "the unique constraint on state_hash is missing"


check("a state value is single-use", t_state_is_single_use)
check("an expired state is refused", t_an_expired_state_is_refused)
check("a duplicate state is rejected", t_duplicate_state_is_rejected_by_the_unique_index)

print()
print("=== index usage ===")


def t_session_lookup_uses_the_unique_index():
    psql("ANALYZE user_sessions")
    plan = psql(
        "SET enable_seqscan = off; EXPLAIN SELECT * FROM user_sessions "
        "WHERE token_hash = 'x'"
    )
    assert "uq_user_sessions_token_hash" in plan, plan


def t_the_access_lookup_uses_the_primary_key():
    psql("ANALYZE user_installation_access")
    plan = psql(
        "SET enable_seqscan = off; EXPLAIN SELECT installation_id "
        "FROM user_installation_access WHERE user_id = 1"
    )
    assert "pk_user_installation_access" in plan, plan


def t_the_fk_indexes_exist():
    for name in (
        "ix_changed_files_review_run_id",
        "ix_findings_changed_file_id",
        "ix_review_runs_webhook_event_id",
        "ix_changed_files_created_at_unanalysed",
        "ix_user_installation_access_installation_id",
    ):
        found = psql("SELECT count(*) FROM pg_indexes WHERE indexname = '%s'" % name)
        assert found == "1", f"{name} is missing"


check("session lookup uses the unique index", t_session_lookup_uses_the_unique_index)
check("access lookup uses the primary key", t_the_access_lookup_uses_the_primary_key)
check("the Phase 5 indexes all exist", t_the_fk_indexes_exist)

print()
print(f"{passed} passed, {failed} failed")
if problems:
    print("\n".join(problems))
sys.exit(1 if failed else 0)
