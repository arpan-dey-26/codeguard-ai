"""Execute every dashboard query against a REAL PostgreSQL 16.

The dashboard's whole value is that its numbers are true. SQLAlchemy cannot be
installed here (PyPI 403), so instead of trusting that an ORM would emit what
was meant, this harness takes the *exact SQL the application builds* — by
calling the real query-builder functions — binds the parameters, and runs it.

It does two things:
  1. smoke: every query is valid SQL against the real schema;
  2. correctness: the analytics fixtures from the Phase 4 brief produce the
     numbers the brief specifies.

Limitation, stated rather than glossed: parameters are inlined as literals here
because asyncpg is unavailable. The application binds them as real parameters.
So this verifies the SQL and the aggregation logic, not SQLAlchemy's binding.
"""
from __future__ import annotations

import os as _os
import pathlib as _pathlib

_HERE = _pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = _pathlib.Path(_os.environ.get("CODEGUARD_BACKEND", _HERE.parent))
PG_SOCKET_DIR = _os.environ.get("CODEGUARD_PGDIR", "/var/tmp/pg3")
PG_PORT = _os.environ.get("CODEGUARD_PGPORT", "55433")


import datetime as dt
import pathlib
import subprocess
import sys
import uuid

sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(
    0,
    str(_HERE / "shim_minimal"),
)

from app.api.filters import (  # noqa: E402
    FindingFilters,
    PullRequestFilters,
    RepositoryFilters,
    RunFilters,
    TimeRange,
)
from app.api.scope import ViewerScope  # noqa: E402
from app.db.repositories import dashboard as q  # noqa: E402

PG_DIR = PG_SOCKET_DIR
RESULTS: list[tuple[str, bool, str]] = []


