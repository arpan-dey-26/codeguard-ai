"""Scope, secret exposure and SQL safety.

The dashboard reads repository data. These tests are about the two ways that
goes wrong: serving it to the wrong caller, and letting a caller's text reach
the database.
"""

from __future__ import annotations

import ast
import pathlib
import re
from datetime import UTC, datetime

import pytest
from app.api.filters import FindingFilters, RepositoryFilters, TimeRange
from app.api.scope import (
    DashboardAccessNotConfiguredError,
    ViewerScope,
    resolve_scope,
)
from app.core.config import Settings
from app.core.errors import AuthenticationRequiredError
from app.db.repositories import dashboard as queries
from pydantic import ValidationError

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"
RANGE = TimeRange(start=datetime(2026, 9, 1, tzinfo=UTC), end=datetime(2026, 9, 17, tzinfo=UTC))


# -- the deployment fails closed ---------------------------------------------
#
# Rewritten in Phase 5. Phase 4 had no way to sign in, so development served
# the dashboard unrestricted and everything else refused. Now that
# authentication exists, "no session" is a 401 wherever sign-in is possible,
# and unrestricted access is reachable only by an operator setting a flag whose
# name says what it does.


def _prod(**overrides):
    """A production Settings that satisfies the Phase 5 start-up validator.

    Written out in full rather than hidden in a fixture, because the list *is*
    the requirement: every field here is one a production deployment refuses to
    start without.
    """
    base = {
        "app_env": "production",
        "debug": False,
        "database_url": "postgresql+asyncpg://u:p@db.internal:5432/codeguard",
        "session_secret": "x" * 48,
        "trusted_hosts": "api.example.com",
        "public_base_url": "https://api.example.com",
        "frontend_url": "https://app.example.com",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.parametrize("env", ["development", "test"])
def test_no_session_and_no_sign_in_is_a_503_not_an_open_door(env):
    """A deployment where nobody *can* sign in still does not serve openly."""
    with pytest.raises(DashboardAccessNotConfiguredError):
        resolve_scope(Settings(app_env=env))


def test_no_session_where_sign_in_exists_is_a_401():
    """The caller can authenticate and has not. That is 401, not 503 and not a
    scope — and it is what tells the SPA to show the sign-in screen rather than
    an outage page."""
    settings = Settings(
        app_env="development",
        github_oauth_client_id="Iv1.abc",
        github_oauth_client_secret="s" * 40,
        session_secret="x" * 48,
    )
    with pytest.raises(AuthenticationRequiredError):
        resolve_scope(settings)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_production_refuses_to_start_without_the_security_settings(env):
    """The strongest guarantee is the one that stops the process.

    Phase 4's validator checked ``debug`` and nothing else, so a production
    process happily inherited the development database URL, no session secret
    and a wildcard host list. It now refuses, and the message names every
    missing item at once rather than one per restart.
    """
    with pytest.raises(ValidationError) as caught:
        Settings(app_env=env, debug=False)
    message = str(caught.value)
    for expected in ("SESSION_SECRET", "TRUSTED_HOSTS", "PUBLIC_BASE_URL"):
        assert expected in message, f"{expected} not named in the refusal"


def test_production_refuses_the_development_database_url():
    with pytest.raises(ValidationError) as caught:
        _prod(database_url="postgresql+asyncpg://codeguard:codeguard@localhost:5432/codeguard")
    assert "development default" in str(caught.value)


def test_production_refuses_a_short_session_secret():
    with pytest.raises(ValidationError) as caught:
        _prod(session_secret="short")
    assert "at least" in str(caught.value)


def test_production_refuses_a_wildcard_trusted_host():
    with pytest.raises(ValidationError) as caught:
        _prod(trusted_hosts="*")
    assert "TRUSTED_HOSTS" in str(caught.value)


def test_production_refuses_plain_http_urls():
    with pytest.raises(ValidationError) as caught:
        _prod(public_base_url="http://api.example.com")
    assert "https" in str(caught.value)


def test_production_refuses_the_unauthenticated_reads_flag():
    """Phase 4 allowed this as the only way to serve the dashboard at all.
    Phase 5 ships authentication, so there is no longer a reason for it and
    leaving it set is a configuration error rather than a choice."""
    with pytest.raises(ValidationError) as caught:
        _prod(dashboard_allow_unauthenticated_reads=True)
    assert "DASHBOARD_ALLOW_UNAUTHENTICATED_READS" in str(caught.value)


def test_a_valid_production_configuration_starts():
    """The negative tests above are only meaningful if the positive one passes;
    otherwise they could all be passing for the wrong reason."""
    settings = _prod()
    assert settings.cookie_secure is True
    assert settings.trusted_host_list == ["api.example.com"]


def test_opening_it_takes_a_deliberate_flag_in_development():
    settings = Settings(app_env="development", dashboard_allow_unauthenticated_reads=True)
    assert resolve_scope(settings).is_unrestricted


def test_the_refusal_is_503_not_403():
    """Nothing is wrong with the request; the deployment is incomplete. A 403
    would imply the caller lacks a permission that nobody has yet."""
    assert DashboardAccessNotConfiguredError.status_code == 503


def test_the_open_state_is_visible_in_the_safe_summary():
    """If somebody opens the dashboard, the log line at start-up says so.

    Production now refuses the flag outright, so this is checked in
    development — where it is still reachable and still worth surfacing.
    """
    summary = Settings(
        app_env="development", dashboard_allow_unauthenticated_reads=True
    ).safe_summary()
    assert summary["dashboard_unauthenticated_reads_enabled"] is True
    assert summary["dashboard_authenticated"] is False


def test_the_safe_summary_reports_authentication_honestly():
    """Phase 4 hard-coded ``dashboard_authenticated: False``. It now reflects
    reality, which is the difference between a status field and a decoration."""
    summary = Settings(app_env="development").safe_summary()
    assert summary["dashboard_authenticated"] is True


# -- scope semantics ---------------------------------------------------------


def test_unrestricted_and_sees_nothing_are_different_states():
    """An empty tuple means 'this caller can see nothing' — a real outcome for
    a user whose App access was revoked — and must not behave like 'no
    restriction'."""
    assert ViewerScope.unrestricted().is_unrestricted
    assert not ViewerScope.unrestricted().sees_nothing
    assert ViewerScope.for_installations(()).sees_nothing
    assert not ViewerScope.for_installations(()).is_unrestricted


def test_installation_ids_are_deduplicated_and_ordered():
    scope = ViewerScope.for_installations((3, 1, 3, 2))
    assert scope.installation_ids == (1, 2, 3)


def test_a_blind_scope_produces_a_false_predicate_not_an_absent_one():
    query = queries.findings_page(
        scope=ViewerScope.for_installations(()), filters=FindingFilters(), cursor={}, limit=10
    )
    assert "FALSE" in query.sql


def test_a_restricted_scope_binds_the_installation_list_as_a_parameter():
    query = queries.findings_page(
        scope=ViewerScope.for_installations((7, 9)),
        filters=FindingFilters(),
        cursor={},
        limit=10,
    )
    assert ":scope_installations" in query.sql
    assert query.params["scope_installations"] == [7, 9]
    assert "7" not in query.sql, "ids must be bound, never interpolated"


def test_every_query_builder_applies_the_scope():
    """The invariant that makes Phase 5's authentication a one-place change:
    a query that ignores its scope would serve out-of-scope rows forever."""
    scoped = ViewerScope.for_installations((1,))
    builders = [
        lambda: queries.overview_summary(scope=scoped, time_range=RANGE),
        lambda: queries.findings_by_severity(scope=scoped, time_range=RANGE),
        lambda: queries.findings_by_category(scope=scoped, time_range=RANGE),
        lambda: queries.rejection_reasons(scope=scoped, time_range=RANGE),
        lambda: queries.anchor_distribution(scope=scoped, time_range=RANGE),
        lambda: queries.review_trends(scope=scoped, time_range=RANGE),
        lambda: queries.repositories_page(
            scope=scoped, filters=RepositoryFilters(), cursor={}, limit=5
        ),
        lambda: queries.repository_detail(scope=scoped, repository_id=1),
        lambda: queries.findings_page(scope=scoped, filters=FindingFilters(), cursor={}, limit=5),
        lambda: queries.findings_count(scope=scoped, filters=FindingFilters()),
        lambda: queries.processing_health(scope=scoped, time_range=RANGE),
        lambda: queries.coverage_summary(scope=scoped, time_range=RANGE),
        lambda: queries.recent_activity(scope=scoped),
        lambda: queries.integration_status(scope=scoped),
    ]
    for build in builders:
        query = build()
        assert "scope_installations" in query.params, query.sql[:120]


# -- nothing user-supplied becomes SQL ---------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "'; DROP TABLE findings; --",
        "%' OR '1'='1",
        "\\'; DELETE FROM repositories; --",
        "a\x00b",
    ],
)
def test_a_hostile_search_term_is_bound_and_never_appears_in_the_sql(hostile):
    query = queries.findings_page(
        scope=ViewerScope.unrestricted(),
        filters=FindingFilters(search=hostile),
        cursor={},
        limit=10,
    )
    assert "DROP" not in query.sql.upper().replace("DROPPED", "")
    assert "DELETE" not in query.sql.upper()
    assert hostile not in query.sql
    assert query.params["search"].startswith("%")


