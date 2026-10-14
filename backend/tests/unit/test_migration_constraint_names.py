"""Every constraint a migration drops must be one a migration created.

**The defect this exists to catch.** ``alembic upgrade head`` died on every
fresh database at revision 0005:

    asyncpg.exceptions.UndefinedObjectError: constraint
    "ck_webhook_events_ck_webhook_events_processing_status_valid"
    of relation "webhook_events" does not exist

The doubled prefix is the whole story. ``app.db.base.NAMING_CONVENTION`` renders
a check constraint as ``ck_%(table_name)s_%(constraint_name)s``, and Alembic
applies that convention to any plain string it is handed. Migration 0002 created
the constraint with ``name=op.f("ck_webhook_events_processing_status_valid")`` —
``op.f()`` wraps a name in ``conv()``, meaning *already final, do not render
again* — so the real PostgreSQL name is exactly that. Migration 0005 then
dropped it by the same literal string but **without** ``op.f()``, so Alembic
rendered it a second time.

Nothing caught it. The migration-cycle harness in ``verification/`` stubbed
``op.f()`` as ``lambda name: name``, which erased the only distinction that
matters, and its ``drop_constraint`` used the string verbatim. It reported the
full upgrade/downgrade cycle green against a real PostgreSQL while the real
migration could not run at all. Both have been corrected; this file is the cheap
check that needs no database, no Alembic and no SQLAlchemy, so it runs
everywhere the rest of the suite does.

The rule, in one line: **create by logical name, drop by final name.**
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

VERSIONS = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"

# ``app.db.base.NAMING_CONVENTION``, restated rather than imported: importing it
# pulls in SQLAlchemy, which is not installable in every environment this suite
# runs in, and a copy of five format strings is cheaper than skipping the test.
# `test_the_restated_convention_matches_the_real_one` keeps the copy honest.
CK_TEMPLATE = "ck_{table_name}_{constraint_name}"

# Only the ``ck`` template interpolates ``%(constraint_name)s``. ``fk``, ``ix``,
# ``uq`` and ``pk`` are built from column and table names that a bare
# ``drop_constraint`` cannot supply, so Alembic passes those strings through
# unchanged — which is why the plain-string foreign-key drop in 0003 is correct
# as written and is not flagged below.
CONVENTION_APPLIES_TO = {"check"}


def _migrations() -> list[pathlib.Path]:
    files = sorted(p for p in VERSIONS.glob("*.py") if not p.name.startswith("__"))
    assert files, f"no migrations found in {VERSIONS}"
    return files


def _is_op_f(node: ast.expr) -> bool:
    """Is this expression ``op.f(...)``?"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "f"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    )


def _literal(node: ast.expr) -> str | None:
    """The string this expression evaluates to, unwrapping one ``op.f()``."""
    if _is_op_f(node) and node.args:  # type: ignore[attr-defined]
        return _literal(node.args[0])  # type: ignore[attr-defined]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return None  # an f-string; not used for constraint names here
    return None


