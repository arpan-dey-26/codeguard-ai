
import os as _os
import pathlib as _pathlib

_HERE = _pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = _pathlib.Path(_os.environ.get("CODEGUARD_BACKEND", _HERE.parent))
PG_SOCKET_DIR = _os.environ.get("CODEGUARD_PGDIR", "/var/tmp/pg3")
PG_PORT = _os.environ.get("CODEGUARD_PGPORT", "55433")

"""Phase 3 persistence verification against a REAL PostgreSQL 16.

SQLAlchemy and asyncpg cannot be installed here (the package index returns
403), so the repository classes cannot be instantiated. Instead, the SQL
statements are extracted **verbatim from the production source** by parsing
`app/db/repositories/reviews.py` and pulling the string literals out of each
`text(...)` call, then executed through psql against a live server.

What this verifies honestly:
  * the statements are syntactically valid PostgreSQL against the real schema;
  * ON CONFLICT DO NOTHING makes persist idempotent;
  * the supersession UPDATE matches the rows it should and no others;
  * duration_ms is computed server-side;
  * the previously_reported join returns only fingerprints with a comment.

What it does NOT verify: the Python glue in the repository classes, parameter
binding by SQLAlchemy, or transaction boundaries under concurrency. Those need
the real driver and are listed as not-executed in the report.
"""
import ast
import json
import pathlib
import subprocess
import sys

SRC = BACKEND_ROOT / "app" / "db" / "repositories" / "reviews.py"
PG = ["-h", PG_SOCKET_DIR, "-p", PG_PORT, "-U", "postgres", "-d", "codeguard"]


def extract_sql(path: pathlib.Path) -> dict[str, str]:
    """Pull every text(...) string literal out, keyed by enclosing method."""
    tree = ast.parse(path.read_text())
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for index, call in enumerate(
            n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "text"
        ):
            if call.args and isinstance(call.args[0], ast.Constant):
                out[f"{node.name}#{index}"] = call.args[0].value
    return out


def psql(sql: str, *, tuples_only: bool = False) -> tuple[int, str]:
    args = ["psql", *PG, "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        args += ["-t", "-A"]
    args += ["-c", sql]
    proc = subprocess.run(
        ["su", "postgres", "-c", " ".join(_q(a) for a in args)],
        capture_output=True, text=True,
        env={"PATH": "/usr/lib/postgresql/16/bin:/usr/bin:/bin"},
    )
    text_out = (proc.stdout + proc.stderr).strip()
    # With -t -A psql still emits the command tag ("INSERT 0 0") on its own
    # line. Strip tags so an empty result really means "no row returned".
    if tuples_only:
        text_out = "\n".join(
            line for line in text_out.splitlines()
            if not line.startswith(("INSERT ", "UPDATE ", "DELETE ", "SELECT "))
        ).strip()
    return proc.returncode, text_out


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def bind(sql: str, params: dict) -> str:
    """Replace :name placeholders with literals, longest name first."""
    out = sql
    for key in sorted(params, key=len, reverse=True):
        value = params[key]
        if value is None:
            literal = "NULL"
        elif isinstance(value, bool):
            literal = "true" if value else "false"
        elif isinstance(value, (int, float)):
            literal = str(value)
        elif isinstance(value, list):
            elements = ",".join("'" + str(v).replace("'", "''") + "'" for v in value)
            # The harness inlines literals, so an array of UUID strings needs an
            # explicit cast that SQLAlchemy's driver would have supplied.
            cast = "::uuid[]" if all(len(str(v)) == 36 and "-" in str(v) for v in value) else ""
            literal = "ARRAY[" + elements + "]" + cast
        else:
            literal = "'" + str(value).replace("'", "''") + "'"
        out = out.replace(":" + key, literal)
    return out


RESULTS = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, condition, detail))
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"  [{detail}]" if detail and not condition else ""))


