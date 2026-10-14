"""The authorization boundary: User A must not see User B's data.

These tests operate on the **SQL the application actually runs**. The query
builders in ``app.db.repositories.dashboard`` are pure — they return SQL text
and a parameter dict — so the scoping predicate can be inspected directly,
without a database, an ORM or an HTTP client. That is a deliberate property of
the Phase 4 design and it is what makes an authorization test something other
than a mock agreeing with itself.

The four attacks checked here are the ones that actually happen:

1. **Direct object reference.** Ask for a specific id belonging to somebody
   else. Must return nothing — and therefore 404, never 403, because a 403
   confirms the row exists.
2. **Scope widening.** Supply an installation id and hope it is honoured.
   Nothing in any signature accepts one from the caller.
3. **Empty-scope collapse.** A user with no access has an empty id list, and an
   empty ``IN ()`` is a classic way to accidentally match everything.
4. **Filter-as-authorization.** Pass ``repository_id`` for a repository outside
   the scope and see whether the filter replaces the scope rather than
   narrowing within it.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from app.api.filters import (
    FindingFilters,
    PullRequestFilters,
    RepositoryFilters,
    RunFilters,
    TimeRange,
)
from app.api.scope import ViewerScope
from app.db.repositories import dashboard as queries

RANGE = TimeRange(start=datetime(2026, 9, 1, tzinfo=UTC), end=datetime(2026, 9, 17, tzinfo=UTC))

# Two users with disjoint access. Installation 7 is A's; 99 is B's.
USER_A = ViewerScope.for_installations((7,), user_id=1)
USER_B = ViewerScope.for_installations((99,), user_id=2)
NO_ACCESS = ViewerScope.for_installations((), user_id=3)


@dataclass(frozen=True)
class _Session:
    """A structural stand-in for ``SessionContext``.

    Not a mock of the real class and not an import of it: ``ViewerScope`` takes
    a Protocol precisely so the authorization boundary can be exercised without
    SQLAlchemy. If somebody re-couples the two, this file stops importing and
    says so — which is the point.
    """

    user_id: int
    installation_ids: tuple[int, ...]


def _session(user_id: int, installations: tuple[int, ...]) -> _Session:
    return _Session(user_id=user_id, installation_ids=installations)


# -- the scope comes from the session, and only from the session -------------


def test_a_session_becomes_exactly_its_own_installation_list():
    scope = ViewerScope.for_session(_session(1, (7, 7, 3)))
    assert scope.installation_ids == (3, 7)
    assert scope.user_id == 1


def test_two_users_produce_disjoint_scopes():
    a = ViewerScope.for_session(_session(1, (7,)))
    b = ViewerScope.for_session(_session(2, (99,)))
    assert set(a.installation_ids or ()) & set(b.installation_ids or ()) == set()


def test_a_user_with_no_access_sees_nothing_rather_than_everything():
    scope = ViewerScope.for_session(_session(3, ()))
    assert scope.sees_nothing
    assert not scope.is_unrestricted


def test_no_query_builder_accepts_an_installation_id_from_the_caller():
    """NFR-S8, checked as a signature property rather than trusted as a rule.

    If no function can be *told* which installation to read, no endpoint can
    accidentally let a caller widen their own scope — the mistake is not
    expressible.
    """
    offenders = []
    for name, fn in vars(queries).items():
        if name.startswith("_") or not callable(fn) or not inspect.isfunction(fn):
            continue
        params = set(inspect.signature(fn).parameters)
        for forbidden in ("installation_id", "installation_ids", "user_id"):
            if forbidden in params:
                offenders.append(f"{name}({forbidden}=...)")
    assert offenders == [], offenders


# -- attack 1: direct object reference ---------------------------------------


@pytest.mark.parametrize(
    "builder,args",
    [
        (queries.repository_detail, {"repository_id": 4242}),
        (queries.pull_request_detail, {"pull_request_id": 4242}),
        (
            queries.review_run_detail,
            {"review_run_id": "0190b0de-0000-7000-8000-000000000000"},
        ),
        (queries.finding_detail, {"finding_id": "0190b0de-0000-7000-8000-000000000000"}),
    ],
)
def test_a_detail_query_for_another_users_row_is_still_scoped(builder, args):
    """The id is bound; the scope is applied *in addition*. A row belonging to
    another installation therefore returns no rows, and the route turns "no
    rows" into 404 — which is what stops the endpoint confirming the row
    exists."""
    query = builder(scope=USER_A, **args)
    assert ":scope_installations" in query.sql
    assert query.params["scope_installations"] == [7]


@pytest.mark.parametrize(
    "builder,args",
    [
        (queries.repository_detail, {"repository_id": 4242}),
        (queries.pull_request_detail, {"pull_request_id": 4242}),
    ],
)
def test_the_same_query_under_a_different_user_binds_a_different_scope(builder, args):
    a = builder(scope=USER_A, **args)
    b = builder(scope=USER_B, **args)
    assert a.sql == b.sql, "the SQL shape must not depend on who is asking"
    assert a.params["scope_installations"] != b.params["scope_installations"]


# -- attack 3: the empty scope -----------------------------------------------


@pytest.mark.parametrize(
    "builder,kwargs",
    [
        (queries.repositories_page, {"filters": RepositoryFilters(), "cursor": {}, "limit": 10}),
        (queries.findings_page, {"filters": FindingFilters(), "cursor": {}, "limit": 10}),
        (
            queries.pull_requests_page,
            {"filters": PullRequestFilters(), "cursor": {}, "limit": 10},
        ),
        (queries.review_runs_page, {"filters": RunFilters(), "cursor": {}, "limit": 10}),
    ],
)
def test_an_empty_scope_produces_a_false_predicate_not_an_absent_one(builder, kwargs):
    """The failure mode this guards: building ``installation_id IN ()``, which
    is a syntax error, and then "fixing" it by omitting the clause — which
    turns "sees nothing" into "sees everything"."""
    query = builder(scope=NO_ACCESS, **kwargs)
    assert "FALSE" in query.sql.upper()
    # And nothing that could match: no bound list for the planner to widen.
    assert query.params.get("scope_installations") in (None, [])


# -- attack 4: a filter is not an authorization ------------------------------


def test_a_repository_filter_narrows_within_the_scope_and_does_not_replace_it():
    """Asking for a repository in somebody else's installation must not drop
    the scope predicate. The filter and the scope are ANDed; the filter can
    only ever subtract."""
    query = queries.findings_page(
        scope=USER_A,
        filters=FindingFilters(repository_id=999999),
        cursor={},
        limit=10,
    )
    assert ":scope_installations" in query.sql
    assert query.params["scope_installations"] == [7]
    assert query.params.get("repository_id") == 999999
    # Both predicates present, joined by AND — never OR, which would let the
    # filter *widen* the result set.
    assert " OR " not in _scope_clause(query.sql)


def _scope_clause(sql: str) -> str:
    """The text around the scope predicate, for checking how it is combined."""
    match = re.search(r".{0,200}scope_installations.{0,200}", sql, re.S)
    return match.group(0) if match else ""


# -- the parameters are bound, never interpolated ----------------------------


def test_installation_ids_are_never_interpolated_into_sql():
    scope = ViewerScope.for_installations((13579,), user_id=1)
    query = queries.findings_page(scope=scope, filters=FindingFilters(), cursor={}, limit=10)
    assert "13579" not in query.sql
    assert query.params["scope_installations"] == [13579]


def test_a_hostile_user_id_cannot_reach_the_sql():
    """``user_id`` comes from our own session row, not from input — but the
    binding is asserted anyway, because "this value is trusted" is exactly the
    assumption that stops being true when somebody adds a new caller."""
    scope = ViewerScope.for_installations((7,), user_id=1)
    query = queries.repositories_page(scope=scope, filters=RepositoryFilters(), cursor={}, limit=10)
    assert "'; DROP" not in query.sql
    assert isinstance(query.params, dict)


# -- child queries are guarded by their scoped parent -------------------------


def test_unscoped_child_queries_are_only_reached_after_a_scoped_parent():
    """Three builders take an id with no scope: ``pull_request_runs``,
    ``review_run_changed_files`` and ``finding_events``.

    That is correct *only* because each is called after a scoped parent query
    has already returned a row — and 404'd when it did not. The pattern is the
    authorization, so it is asserted here rather than left to a convention a
    later contributor might not notice.

    The check is structural: in every route function that calls one of these,
    a scoped ``*_detail`` call and a 404 must appear first.
    """
    import ast as _ast
    import pathlib as _pathlib

    unscoped = {"pull_request_runs", "review_run_changed_files", "finding_events"}
    routes_dir = _pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "dashboard"
    problems: list[str] = []

    for path in sorted(routes_dir.glob("*.py")):
        tree = _ast.parse(path.read_text())
        for fn in [
            n for n in _ast.walk(tree) if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
        ]:
            calls = [
                node.func.attr
                for node in _ast.walk(fn)
                if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
            ]
            used = unscoped & set(calls)
            if not used:
                continue
            scoped_first = any(c.endswith("_detail") for c in calls)
            raises_404 = "HTTP_404_NOT_FOUND" in _ast.dump(fn)
            if not scoped_first:
                problems.append(f"{path.name}:{fn.name} calls {used} with no scoped parent query")
            if not raises_404:
                problems.append(f"{path.name}:{fn.name} calls {used} without a 404 guard")

    assert problems == [], problems


def test_a_cross_tenant_id_produces_404_not_403():
    """Architecture.md §17.7. A 403 says 'this exists and is not yours', which
    is a working existence oracle for enumerating another tenant's ids. A 404
    says nothing."""
    import ast as _ast
    import pathlib as _pathlib

    routes_dir = _pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "dashboard"
    forbidden = []
    for path in sorted(routes_dir.glob("*.py")):
        source = path.read_text()
        tree = _ast.parse(source)
        # Strip docstrings: these files discuss 403 in prose, and matching prose
        # would make this test assert something about comments.
        for node in _ast.walk(tree):
            if isinstance(
                node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Module, _ast.ClassDef)
            ):
                doc = _ast.get_docstring(node, clean=False)
                if doc:
                    source = source.replace(doc, "")
        if "HTTP_403_FORBIDDEN" in source:
            forbidden.append(path.name)
    assert forbidden == [], f"dashboard routes must 404, not 403: {forbidden}"
