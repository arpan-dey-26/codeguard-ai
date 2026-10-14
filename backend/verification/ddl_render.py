"""Render real DDL from the Alembic migration files.

Alembic and SQLAlchemy cannot be installed in this environment (the package
index returns 403). Rather than hand-copying the schema into a .sql file --
which would verify a transcription, not the migration -- this shim implements
just enough of `sqlalchemy` and `alembic.op` to execute the migration modules
and emit the SQL they describe. The migration source is the input; the DDL is
derived from it.

Not a general-purpose Alembic: it supports exactly the constructs these
migrations use, and raises on anything else rather than silently emitting
wrong SQL.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

OUT: list[str] = []


# ---------------------------------------------------------------- sqlalchemy
class _Type:
    def __init__(self, sql):
        self.sql = sql

    def __str__(self):
        return self.sql


def BigInteger():
    return _Type("BIGINT")


def Integer():
    return _Type("INTEGER")


def SmallInteger():
    return _Type("SMALLINT")


def Text():
    return _Type("TEXT")


def Boolean():
    return _Type("BOOLEAN")


def String(length=None):
    return _Type(f"VARCHAR({length})" if length else "VARCHAR")


def DateTime(timezone=False):
    return _Type("TIMESTAMPTZ" if timezone else "TIMESTAMP")


def Numeric(precision=None, scale=None):
    return _Type(f"NUMERIC({precision},{scale})")


class _Text:
    def __init__(self, s):
        self.s = s

    def __str__(self):
        return self.s


def text(s):
    return _Text(s)


def false():
    return _Text("false")


class _Func:
    def now(self):
        return _Text("now()")


func = _Func()


class Column:
    def __init__(
        self,
        name,
        type_,
        nullable=True,
        server_default=None,
        autoincrement=False,
        primary_key=False,
    ):
        self.name = name
        self.type_ = type_
        self.nullable = nullable
        self.server_default = server_default
        self.autoincrement = autoincrement
        self.primary_key = primary_key

    def ddl(self):
        parts = [f'"{self.name}"', str(self.type_)]
        if self.autoincrement and str(self.type_) == "BIGINT":
            parts[1] = "BIGINT GENERATED ALWAYS AS IDENTITY"
        if self.server_default is not None:
            d = self.server_default
            if isinstance(d, _Text):
                parts.append(f"DEFAULT {d.s}")
            else:
                parts.append(f"DEFAULT '{d}'")
        if not self.nullable:
            parts.append("NOT NULL")
        return " ".join(parts)


class PrimaryKeyConstraint:
    def __init__(self, *cols, name=None):
        self.cols, self.name = cols, name

    def ddl(self):
        cols = ", ".join(f'"{c}"' for c in self.cols)
        return f'CONSTRAINT "{self.name}" PRIMARY KEY ({cols})'


class UniqueConstraint:
    def __init__(self, *cols, name=None):
        self.cols, self.name = cols, name

    def ddl(self):
        cols = ", ".join(f'"{c}"' for c in self.cols)
        return f'CONSTRAINT "{self.name}" UNIQUE ({cols})'


class CheckConstraint:
    def __init__(self, sqltext, name=None):
        self.sqltext, self.name = sqltext, name

    def ddl(self):
        return f'CONSTRAINT "{self.name}" CHECK ({self.sqltext})'


class ForeignKeyConstraint:
    def __init__(self, cols, refs, name=None, ondelete=None):
        self.cols, self.refs, self.name, self.ondelete = cols, refs, name, ondelete

    def ddl(self):
        cols = ", ".join(f'"{c}"' for c in self.cols)
        table = self.refs[0].split(".")[0]
        refcols = ", ".join(f'"{r.split(".")[1]}"' for r in self.refs)
        s = f'CONSTRAINT "{self.name}" FOREIGN KEY ({cols}) REFERENCES "{table}" ({refcols})'
        if self.ondelete:
            s += f" ON DELETE {self.ondelete}"
        return s


sa = types.ModuleType("sqlalchemy")
for _n in (
    "BigInteger",
    "Integer",
    "SmallInteger",
    "Text",
    "Boolean",
    "String",
    "DateTime",
    "Numeric",
    "text",
    "false",
    "func",
    "Column",
    "PrimaryKeyConstraint",
    "UniqueConstraint",
    "CheckConstraint",
    "ForeignKeyConstraint",
):
    setattr(sa, _n, globals()[_n])

_pg = types.ModuleType("sqlalchemy.dialects.postgresql")
_pg.UUID = lambda **kw: _Type("UUID")
_pg.JSONB = lambda **kw: _Type("JSONB")
_dialects = types.ModuleType("sqlalchemy.dialects")
_dialects.postgresql = _pg
sa.dialects = _dialects
sys.modules["sqlalchemy"] = sa
sys.modules["sqlalchemy.dialects"] = _dialects
sys.modules["sqlalchemy.dialects.postgresql"] = _pg


# --------------------------------------------------------------- alembic.op
class _Conv(str):
    """A name Alembic must treat as final.

    Stands in for ``sqlalchemy.sql.elements.conv``, which is what ``op.f()``
    returns. The distinction is the whole point: a plain ``str`` gets the
    metadata naming convention applied to it, a ``conv`` does not.

    **This class exists because its absence hid a real bug.** ``f()`` used to
    be ``return name``, so ``op.f("x")`` and ``"x"`` were the same object to
    this harness, and ``drop_constraint`` below used whatever string it was
    handed. Migration 0005 dropped a check constraint by its already-rendered
    name without ``op.f()``, real Alembic rendered it a second time into
    ``ck_webhook_events_ck_webhook_events_processing_status_valid``, and
    ``alembic upgrade head`` failed on every fresh database — while this
    harness reported the full upgrade/downgrade cycle green. A stub that models
    what the author meant instead of what the library does is not verification.
    """

    __slots__ = ()


# The ``ck`` entry of ``app.db.base.NAMING_CONVENTION``. Only this one
# interpolates ``%(constraint_name)s``, which is why check constraints are the
# only kind that can be double-prefixed this way — and why the plain-string
# ``drop_constraint`` of a foreign key in 0003 is harmless.
_CK_CONVENTION = "ck_{table}_{name}"


def _render_ck(name, table):
    """Apply the check-constraint convention, unless the name is already final."""
    if isinstance(name, _Conv):
        return str(name)
    return _CK_CONVENTION.format(table=table, name=name)


class _Op:
    def f(self, name):
        return _Conv(name)

    def create_table(self, name, *args):
        body = [a.ddl() for a in args]
        OUT.append(f'CREATE TABLE "{name}" (\n  ' + ",\n  ".join(body) + "\n);")

    def create_index(self, name, table, cols, unique=False, postgresql_where=None):
        u = "UNIQUE " if unique else ""
        # A column may be a plain name or a sa.text() expression (e.g.
        # "created_at DESC"); the latter must not be quoted as an identifier.
        c = ", ".join(x.s if isinstance(x, _Text) else f'"{x}"' for x in cols)
        w = f" WHERE {postgresql_where.s}" if postgresql_where is not None else ""
        OUT.append(f'CREATE {u}INDEX "{name}" ON "{table}" ({c}){w};')

    def add_column(self, table, column):
        OUT.append(f'ALTER TABLE "{table}" ADD COLUMN {column.ddl()};')

    def create_foreign_key(self, name, table, ref, cols, refcols, ondelete=None):
        c = ", ".join(f'"{x}"' for x in cols)
        r = ", ".join(f'"{x}"' for x in refcols)
        s = (
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ({c}) REFERENCES "{ref}" ({r})'
        )
        if ondelete:
            s += f" ON DELETE {ondelete}"
        OUT.append(s + ";")

    def drop_table(self, name):
        OUT.append(f'DROP TABLE "{name}";')

    def drop_index(self, name, table_name=None):
        OUT.append(f'DROP INDEX "{name}";')

    def drop_column(self, table, col):
        OUT.append(f'ALTER TABLE "{table}" DROP COLUMN "{col}";')

    def drop_constraint(self, name, table, type_=None):
        # A *check* constraint name goes through the convention unless it is
        # `conv`-marked, exactly as Alembic does. Other constraint types do not:
        # their convention templates need the column and referred-table names,
        # which a bare drop cannot supply, so Alembic passes the string through.
        rendered = _render_ck(name, table) if type_ == "check" else str(name)
        OUT.append(f'ALTER TABLE "{table}" DROP CONSTRAINT "{rendered}";')

    def create_check_constraint(self, name, table, condition, **kw):
        # The same rule, in the other direction. Previously this special-cased
        # any name starting with `ck_` and left it alone — which is *not* what
        # Alembic does, and which masked the 0005 defect by making the create
        # and the drop agree on a name the database never had.
        OUT.append(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{_render_ck(name, table)}" CHECK ({condition});'
        )

    def execute(self, statement):
        OUT.append(str(statement).rstrip(";") + ";")


op = _Op()
alembic = types.ModuleType("alembic")
alembic.op = op
sys.modules["alembic"] = alembic


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    versions = Path(sys.argv[1])
    direction = sys.argv[2] if len(sys.argv) > 2 else "upgrade"
    files = sorted(versions.glob("*.py"))
    if direction == "downgrade":
        files = list(reversed(files))
    for f in files:
        module = load(f)
        OUT.append(f"-- {f.name} :: {direction}")
        getattr(module, direction)()
    print("\n".join(OUT))
