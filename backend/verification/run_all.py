"""Run every verification harness and report honestly. Phase 6 §29.

These harnesses exist because the release environment could not install
SQLAlchemy, asyncpg, FastAPI or pytest-asyncio (PyPI returns 403), and a
release that says "the SQL is correct" without ever executing it is asserting,
not verifying. Each script drives the **exact SQL the application builds** —
extracted from the repository modules by AST, or produced by calling the real
query-builder functions — against a real PostgreSQL 16.

What they do not cover, and no wording here should suggest otherwise:
SQLAlchemy's parameter binding and session lifecycle, and the FastAPI request
path. Those need the real drivers. They are recorded as BLOCKED in
``docs/final-verification-matrix.md``.

Usage::

    CODEGUARD_PGDIR=/var/tmp/pg3 CODEGUARD_PGPORT=55433 python3 run_all.py

Environment:
    CODEGUARD_BACKEND  backend root (default: this file's parent directory)
    CODEGUARD_PGDIR    PostgreSQL unix socket directory (default /var/tmp/pg3)
    CODEGUARD_PGPORT   PostgreSQL port (default 55433)

Exit code is non-zero if any harness reports a failure, so this is usable as a
release gate.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

# Ordered. ``verify_migration_cycle`` rebuilds its own database and
# ``verify_dashboard_sql`` seeds its own fixtures, so the order below is for
# readability rather than for correctness — but each script must leave the
# databases in a state the next one can rebuild from, and running them in this
# order is what has actually been exercised.
HARNESSES = [
    ("verify_migration_cycle.py", "migrations upgrade, downgrade and upgrade again"),
    ("verify_queue_concurrency.py", "job queue under genuinely concurrent workers"),
    ("verify_review_run_race.py", "stale pull requests and concurrent review runs"),
    ("verify_phase3_db.py", "review entities, constraints and cascades"),
    ("verify_auth_sql.py", "sessions, scope and the authorization boundary"),
    ("verify_dashboard_sql.py", "every dashboard query, and the numbers it returns"),
    ("final_audit.py", "security, configuration, schema parity, analytics integrity"),
]

_SUMMARY = re.compile(r"(\d+) passed, (\d+) failed")


def main() -> int:
    total_passed = total_failed = 0
    rows: list[tuple[str, int, int, str]] = []

    for script, description in HARNESSES:
        path = HERE / script
        if not path.exists():
            print(f"  MISSING  {script}")
            total_failed += 1
            continue
        proc = subprocess.run(
            [sys.executable, str(path)], capture_output=True, text=True, cwd=HERE
        )
        output = proc.stdout + proc.stderr
        match = None
        for match in _SUMMARY.finditer(output):
            pass
        if match is None:
            print(f"  ERROR    {script}: no summary line — the harness did not finish")
            print("           " + output.strip().splitlines()[-1][:160] if output.strip() else "")
            total_failed += 1
            continue
        passed, failed = int(match.group(1)), int(match.group(2))
        total_passed += passed
        total_failed += failed
        status = "ok  " if failed == 0 else "FAIL"
        print(f"  {status}  {script:<30} {passed:>4} passed, {failed:>2} failed   {description}")
        rows.append((script, passed, failed, description))
        if failed:
            for line in output.splitlines():
                if line.strip().startswith("FAIL"):
                    print(f"           {line.strip()[:150]}")

    print(f"\n  TOTAL: {total_passed} passed, {total_failed} failed")
    if total_failed:
        print("\n  A failure here is not a formatting problem. Read it before releasing.")
    return 1 if total_failed else 0


if __name__ == "__main__":
    print("CodeGuard AI — PostgreSQL verification harnesses\n")
    raise SystemExit(main())
