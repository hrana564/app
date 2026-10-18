"""
Rule execution context.

A rule receives one of these and must return a dict matching app.schemas.RuleResult.
The context owns the DuckDB connection, exposes every registered datasource as a
view, and gives the rule parameters plus a logging channel.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import re

import duckdb

# ctx.table("pos_sales") written inside a SQL string must be resolved to the
# DuckDB view name before the parser sees it. Only simple identifiers are
# matched, so the transform can never corrupt surrounding SQL.
_TABLE_REF = re.compile(r"""\bctx\.table\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\)""")


class Result:
    """Thin wrapper over a DuckDB result set with dict-friendly access."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        row = self.one()
        if not row:
            return None
        return next(iter(row.values()), None)

    def count(self) -> int:
        return len(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)


class RuleContext:
    """Everything a rule is allowed to touch.

    `ctx.table("x")` may be written either inside a SQL string or called from
    Python. Inside a string it is rewritten to the quoted view name before the
    query reaches DuckDB (see `_TABLE_REF`), which is what makes the documented
    authoring style work:

        ctx.sql("SELECT * FROM ctx.table('pos_sales')")
    """

    def __init__(
        self,
        *,
        con: duckdb.DuckDBPyConnection,
        data_date: date,
        query_mode: str = "SNAPSHOT",
        rule_name: str = "",
        registered: list[str] | None = None,
    ):
        self._con = con
        self.date = data_date
        self.query_mode = query_mode
        self.rule_name = rule_name
        self._registered = registered or []
        self._logs: list[str] = []

    # -- data access ----------------------------------------------------
    def table(self, name: str) -> str:
        """Return a quoted view name for use in FROM / JOIN.

        Every registered datasource is already a view, so this is safe to call
        from any query and composes with joins across sources.
        """
        return f'"{name}"'

    def sql(self, query: str, params: dict[str, Any] | None = None) -> Result:
        """Run read-only SQL. DuckDB is local and in-process; no network egress."""
        query = self._rewrite_table_refs(query)
        cursor = (
            self._con.execute(query, params) if params else self._con.execute(query)
        )
        columns = [d[0] for d in (cursor.description or [])]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return Result(rows)

    def _rewrite_table_refs(self, query: str) -> str:
        """Turn ctx.table("name") into "name" so DuckDB can resolve the view."""
        referenced = _TABLE_REF.findall(query)
        if not referenced:
            return query
        known = set(self._registered)
        for name in referenced:
            if name not in known:
                raise KeyError(
                    f"Rule references unknown datasource '{name}'. "
                    f"Registered: {', '.join(sorted(known)) or '(none)'}"
                )
        return _TABLE_REF.sub(lambda m: f'"{m.group(1)}"', query)

    def count_rows(self, name: str) -> int:
        """Rows available in a view - used to record rows_scanned on the run."""
        value = self.sql(f"SELECT count(*) AS n FROM {self.table(name)}").scalar()
        return int(value or 0)

    # -- diagnostics ----------------------------------------------------
    def log(self, message: str) -> None:
        self._logs.append(str(message))

    @property
    def logs(self) -> list[str]:
        return list(self._logs)


def build_views(
    con: duckdb.DuckDBPyConnection,
    *,
    sourcedata_dir: Path,
    entity: str,
    data_date: date,
    query_mode: str = "SNAPSHOT",
    window_days: int = 1,
    registry: dict[str, Any] | None = None,
) -> dict[str, int]:
    """
    Register every datasource as a DuckDB view scoped to the run.

    SNAPSHOT -> the single day folder for that entity
    WINDOW   -> the last `window_days` day folders

    Returns {datasource_name: row_count}.
    """
    if registry is None:
        from app.engine.datasources import registry_for

        registry = registry_for(entity)

    base = sourcedata_dir / entity.upper()
    iso = data_date.isoformat()

    if query_mode == "WINDOW":
        first = date.fromordinal(data_date.toordinal() - max(0, window_days - 1))
        day_folders = (
            sorted(p.name for p in base.iterdir() if p.is_dir() and _in_range(p.name, first, iso))
            if base.exists()
            else [iso]
        )
    else:
        day_folders = [iso]

    counts: dict[str, int] = {}
    for name, source in registry.items():
        files = [
            str((base / folder / f"{name}.csv").resolve())
            for folder in day_folders
            if (base / folder / f"{name}.csv").exists()
        ]

        if not files:
            # Keep the view defined so a rule fails with "table has no rows"
            # rather than an opaque parser error.
            con.execute(f'CREATE OR REPLACE VIEW "{name}" AS SELECT * WHERE 1=0')
            counts[name] = 0
            continue

        file_list = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
        casts = [
            f'CAST("{col}" AS {dtype}) AS "{col}"'
            for col, dtype in source.columns.items()
        ]

        # Two layers: the inner query casts the raw CSV strings to their declared
        # types, the outer query adds derived columns on top of those casts.
        # Derived expressions cannot live in the same SELECT as the casts -
        # DuckDB would bind them to the untyped VARCHAR column instead.
        derived = [
            f'{expr} AS "{col}"' for col, expr in (source.derived or {}).items()
        ]
        outer = ", ".join([f'"{col}"' for col in source.columns] + derived)

        con.execute(
            f'CREATE OR REPLACE VIEW "{name}" AS '
            f"SELECT {outer} FROM ("
            f'SELECT {", ".join(casts)} FROM read_csv([{file_list}], '
            f"union_by_name=true, header=true, all_varchar=true)"
            f") AS _typed"
        )
        counts[name] = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]

    return counts


def _in_range(folder_name: str, first: date, last: str) -> bool:
    try:
        d = date.fromisoformat(folder_name)
    except ValueError:
        return False
    return first <= d <= date.fromisoformat(last)