def test_the_query_text_contains_no_formatted_filter_values():
    """Structure is chosen from constants; values are always parameters."""
    query = queries.findings_page(
        scope=ViewerScope.unrestricted(),
        filters=FindingFilters(
            repository_id=999333,
            severities=("critical",),
            categories=("security",),
            validation="rejected",
            publication="published",
        ),
        cursor={},
        limit=10,
    )
    assert "999333" not in query.sql
    assert query.params["repository_id"] == 999333
    assert query.params["severities"] == ["critical"]


def test_no_query_builder_uses_string_formatting_on_a_parameter():
    """An f-string in this module is how a bound parameter quietly becomes an
    interpolated one. The fragments that *are* interpolated are checked below
    to be constants defined in the module itself."""
    source = (APP_DIR / "db" / "repositories" / "dashboard.py").read_text()
    tree = ast.parse(source)

    module_constants = {
        node.targets[0].id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    # Locals that hold SQL *fragments* composed from constants in this module,
    # plus the two keyword-only arguments used to pick a table alias and a
    # column name. Those two are checked separately below: every call site must
    # pass a literal, so no caller text can reach them.
    allowed_locals = {
        "scope_sql",
        "repo_sql",
        "range_sql",
        "clauses",
        "width",
        "bucket",
        "visible_repos",
        "sql",
        "alias",
        "column",
    }

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            expr = value.value
            if isinstance(expr, ast.Name):
                if expr.id in module_constants or expr.id in allowed_locals:
                    continue
                offenders.append(f"line {node.lineno}: {expr.id}")
            elif isinstance(expr, ast.Call):
                # `_and(...)` / `_scope_clause(...)` compose constant fragments.
                func = expr.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name.startswith("_") or name in {"escape_like"}:
                    continue
                offenders.append(f"line {node.lineno}: call {name}")
    assert offenders == [], offenders


def test_the_alias_and_column_arguments_are_only_ever_given_literals():
    """``_scope_clause(alias=...)`` and ``_range_clause(column=...)`` are the
    only two values interpolated into SQL text. This asserts that every call
    site passes a constant written in this file, so neither can carry caller
    input."""
    tree = ast.parse((APP_DIR / "db" / "repositories" / "dashboard.py").read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in ("alias", "column") and not isinstance(keyword.value, ast.Constant):
                offenders.append(f"line {node.lineno}: {keyword.arg}")
    assert offenders == [], offenders


# -- secrets never reach a response ------------------------------------------


_SECRET_COLUMNS = (
    "private_key",
    "api_key",
    "webhook_secret",
    "access_token",
    "installation_token",
    "client_secret",
)


def test_no_dashboard_query_selects_a_secret_shaped_column():
    source = (APP_DIR / "db" / "repositories" / "dashboard.py").read_text().lower()
    for column in _SECRET_COLUMNS:
        assert column not in source, column


def test_no_dashboard_schema_declares_a_secret_shaped_field():
    """A *boolean about* a secret is fine and necessary — the settings page has
    to say whether the webhook secret is configured. The secret itself is not.
    So the check is on the field's suffix, not on the substring: a name ending
    in ``_configured`` is a statement, one ending in ``_secret`` is a value."""
    import ast as _ast

    tree = _ast.parse((APP_DIR / "schemas" / "dashboard.py").read_text())
    offenders = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
            name = node.target.id.lower()
            if name.endswith(("_configured", "_enabled")):
                continue
            if any(name.endswith(c) or name == c for c in _SECRET_COLUMNS):
                offenders.append(name)
            # `key` on its own is not credential-shaped — `CountBucket.key`
            # is a chart category label. The pattern targets the names a
            # credential actually has.
            if re.search(r"(^|_)(secret|token|password)$|_key$|^api_key$", name):
                offenders.append(name)
    assert offenders == [], offenders


def test_the_settings_response_exposes_booleans_about_secrets_not_secrets():
    from app.schemas.dashboard import SettingsResponse

    fields = set(SettingsResponse.model_fields)
    assert "github_app_configured" in fields
    assert "ai_provider_configured" in fields
    for name in fields:
        assert not re.search(r"(key|secret|token|password)$", name), name
    # And the values it does carry are literals or booleans, never a SecretStr
    # that could be rendered by an accidental str().
    from pydantic import SecretStr

    for name, field in SettingsResponse.model_fields.items():
        assert field.annotation is not SecretStr, name


def test_the_settings_endpoint_has_no_write_route():
    """Editing credentials from a browser needs secure secret management,
    which this build does not have."""
    source = (APP_DIR / "api" / "v1" / "dashboard" / "system.py").read_text()
    assert "@router.patch" not in source
    assert "@router.post" not in source
    assert "@router.put" not in source


def test_the_whole_dashboard_api_is_read_only():
    package = APP_DIR / "api" / "v1" / "dashboard"
    for path in package.glob("*.py"):
        source = path.read_text()
        for verb in ("@router.post", "@router.patch", "@router.put", "@router.delete"):
            assert verb not in source, f"{path.name} declares {verb}"


def test_a_finding_response_cannot_carry_a_file_path_the_caller_chose():
    """The finding endpoints take an id, never a path. There is no parameter a
    caller could point at an arbitrary file."""
    source = (APP_DIR / "api" / "v1" / "dashboard" / "findings.py").read_text()
    tree = ast.parse(source)
    params: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params.update(a.arg for a in node.args.args)
            params.update(a.arg for a in node.args.kwonlyargs)
    assert "file_path" not in params
    assert "path" not in params


def test_source_context_is_limited_to_the_one_stored_anchor_line():
    """Phase 3 does not persist repository source, so the only source text the
    API can return is the anchor snippet already stored on the finding."""
    from app.schemas.dashboard import anchor_context

    context = anchor_context(anchor_snippet="handle = open(path)", resolved_line=14)
    assert len(context) == 1
    assert context[0].line == 14
    assert context[0].text == "handle = open(path)"
    assert context[0].is_anchor is True


def test_source_context_is_empty_when_there_is_nothing_verified_to_show():
    from app.schemas.dashboard import anchor_context

    assert anchor_context(anchor_snippet="", resolved_line=14) == []
    assert anchor_context(anchor_snippet="x = 1", resolved_line=None) == []
    assert anchor_context(anchor_snippet=None, resolved_line=None) == []


def test_source_context_is_numbered_with_the_resolved_line_not_the_claimed_one():
    """Labelling our verified text with the model's unverified coordinate would
    be the one place the UI could show a wrong line number confidently."""
    from app.schemas.dashboard import anchor_context

    assert anchor_context(anchor_snippet="x = 1", resolved_line=42)[0].line == 42