def psql(sql: str, *, tuples_only: bool = True) -> tuple[int, str]:
    args = ["psql", "-h", PG_DIR, "-p", PG_PORT, "-U", "postgres", "-d", "codeguard",
            "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        args += ["-t", "-A", "-F", "|"]
    args += ["-c", sql]
    proc = subprocess.run(
        ["su", "postgres", "-c", " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)],
        capture_output=True, text=True,
        env={"PATH": "/usr/lib/postgresql/16/bin:/usr/bin:/bin"},
    )
    out = (proc.stdout + proc.stderr).strip()
    if tuples_only:
        out = "\n".join(
            ln for ln in out.splitlines()
            if not ln.startswith(("INSERT ", "UPDATE ", "DELETE ", "SELECT ", "TRUNCATE"))
        ).strip()
    return proc.returncode, out


def lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dt.datetime):
        return "'" + value.isoformat() + "'::timestamptz"
    if isinstance(value, list):
        if not value:
            return "ARRAY[]::text[]"
        inner = ",".join(lit(v) for v in value)
        if all(isinstance(v, int) for v in value):
            return f"ARRAY[{inner}]::bigint[]"
        return f"ARRAY[{inner}]::text[]"
    return "'" + str(value).replace("'", "''") + "'"


def bind(query: q.Query) -> str:
    sql = query.sql
    for key in sorted(query.params, key=len, reverse=True):
        sql = sql.replace(":" + key, lit(query.params[key]))
    return sql


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "  PASS  " if ok else "  FAIL  "
    print(mark + name + (f"\n          {detail[:400]}" if detail and not ok else ""))


def run_ok(name: str, query: q.Query) -> str:
    rc, out = psql(bind(query))
    check(name, rc == 0, out)
    return out


# ---------------------------------------------------------------------------
# Fixture: exactly the shape the Phase 4 brief §42 asks for.
# ---------------------------------------------------------------------------

# Follows the real clock. This was pinned to a fixed date, and every fixture
# row is inserted with the database's `now()`: two days after the script was
# written the rows fell outside the window and eleven correctness checks
# silently returned 0. A verification harness that reports a false failure
# on a Thursday is a harness nobody trusts on a Friday.
NOW = dt.datetime.now(dt.UTC)
RANGE = TimeRange(start=NOW - dt.timedelta(days=30), end=NOW + dt.timedelta(days=1))

SHA_A1 = "a" * 40   # PR A, first commit  (superseded / stale)
SHA_A2 = "b" * 40   # PR A, current commit
SHA_B1 = "c" * 40   # PR B, current commit

RUN_A1 = "018f0001-0000-7000-8000-000000000001"  # PR A attempt 1, old commit
RUN_A2 = "018f0001-0000-7000-8000-000000000002"  # PR A attempt 1, current commit
RUN_B1 = "018f0001-0000-7000-8000-000000000003"  # PR B attempt 1


def seed() -> tuple[int, int, int, int]:
    psql("TRUNCATE installations, webhook_events CASCADE;", tuples_only=False)
    psql(
        """
        INSERT INTO installations (github_installation_id, account_login, account_type, account_github_id)
        VALUES (1,'acme','Organization',10);
        INSERT INTO repositories (installation_id, github_repo_id, owner_login, name, full_name,
                                  default_branch, is_private, is_active, primary_language)
        SELECT id, 100,'acme','web','acme/web','main',false,true,'Python' FROM installations;
        INSERT INTO repositories (installation_id, github_repo_id, owner_login, name, full_name,
                                  default_branch, is_private, is_active, primary_language)
        SELECT id, 101,'acme','api','acme/api','main',true,true,'Python' FROM installations;
        """,
        tuples_only=False,
    )
    _, out = psql("SELECT id FROM repositories ORDER BY github_repo_id")
    repo_web, repo_api = (int(x) for x in out.split())
    _, out = psql("SELECT id FROM installations")
    installation_id = int(out)

    psql(
        f"""
        INSERT INTO pull_requests (repository_id, github_pr_id, number, title, author_login, state,
            is_draft, base_ref, head_ref, html_url, changed_files_count, changed_files_truncated,
            head_sha, base_sha, updated_at)
        VALUES
          ({repo_web}, 1001, 7, 'Add session store', 'dev-a', 'open', false, 'main','feat/a','',
           3, false, {lit(SHA_A2)}, 'f{"0"*39}', now() - interval '1 hour'),
          ({repo_web}, 1002, 8, 'Fix billing rounding', 'dev-b', 'open', false, 'main','feat/b','',
           1, false, {lit(SHA_B1)}, 'f{"0"*39}', now() - interval '2 hours');
        """,
        tuples_only=False,
    )
    _, out = psql("SELECT id FROM pull_requests ORDER BY number")
    pr_a, pr_b = (int(x) for x in out.split())

    # PR A: two review ATTEMPTS across two commits — one superseded (stale),
    # one current. PR B: one attempt. Unique PRs reviewed = 2, runs = 3.
    for run_id, pr, repo, sha, attempt, status, emitted, dropped, inline, summary, dash in [
        (RUN_A1, pr_a, repo_web, SHA_A1, 1, "superseded", 0, 0, 0, 0, 0),
        (RUN_A2, pr_a, repo_web, SHA_A2, 1, "partial_success", 7, 1, 3, 1, 2),
        (RUN_B1, pr_b, repo_web, SHA_B1, 1, "succeeded", 3, 0, 2, 0, 1),
    ]:
        psql(
            f"""
            INSERT INTO review_runs (id, pull_request_id, repository_id, trigger, head_sha,
                base_sha, attempt, status, started_at, finished_at, duration_ms,
                findings_emitted, findings_dropped, findings_persisted, findings_inline,
                findings_summary, findings_dashboard_only, files_changed, files_analysed,
                files_skipped, context_truncated, created_at, updated_at,
                provider, model_name, prompt_version, error_code)
            VALUES ({lit(run_id)}::uuid, {pr}, {repo}, 'opened', {lit(sha)}, 'f{"0"*39}',
                {attempt}, {lit(status)}, now() - interval '30 minutes',
                now() - interval '29 minutes', 61000,
                {emitted}, {dropped}, {inline + summary + dash}, {inline}, {summary}, {dash},
                3, 2, 1, {lit(status == 'partial_success')}, now() - interval '30 minutes', now(),
                'gemini', 'gemini-test', '2026.09.16-v1',
                {lit('publish_failed' if status == 'partial_success' else None)});
            """,
            tuples_only=False,
        )

    # 10 claims on the current runs: 6 validated, 4 rejected. 5 published.
    findings: list[tuple] = []
    plan = [
        # (run, validated, severity, category, published, status)
        (RUN_A2, True, "critical", "security", True, "open"),
        (RUN_A2, True, "high", "resource", True, "open"),
        (RUN_A2, True, "medium", "bug", True, "open"),
        (RUN_A2, True, "low", "maintainability", False, "open"),
        (RUN_A2, False, "info", "maintainability", False, "open"),
        (RUN_A2, False, "info", "maintainability", False, "open"),
        (RUN_B1, True, "high", "security", True, "open"),
        (RUN_B1, True, "medium", "performance", True, "resolved"),
        (RUN_B1, False, "info", "maintainability", False, "open"),
        (RUN_B1, False, "info", "maintainability", False, "open"),
    ]
    reasons = ["unsafe_path", "anchor_not_found", "line_not_in_diff", "wrong_side"]
    reason_i = 0
    for index, (run, validated, severity, category, published, status) in enumerate(plan):
        pr = pr_a if run == RUN_A2 else pr_b
        fid = str(uuid.UUID(int=0x018F1111_0000_7000_8000_000000000000 + index))
        if validated:
            rejection, resolved, offset, verified, mapping = (
                None, 10 + index, 0 if index % 2 == 0 else 1, True, "inline",
            )
        else:
            rejection, resolved, offset, verified, mapping = (
                reasons[reason_i % len(reasons)], None, None, False, "dashboard_only",
            )
            reason_i += 1
        psql(
            f"""
            INSERT INTO findings (id, review_run_id, repository_id, pull_request_id,
                model_finding_id, category, severity, title, description, impact,
                file_path, claimed_line_start, resolved_line, anchor_offset, side,
                anchor_snippet, anchor_verified, model_confidence, effective_confidence,
                mapping_status, rejection_reason, fingerprint, status, created_at, updated_at)
            VALUES ({lit(fid)}::uuid, {lit(run)}::uuid, {repo_web}, {pr},
                {lit('F%03d' % index)}, {lit(category)}, {lit(severity)},
                {lit('Finding %d' % index)}, 'desc', 'impact',
                'app/session.py', {10 + index}, {lit(resolved)}, {lit(offset)}, 'RIGHT',
                'handle = open(path)', {lit(verified)}, 0.90, 0.90,
                {lit(mapping)}, {lit(rejection)}, {lit('%032d' % index)}, {lit(status)},
                now() - interval '25 minutes', now());
            """,
            tuples_only=False,
        )
        findings.append((fid, published, run))

    # 5 published comments; one validated finding deliberately has none, which
    # is the "publication failed / not published" case.
    for fid, published, run in findings:
        if not published:
            continue
        psql(
            f"""
            INSERT INTO review_comments (finding_id, review_run_id, github_review_id,
                github_comment_id, path, line, side, commit_sha, body_hash, posted_at)
            VALUES ({lit(fid)}::uuid, {lit(run)}::uuid, 9001,
                {abs(hash(fid)) % 1_000_000}, 'app/session.py', 14, 'RIGHT',
                {lit(SHA_A2)}, {lit('0' * 64)}, now());
            """,
            tuples_only=False,
        )

    # Changed files, for coverage and skip-reason queries.
    for pr, sha in ((pr_a, SHA_A2), (pr_b, SHA_B1)):
        psql(
            f"""
            INSERT INTO changed_files (pull_request_id, head_sha, file_path, change_status,
                additions, deletions, changes, has_patch, language, is_analysed, skip_reason,
                parse_ok, declarations_touched, created_at, updated_at)
            VALUES
              ({pr}, {lit(sha)}, 'app/session.py', 'modified', 12, 3, 15, true, 'python', true,
               NULL, true, 2, now(), now()),
              ({pr}, {lit(sha)}, 'assets/logo.png', 'added', 0, 0, 0, false, NULL, false,
               'binary', NULL, NULL, now(), now()),
              ({pr}, {lit(sha)}, 'vendor/big.min.js', 'modified', 900, 900, 1800, true,
               'javascript', false, 'file_too_large', NULL, NULL, now(), now());
            """,
            tuples_only=False,
        )
    return repo_web, repo_api, pr_a, pr_b, installation_id  # type: ignore[return-value]


def main() -> int:
    repo_web, repo_api, pr_a, pr_b, installation_id = seed()  # type: ignore[misc]
    scope = ViewerScope.unrestricted()
    restricted = ViewerScope.for_installations((installation_id,))
    blind = ViewerScope.for_installations(())

    print("\n== every query is valid SQL against the real schema ==")
    run_ok("overview_summary", q.overview_summary(scope=scope, time_range=RANGE))
    run_ok("findings_by_severity", q.findings_by_severity(scope=scope, time_range=RANGE))
    run_ok("findings_by_category", q.findings_by_category(scope=scope, time_range=RANGE))
    run_ok("rejection_reasons", q.rejection_reasons(scope=scope, time_range=RANGE))
    run_ok("anchor_distribution", q.anchor_distribution(scope=scope, time_range=RANGE))
    run_ok("review_trends[day]", q.review_trends(scope=scope, time_range=RANGE, bucket="day"))
    run_ok("review_trends[week]", q.review_trends(scope=scope, time_range=RANGE, bucket="week"))
    run_ok("repositories_page", q.repositories_page(
        scope=scope, filters=RepositoryFilters(), cursor={}, limit=10))
    run_ok("repository_detail", q.repository_detail(scope=scope, repository_id=repo_web))
    run_ok("pull_requests_page", q.pull_requests_page(
        scope=scope, filters=PullRequestFilters(), cursor={}, limit=10))
    run_ok("pull_request_detail", q.pull_request_detail(scope=scope, pull_request_id=pr_a))
    run_ok("pull_request_runs", q.pull_request_runs(pull_request_id=pr_a))
    run_ok("review_runs_page", q.review_runs_page(
        scope=scope, filters=RunFilters(), cursor={}, limit=10))
    run_ok("review_run_detail", q.review_run_detail(scope=scope, review_run_id=RUN_A2))
    run_ok("review_run_changed_files", q.review_run_changed_files(review_run_id=RUN_A2))
    run_ok("findings_page", q.findings_page(
        scope=scope, filters=FindingFilters(), cursor={}, limit=10))
    run_ok("findings_count", q.findings_count(scope=scope, filters=FindingFilters()))
    run_ok("finding_detail", q.finding_detail(
        scope=scope, finding_id=str(uuid.UUID(int=0x018F1111_0000_7000_8000_000000000000))))
    run_ok("finding_events", q.finding_events(
        finding_id=str(uuid.UUID(int=0x018F1111_0000_7000_8000_000000000000))))
    run_ok("processing_health", q.processing_health(scope=scope, time_range=RANGE))
    run_ok("run_error_breakdown", q.run_error_breakdown(scope=scope, time_range=RANGE))
    run_ok("integration_status", q.integration_status(scope=scope))
    run_ok("recent_activity", q.recent_activity(scope=scope))
    run_ok("coverage_summary", q.coverage_summary(scope=scope, time_range=RANGE))
    run_ok("skip_reason_breakdown", q.skip_reason_breakdown(scope=scope, time_range=RANGE))

    print("\n== analytics correctness (brief section 42) ==")
    rc, out = psql(bind(q.overview_summary(scope=scope, time_range=RANGE)))
    cols = [
        "repositories_visible", "repositories_active", "pull_requests_reviewed",
        "review_runs_total", "runs_succeeded", "runs_partial_success", "runs_failed",
        "runs_superseded", "runs_in_flight", "runs_limited_coverage",
        "claims_emitted", "claims_dropped", "validated_findings", "rejected_claims",
        "open_findings", "open_high_severity", "published_comments", "findings_demoted",
        "duration_p50_ms", "duration_p95_ms", "anchors_exact", "anchors_relocated",
    ]
    values = dict(zip(cols, out.split("|")))
    print("   overview:", {k: values[k] for k in (
        "pull_requests_reviewed", "review_runs_total", "validated_findings",
        "rejected_claims", "published_comments", "runs_superseded")})

    check("unique pull requests reviewed = 2 (attempts are not PRs)",
          values["pull_requests_reviewed"] == "2", values.get("pull_requests_reviewed", ""))
    check("review runs = 3 (attempts counted separately)",
          values["review_runs_total"] == "3", values.get("review_runs_total", ""))
    check("validated findings = 6", values["validated_findings"] == "6",
          values.get("validated_findings", ""))
    check("rejected claims = 4", values["rejected_claims"] == "4",
          values.get("rejected_claims", ""))
    check("published comments = 5", values["published_comments"] == "5",
          values.get("published_comments", ""))
    check("validated and published stay distinct (6 != 5)",
          values["validated_findings"] != values["published_comments"])
    check("claims emitted = 10 and dropped = 1 are separate counters",
          values["claims_emitted"] == "10" and values["claims_dropped"] == "1",
          f"{values['claims_emitted']}/{values['claims_dropped']}")
    check("superseded run counted as superseded, not as a success",
          values["runs_superseded"] == "1" and values["runs_succeeded"] == "1",
          f"{values['runs_superseded']}/{values['runs_succeeded']}")

    rc, out = psql(bind(q.findings_by_severity(scope=scope, time_range=RANGE)))
    by_sev = dict(line.split("|") for line in out.splitlines() if "|" in line)
    check("severity chart counts validated findings only (no 'info' rejections)",
          by_sev.get("info") is None and sum(int(v) for v in by_sev.values()) == 6, out)

    rc, out = psql(bind(q.rejection_reasons(scope=scope, time_range=RANGE)))
    reasons = dict(line.split("|") for line in out.splitlines() if "|" in line)
    check("rejection reasons are reported per gate",
          sum(int(v) for v in reasons.values()) == 4, out)

    # -- stale review must never be the current one -------------------------
    rc, out = psql(bind(q.pull_requests_page(
        scope=scope, filters=PullRequestFilters(), cursor={}, limit=10)))
    rows = [line.split("|") for line in out.splitlines() if "|" in line]
    pr_a_row = next(r for r in rows if r[1] == "7")
    current_run_id = pr_a_row[15]
    check("a superseded run is never shown as the PR's current review",
          current_run_id == RUN_A2, f"current_run_id={current_run_id}")

    rc, out = psql(bind(q.pull_request_runs(pull_request_id=pr_a)))
    run_rows = [line.split("|") for line in out.splitlines() if "|" in line]
    check("but both attempts remain visible in the PR's history",
          len(run_rows) == 2, str(len(run_rows)))
    stale_flags = {r[0]: r[-1] for r in run_rows}
    check("the old-commit run is flagged stale", stale_flags.get(RUN_A1) == "t",
          str(stale_flags))
    check("the current-commit run is not flagged stale", stale_flags.get(RUN_A2) == "f",
          str(stale_flags))

    # -- duplicate fingerprints are not double counted ----------------------
    psql(f"""
        INSERT INTO findings (id, review_run_id, repository_id, pull_request_id,
            model_finding_id, category, severity, title, description, impact,
            file_path, claimed_line_start, resolved_line, anchor_offset, side,
            anchor_snippet, anchor_verified, model_confidence, effective_confidence,
            mapping_status, rejection_reason, fingerprint, status, created_at, updated_at)
        VALUES ('018f9999-0000-7000-8000-000000000001'::uuid, {lit(RUN_A2)}::uuid,
            {repo_web}, {pr_a}, 'FDUP', 'bug', 'high', 'Finding 0', 'desc', 'impact',
            'app/session.py', 10, 10, 0, 'RIGHT', 'x', true, 0.9, 0.9,
            'inline', NULL, {lit('%032d' % 0)}, 'open', now(), now());
    """, tuples_only=False)
    rc, out = psql("SELECT count(*) FROM findings WHERE fingerprint = " + lit("%032d" % 0))
    check("the (review_run_id, fingerprint) unique index blocks a duplicate row",
          out.strip() == "1", out)

    # -- scope is enforced in SQL -------------------------------------------
    rc, out = psql(bind(q.findings_page(
        scope=blind, filters=FindingFilters(), cursor={}, limit=10)))
    check("a caller scoped to no installation sees no findings", out.strip() == "", out[:200])

    rc, out = psql(bind(q.repository_detail(scope=blind, repository_id=repo_web)))
    check("an out-of-scope repository returns no row (route renders 404)",
          out.strip() == "", out[:200])

    rc, out = psql(bind(q.findings_page(
        scope=restricted, filters=FindingFilters(), cursor={}, limit=100)))
    check("a scoped caller still sees their own installation's findings",
          len([x for x in out.splitlines() if "|" in x]) == 10, out[:200])

    # -- filters -------------------------------------------------------------
    rc, out = psql(bind(q.findings_page(
        scope=scope, filters=FindingFilters(validation="rejected"), cursor={}, limit=100)))
    check("validation=rejected returns only rejected claims",
          len([x for x in out.splitlines() if "|" in x]) == 4, out[:200])

    rc, out = psql(bind(q.findings_page(
        scope=scope, filters=FindingFilters(publication="published"), cursor={}, limit=100)))
    check("publication=published is derived from review_comments rows",
          len([x for x in out.splitlines() if "|" in x]) == 5, out[:200])

    rc, out = psql(bind(q.findings_page(
        scope=scope, filters=FindingFilters(severities=("critical", "high")),
        cursor={}, limit=100)))
    check("severity filter narrows correctly",
          len([x for x in out.splitlines() if "|" in x]) == 3, out[:200])

    rc, out = psql(bind(q.findings_page(
        scope=scope, filters=FindingFilters(search="Finding 1"), cursor={}, limit=100)))
    check("free-text search is bound, not interpolated",
          rc == 0 and len([x for x in out.splitlines() if "|" in x]) >= 1, out[:200])

    injection = "'; DROP TABLE findings; --"
    rc, out = psql(bind(q.findings_page(
        scope=scope, filters=FindingFilters(search=injection), cursor={}, limit=100)))
    rc2, still_there = psql("SELECT count(*) FROM findings")
    check("a SQL-injection payload in a search term changes nothing",
          rc == 0 and int(still_there) > 0, f"rc={rc} rows={still_there}")

    wildcard = "%"
    rc, out = psql(bind(q.findings_page(
        scope=scope, filters=FindingFilters(search=wildcard), cursor={}, limit=100)))
    check("a literal % in a search term does not become a wildcard",
          len([x for x in out.splitlines() if "|" in x]) == 0, out[:200])

    # -- pagination ----------------------------------------------------------
    rc, page1 = psql(bind(q.findings_page(
        scope=scope, filters=FindingFilters(), cursor={}, limit=3)))
    rows1 = [x.split("|") for x in page1.splitlines() if "|" in x]
    check("over-fetch returns limit+1 rows so 'has more' needs no COUNT",
          len(rows1) == 4, str(len(rows1)))

    # -- time range ----------------------------------------------------------
    empty_range = TimeRange(
        start=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        end=dt.datetime(2020, 1, 2, tzinfo=dt.UTC),
    )
    rc, out = psql(bind(q.overview_summary(scope=scope, time_range=empty_range)))
    values2 = dict(zip(cols, out.split("|")))
    check("an empty window reports zeros, not the all-time totals",
          values2["review_runs_total"] == "0" and values2["validated_findings"] == "0", out)

    aligned = RANGE.snapped("day")
    rc, out = psql(bind(q.review_trends(scope=scope, time_range=aligned, bucket="day")))
    trend_rows = [x for x in out.splitlines() if "|" in x]
    check("a snapped range yields exactly one bucket per day, gap-filled",
          len(trend_rows) == aligned.days, f"{len(trend_rows)} rows for {aligned.days} days")
    check("snapping makes both ends land on day boundaries",
          aligned.start.hour == 0 and aligned.end.hour == 0,
          f"{aligned.start} -> {aligned.end}")
    zero_days = [r for r in trend_rows if r.split("|")[1] == "0"]
    check("quiet days appear as zero rather than being omitted",
          len(zero_days) >= 28, str(len(zero_days)))

    # -- coverage ------------------------------------------------------------
    rc, out = psql(bind(q.skip_reason_breakdown(scope=scope, time_range=RANGE)))
    skips = dict(line.split("|") for line in out.splitlines() if "|" in line)
    check("skip reasons are reported by cause, not merged into one number",
          skips.get("binary") == "2" and skips.get("file_too_large") == "2", out)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
