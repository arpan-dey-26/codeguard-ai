"""The backend schema and the frontend types must describe the same API.

Phase 0 planned to generate the TypeScript types from the OpenAPI schema in
Phase 4. That did not happen and the reason is recorded rather than hidden: the
generator toolchain cannot be installed here (the npm registry returns 403). So
``frontend/src/types/api.ts`` is hand-written — and a hand-written mirror drifts
the first time someone adds a field on one side only.

This test is the substitute for generation. It parses the TypeScript
interfaces and compares them, field by field, against the Pydantic models. It
is weaker than generation (it checks names, not types) and stronger than
nothing, which is what a hand-written mirror otherwise has.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from app.schemas import dashboard as schemas

FRONTEND_TYPES = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src" / "types" / "api.ts"
)

# backend model -> TypeScript interface
PAIRS = [
    ("FunnelCounts", "FunnelCounts"),
    ("RunCounts", "RunCounts"),
    ("AnchorCounts", "AnchorCounts"),
    ("OverviewResponse", "OverviewResponse"),
    ("TrendPoint", "TrendPoint"),
    ("TrendResponse", "TrendResponse"),
    ("ProcessingHealthResponse", "ProcessingHealthResponse"),
    ("CoverageResponse", "CoverageResponse"),
    ("RecentActivityEntry", "RecentActivityEntry"),
    ("RepositorySummary", "RepositorySummary"),
    ("RepositoryDetailResponse", "RepositoryDetailResponse"),
    ("PullRequestSummary", "PullRequestSummary"),
    ("ReviewRunSummary", "ReviewRunSummary"),
    ("PullRequestDetailResponse", "PullRequestDetailResponse"),
    ("ChangedFileSummary", "ChangedFileSummary"),
    ("ReviewRunDetailResponse", "ReviewRunDetailResponse"),
    ("FindingSummary", "FindingSummary"),
    ("FindingEventEntry", "FindingEventEntry"),
    ("SourceContextLine", "SourceContextLine"),
    ("FindingDetailResponse", "FindingDetailResponse"),
    ("InstallationSummary", "InstallationSummary"),
    ("IntegrationStatusResponse", "IntegrationStatusResponse"),
    ("SettingsResponse", "SettingsResponse"),
    ("CountBucket", "CountBucket"),
]


def _typescript_interfaces() -> dict[str, set[str]]:
    """Field names per exported interface.

    A small parser rather than a real one: these interfaces are flat
    ``name: type`` lines, and a dependency-free reader keeps this test able to
    run in an environment where nothing can be installed.
    """
    source = FRONTEND_TYPES.read_text()
    interfaces: dict[str, set[str]] = {}
    for match in re.finditer(r"export interface (\w+)(?:<[^>]+>)?\s*\{(.*?)\n\}", source, re.S):
        name, body = match.group(1), match.group(2)
        fields: set[str] = set()
        # Strip block comments so a field name mentioned in prose is not
        # mistaken for a declaration.
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            field = re.match(r"(\w+)\??\s*:", stripped)
            if field:
                fields.add(field.group(1))
        interfaces[name] = fields
    return interfaces


@pytest.mark.parametrize("model_name,interface_name", PAIRS)
def test_the_frontend_interface_matches_the_backend_schema(model_name, interface_name):
    interfaces = _typescript_interfaces()
    assert interface_name in interfaces, f"{interface_name} is missing from api.ts"

    model = getattr(schemas, model_name)
    backend_fields = set(model.model_fields)
    frontend_fields = interfaces[interface_name]

    missing_in_frontend = backend_fields - frontend_fields
    extra_in_frontend = frontend_fields - backend_fields

    assert not missing_in_frontend, (
        f"{interface_name} is missing fields the API returns: {sorted(missing_in_frontend)}"
    )
    assert not extra_in_frontend, (
        f"{interface_name} declares fields the API does not return: {sorted(extra_in_frontend)}"
    )


def test_every_dashboard_response_model_has_a_frontend_type():
    """A new response model with no TypeScript counterpart means a page would
    have to reach for `any` to render it."""
    from pydantic import BaseModel

    paired = {model for model, _ in PAIRS}
    unpaired = []
    for name in dir(schemas):
        value = getattr(schemas, name)
        generic_origin = getattr(value, "__pydantic_generic_metadata__", {}).get("origin")
        if (
            isinstance(value, type)
            and issubclass(value, BaseModel)
            and value is not BaseModel
            and value.__module__ == schemas.__name__
            and name not in paired
            and name != "Page"  # generic; expressed as Page<T> in TypeScript
            and generic_origin is not schemas.Page
        ):
            unpaired.append(name)
    assert unpaired == [], f"No frontend type for: {unpaired}"


def _strip_comments(source: str) -> str:
    """Remove comments before scanning for a code pattern.

    Phase 5 fix. The scan below matches ``: any``, and a sentence in a doc
    comment — "Note what is absent: any token, any secret" — matched it. A test
    that fails on prose teaches contributors to reword comments to appease it,
    which is worse than no test: the next person quietly weakens the *comment*
    instead of noticing the *code* is fine. Strip the comments, keep the rule
    strict on code.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//.*$", "", source)


def test_the_frontend_declares_no_any():
    """`any` on a response type moves a contract break from compile time to a
    blank panel in front of a user."""
    source = _strip_comments(FRONTEND_TYPES.read_text())
    assert not re.search(r":\s*any\b", source)
    assert not re.search(r"\bas any\b", source)


def test_the_frontend_types_carry_the_wording_rules():
    """The type file is where a future contributor looks first. The two
    distinctions that are easiest to get wrong are documented there."""
    source = FRONTEND_TYPES.read_text().lower()
    assert 'not "false positives"' in source or "not 'false positives'" in source
    assert "never inferred" in source


def test_no_frontend_source_file_uses_any():
    """The rule applies to the whole application, not only the type file."""
    frontend_src = FRONTEND_TYPES.parents[1]
    offenders = []
    for path in frontend_src.rglob("*.ts*"):
        if path.name.endswith(".d.ts"):
            continue
        text = _strip_comments(path.read_text())
        if re.search(r":\s*any\b", text) or re.search(r"\bas any\b", text):
            offenders.append(str(path.relative_to(frontend_src)))
    assert offenders == [], offenders
