"""
Rule execution context.

A rule receives one of these and must return a dict matching
app.schemas.RuleResult. The context gives the rule its parameters, a logging
channel, and a query surface.

QUERY ENGINE
------------
DuckDB was removed. The replacement is not yet decided, so `ctx.sql()` is the
seam: it raises rather than silently returning wrong data. Wire it to whatever
engine you choose - the four methods below are the entire contract a rule sees,
so nothing in a rule file needs to change when you do.

    ctx.table("ipos_sales")   quoted table name for FROM / JOIN
    ctx.sql("SELECT ...")     Result (.all() / .one() / .scalar() / .count())
    ctx.date                  the date being audited
    ctx.log("...")            appends a note to the run summary

`ctx.table("x")` may be written inside a SQL string or called from Python.
Inside a string it is rewritten to the quoted name before the query reaches
the engine (see `_TABLE_REF`), which is what makes this authoring style work:

    ctx.sql("SELECT * FROM ctx.table('ipos_sales')")
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# Matches ctx.table("name") or ctx.table('name') inside a SQL string. Only
# simple identifiers are matched, so the transform can never corrupt the
# surrounding SQL.
_TABLE_REF = re.compile(r"""\bctx\.table\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\)""")


class Result:
    """Thin wrapper over a result set with dict-friendly access."""

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
    """Everything a rule is allowed to touch."""

    def __init__(
        self,
        *,
        data_date: date,
        query_mode: str = "SNAPSHOT",
        rule_name: str = "",
        registered: list[str] | None = None,
    ):
        self.date = data_date
        self.query_mode = query_mode
        self.rule_name = rule_name
        self._registered = registered or []
        self._logs: list[str] = []

    # -- data access ----------------------------------------------------
    def table(self, name: str) -> str:
        """Return a quoted table name for use in FROM / JOIN."""
        return f'"{name}"'

    def sql(self, query: str, params: dict[str, Any] | None = None) -> Result:
        """Run read-only SQL.

        Not wired: DuckDB was removed and the replacement engine is still open.
        Failing loudly beats returning wrong data to a scheduled control.
        """
        raise NotImplementedError(
            "ctx.sql() has no query engine. DuckDB was removed; "
            "the replacement is not yet decided."
        )

    def _rewrite_table_refs(self, query: str) -> str:
        """Turn ctx.table("name") into "name" so the engine can resolve it.

        Also the guard against typos: an unregistered name fails here rather
        than as an opaque parser error later.
        """
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
        """Rows available in a table - recorded as rows_scanned on the run."""
        value = self.sql(f"SELECT count(*) AS n FROM {self.table(name)}").scalar()
        return int(value or 0)

    # -- diagnostics ----------------------------------------------------
    def log(self, message: str) -> None:
        self._logs.append(str(message))

    @property
    def logs(self) -> list[str]:
        return list(self._logs)