def _calls(path: pathlib.Path, attr: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _rendered_drop_name(call: ast.Call) -> tuple[str, str] | None:
    """What name ``op.drop_constraint(...)`` will actually send to PostgreSQL.

    Returns ``(rendered_name, table)``, or ``None`` when the call is not a
    literal this check can resolve.
    """
    if not call.args:
        return None
    name_node = call.args[0]
    table_node = call.args[1] if len(call.args) > 1 else _kwarg(call, "table_name")
    type_node = _kwarg(call, "type_")

    name = _literal(name_node)
    table = _literal(table_node) if table_node is not None else None
    type_ = _literal(type_node) if type_node is not None else None
    if name is None or table is None:
        return None

    already_final = _is_op_f(name_node)
    if type_ in CONVENTION_APPLIES_TO and not already_final:
        return CK_TEMPLATE.format(table_name=table, constraint_name=name), table
    return name, table


def _created_constraint_names() -> set[str]:
    """Every constraint name any migration creates, as PostgreSQL will see it."""
    created: set[str] = set()
    for path in _migrations():
        tree = ast.parse(path.read_text())

        # Inline constraints in `create_table`: sa.CheckConstraint(..., name=...),
        # PrimaryKeyConstraint, UniqueConstraint, ForeignKeyConstraint. All of
        # these pass `op.f(...)` in this project, so the name is final.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name_kw = _kwarg(node, "name")
                if name_kw is not None:
                    literal = _literal(name_kw)
                    if literal:
                        created.add(literal)

        # `op.create_check_constraint(logical_name, table, condition)` — the
        # convention renders it, exactly as it does on the drop side.
        for call in _calls(path, "create_check_constraint"):
            if len(call.args) >= 2:
                name = _literal(call.args[0])
                table = _literal(call.args[1])
                if name and table:
                    created.add(
                        name
                        if _is_op_f(call.args[0])
                        else CK_TEMPLATE.format(table_name=table, constraint_name=name)
                    )

        # Constraints added to an existing table. The first positional argument
        # is the constraint name in each case. Their convention templates carry
        # no `%(constraint_name)s`, so the name is used as given — which is why
        # the plain-string calls here are correct and the check-constraint ones
        # were not.
        for op_name in (
            "create_foreign_key",
            "create_unique_constraint",
            "create_primary_key",
        ):
            for call in _calls(path, op_name):
                if call.args:
                    literal = _literal(call.args[0])
                    if literal:
                        created.add(literal)

    return created


# -- the regression ----------------------------------------------------------


def test_no_migration_drops_a_constraint_nobody_created():
    """The check that would have caught the 0005 failure before it shipped."""
    created = _created_constraint_names()
    problems: list[str] = []

    for path in _migrations():
        for call in _calls(path, "drop_constraint"):
            resolved = _rendered_drop_name(call)
            if resolved is None:
                continue
            rendered, _table = resolved
            if rendered not in created:
                problems.append(f"{path.name}:{call.lineno} drops '{rendered}'")

    assert problems == [], (
        "these migrations drop a constraint name that no migration creates — "
        "`alembic upgrade head` will fail on a fresh database with "
        f"UndefinedObjectError: {problems}"
    )


def test_a_check_constraint_dropped_without_op_f_is_double_prefixed():
    """Proves the check above is not vacuous, by modelling the exact defect.

    If this ever stops producing the doubled name, the model has drifted from
    Alembic and `test_no_migration_drops_a_constraint_nobody_created` is no
    longer checking what it claims to.
    """
    broken = (
        ast.parse(
            'op.drop_constraint("ck_webhook_events_processing_status_valid",'
            ' "webhook_events", type_="check")'
        )
        .body[0]
        .value
    )
    assert isinstance(broken, ast.Call)
    rendered, table = _rendered_drop_name(broken)  # type: ignore[misc]
    assert table == "webhook_events"
    assert rendered == "ck_webhook_events_ck_webhook_events_processing_status_valid"

    fixed = (
        ast.parse(
            'op.drop_constraint(op.f("ck_webhook_events_processing_status_valid"),'
            ' "webhook_events", type_="check")'
        )
        .body[0]
        .value
    )
    assert isinstance(fixed, ast.Call)
    rendered_fixed, _ = _rendered_drop_name(fixed)  # type: ignore[misc]
    assert rendered_fixed == "ck_webhook_events_processing_status_valid"


def test_the_webhook_events_status_constraint_round_trips():
    """The specific constraint that failed, in both directions.

    0002 creates it, 0005 drops and widens it, and 0005's downgrade drops and
    narrows it again — all under one name. A mismatch in any of the four is the
    bug returning.
    """
    source = "\n".join(path.read_text() for path in _migrations())
    final_name = "ck_webhook_events_processing_status_valid"

    # Created by 0002 with op.f(), so the name is final and unrendered.
    assert f'op.f("{final_name}")' in source

    # Every drop of it is op.f()-wrapped.
    for path in _migrations():
        for call in _calls(path, "drop_constraint"):
            resolved = _rendered_drop_name(call)
            if resolved and resolved[1] == "webhook_events":
                assert resolved[0] == final_name, (
                    f"{path.name}:{call.lineno} drops '{resolved[0]}', but 0002 "
                    f"created '{final_name}'"
                )

    # Every recreate uses the logical name, so the convention renders it back to
    # the same final name.
    recreated = [
        call
        for path in _migrations()
        for call in _calls(path, "create_check_constraint")
        if len(call.args) >= 2 and _literal(call.args[1]) == "webhook_events"
    ]
    assert recreated, "nothing recreates the webhook_events status constraint"
    for call in recreated:
        assert _literal(call.args[0]) == "processing_status_valid", (
            "recreate must pass the *logical* name and let the convention render "
            "it; passing the rendered name would double the prefix"
        )

    # The widened vocabulary 0005 exists to introduce, and the narrow one its
    # downgrade restores.
    assert "'coalesced'" in source


@pytest.mark.parametrize(
    ("key", "template"),
    [
        ("ck", "ck_%(table_name)s_%(constraint_name)s"),
        ("fk", "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"),
        ("ix", "ix_%(table_name)s_%(column_0_N_name)s"),
        ("uq", "uq_%(table_name)s_%(column_0_N_name)s"),
        ("pk", "pk_%(table_name)s"),
    ],
)
def test_the_restated_convention_matches_the_real_one(key, template):
    """`CK_TEMPLATE` above is a copy. This is what stops it going stale.

    Read from the source rather than imported: `app.db.base` imports SQLAlchemy,
    which is not installable in every environment this suite runs in.
    """
    base = (pathlib.Path(__file__).resolve().parents[2] / "app" / "db" / "base.py").read_text()
    match = re.search(rf'"{key}":\s*"([^"]+)"', base)
    assert match is not None, f"no '{key}' entry in NAMING_CONVENTION"
    assert match.group(1) == template, (
        f"NAMING_CONVENTION['{key}'] changed to {match.group(1)!r}; the copy in "
        "this file, and the drop-name logic above, must be updated with it"
    )