def main() -> int:
    sql = extract_sql(SRC)
    print(f"Extracted {len(sql)} SQL statements from {SRC.name}\n")

    # Reset to a known state.
    psql("TRUNCATE installations CASCADE;")
    psql("""
        INSERT INTO installations (github_installation_id, account_login, account_type, account_github_id)
        VALUES (1,'acme','Organization',10);
        INSERT INTO repositories (installation_id, github_repo_id, owner_login, name, full_name,
                                  default_branch, is_private, is_active)
        SELECT id, 100,'acme','web','acme/web','main',false,true FROM installations;
        INSERT INTO pull_requests (repository_id, github_pr_id, number, title, author_login, state,
                                   is_draft, base_ref, head_ref, html_url, changed_files_count,
                                   changed_files_truncated, head_sha, base_sha)
        SELECT id, 1000, 7, 'PR','dev','open',false,'main','feat','',1,false,
               'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        FROM repositories;
    """)
    rc, out = psql("SELECT id FROM pull_requests LIMIT 1", tuples_only=True)
    pr_id = int(out)
    rc, out = psql("SELECT id FROM repositories LIMIT 1", tuples_only=True)
    repo_id = int(out)

    HEAD = "a" * 40
    NEW_HEAD = "c" * 40
    RUN = "018f2222-0000-7000-8000-000000000001"
    RUN2 = "018f2222-0000-7000-8000-000000000002"

    # -- ReviewRunRepository.create --------------------------------------
    create_sql = next(v for k, v in sql.items() if k.startswith("create#"))
    params = dict(id=RUN, pull_request_id=pr_id, repository_id=repo_id,
                  webhook_event_id=None, trigger="opened", head_sha=HEAD,
                  base_sha="", attempt=1)
    rc, out = psql(bind(create_sql, params), tuples_only=True)
    check("create inserts and returns the run id", rc == 0 and RUN in out, out)

    # Same (pr, sha, attempt) again -> DO NOTHING, no row returned.
    params2 = dict(params, id=RUN2)
    rc, out = psql(bind(create_sql, params2), tuples_only=True)
    check("create is idempotent on (pr, head_sha, attempt)", rc == 0 and out == "", out)

    # -- next_attempt -----------------------------------------------------
    next_sql = next(v for k, v in sql.items() if k.startswith("next_attempt#"))
    rc, out = psql(bind(next_sql, dict(pull_request_id=pr_id, head_sha=HEAD)), tuples_only=True)
    check("next_attempt returns 2 after one run", rc == 0 and out.strip() == "2", out)

    # -- record_provider_call / record_context / record_funnel -----------
    for method, method_params in [
        ("record_provider_call#0", dict(id=RUN, provider="gemini", model_name="gemini-x",
                                        prompt_version="2026.09.16-v1", input_tokens=900,
                                        output_tokens=300, latency_ms=1200, truncated=False)),
        ("record_context#0", dict(id=RUN, files_changed=3, files_analysed=2,
                                  files_skipped=1, context_truncated=True)),
        ("record_funnel#0", dict(id=RUN, emitted=9, persisted=4, inline=2,
                                 summary=1, dashboard_only=5, dropped=5)),
    ]:
        rc, out = psql(bind(sql[method], method_params))
        check(f"{method.split('#')[0]} executes", rc == 0, out)

    rc, out = psql(
        "SELECT provider, prompt_version, findings_emitted, findings_dropped, context_truncated "
        "FROM review_runs WHERE id='%s'" % RUN, tuples_only=True)
    check("the funnel and provider columns round-trip",
          out.strip() == "gemini|2026.09.16-v1|9|5|t", out)

    # -- persist_findings (_insert_one) ----------------------------------
    insert_sql = next(v for k, v in sql.items() if k.startswith("_insert_one#0"))
    base = dict(
        review_run_id=RUN, repository_id=repo_id, pull_request_id=pr_id,
        changed_file_id=None, model_finding_id="F001", category="resource",
        severity="high", title="Unclosed handle", description="d", impact="i",
        suggested_fix=None, rationale=None, cwe_id=None, file_path="app/session.py",
        claimed_line_start=14, claimed_line_end=None, resolved_line=14,
        anchor_offset=0, side="RIGHT", anchor_snippet="handle = open(path)",
        anchor_verified=True, model_confidence=0.90, effective_confidence=0.90,
        mapping_status="inline", rejection_reason=None,
        fingerprint="f" * 32, declaration_id="abc123", qualified_name="S.load",
    )
    f1 = "018f3333-0000-7000-8000-000000000001"
    rc, out = psql(bind(insert_sql, dict(base, id=f1)), tuples_only=True)
    check("a validated finding inserts", rc == 0 and f1 in out, out)

    rc, out = psql(bind(insert_sql, dict(base, id="018f3333-0000-7000-8000-000000000009")),
                   tuples_only=True)
    check("persist is idempotent on (run, fingerprint)", rc == 0 and out == "", out)

    rejected = dict(base, id="018f3333-0000-7000-8000-000000000002",
                    model_finding_id="F003", category="maintainability",
                    severity="info", title="Rejected claim (unsafe_path)",
                    description="path is not a safe relative path",
                    file_path="../../secret", claimed_line_start=999999,
                    resolved_line=None, anchor_offset=None, anchor_snippet="",
                    anchor_verified=False, model_confidence=0.0,
                    effective_confidence=0.0, mapping_status="dashboard_only",
                    rejection_reason="unsafe_path",
                    fingerprint="e" * 32, declaration_id=None, qualified_name="")
    rc, out = psql(bind(insert_sql, rejected), tuples_only=True)
    check("a rejected claim is persisted as dashboard_only", rc == 0 and out != "", out)

    # The CHECK must forbid an inline finding with no verified anchor.
    bad = dict(base, id="018f3333-0000-7000-8000-000000000003",
               fingerprint="d" * 32, resolved_line=None, anchor_verified=False)
    rc, out = psql(bind(insert_sql, bad), tuples_only=True)
    check("an inline finding without a verified anchor is refused by the DB",
          rc != 0 and "inline_requires_verified_anchor" in out, out[:120])

    # -- review_comments --------------------------------------------------
    record_sql = next(v for k, v in sql.items() if k.startswith("record#"))
    cparams = dict(finding_id=f1, run_id=RUN, github_review_id=9001,
                   github_comment_id=5001, path="app/session.py", line=14,
                   side="RIGHT", commit_sha=HEAD, body_hash="0" * 64,
                   html_url="https://github.test/c/1", posted_at="2026-09-16T04:00:00Z")
    rc, out = psql(bind(record_sql, cparams), tuples_only=True)
    check("a posted comment is recorded", rc == 0 and out != "", out)

    rc, out = psql(bind(record_sql, dict(cparams, github_comment_id=5002)), tuples_only=True)
    check("recording the same finding twice is a no-op, not a duplicate",
          rc == 0 and out == "", out)

    # -- previously_reported ---------------------------------------------
    prev_sql = next(v for k, v in sql.items() if k.startswith("previously_reported#"))
    rc, out = psql(bind(prev_sql, dict(pull_request_id=pr_id,
                                       fingerprints=["f" * 32, "e" * 32])),
                   tuples_only=True)
    reported = set(out.split()) if out else set()
    check("previously_reported returns only fingerprints with a comment",
          reported == {"f" * 32}, out)

    # -- mark_summary_only -------------------------------------------------
    demote_sql = next(v for k, v in sql.items() if k.startswith("mark_summary_only#"))
    rc, out = psql(bind(demote_sql, dict(ids=[f1])))
    rc2, status = psql("SELECT mapping_status FROM findings WHERE id='%s'" % f1, tuples_only=True)
    check("demotion moves a finding away from inline", rc == 0 and status.strip() == "summary",
          status)

    # -- supersede_older_runs ---------------------------------------------
    psql(bind(create_sql, dict(params, id=RUN2, head_sha=NEW_HEAD, attempt=1)))
    sup_sql = next(v for k, v in sql.items() if k.startswith("supersede_older_runs#"))
    rc, out = psql(bind(sup_sql, dict(pull_request_id=pr_id, keep_head_sha=NEW_HEAD)))
    rc2, states = psql(
        "SELECT head_sha, status FROM review_runs WHERE pull_request_id=%d ORDER BY head_sha" % pr_id,
        tuples_only=True)
    lines = dict(line.split("|") for line in states.splitlines() if "|" in line)
    check("the older commit's run is superseded", lines.get(HEAD) == "superseded", states)
    check("the current commit's run is untouched", lines.get(NEW_HEAD) == "running", states)

    # -- finish ------------------------------------------------------------
    finish_sql = next(v for k, v in sql.items() if k.startswith("finish#"))
    rc, out = psql(bind(finish_sql, dict(id=RUN2, status="partial_success",
                                         error_code="publish_failed",
                                         error_message="GitHub rejected two comments",
                                         github_review_id=9001, quality_score=72.50)))
    rc2, row = psql(
        "SELECT status, error_code, duration_ms IS NOT NULL, quality_score "
        "FROM review_runs WHERE id='%s'" % RUN2, tuples_only=True)
    check("finish records a terminal status and a server-computed duration",
          rc == 0 and row.strip() == "partial_success|publish_failed|t|72.50", row)

    # -- cascade -----------------------------------------------------------
    psql("DELETE FROM review_runs WHERE id='%s'" % RUN)
    rc, out = psql("SELECT count(*) FROM findings WHERE review_run_id='%s'" % RUN, tuples_only=True)
    check("deleting a run cascades to its findings", out.strip() == "0", out)
    rc, out = psql("SELECT count(*) FROM review_comments", tuples_only=True)
    check("and to its comments", out.strip() == "0", out)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
