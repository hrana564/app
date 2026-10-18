"""
Rule execution context.

A rule receives one of these and returns a dict matching app.schemas.RuleResult.

WHAT ctx GIVES YOU
------------------
    ctx.table("ipos_sales")   a polars DataFrame built from the files this run
                              staged for that table - nothing else is reachable
    ctx.date                  the anchor date being audited
    ctx.lookback_days         how far back this rule reads (1 = that day)
    ctx.log("...")            appends a note to the run summary

That is the whole surface. Three methods and two attributes.

WHY DATAFRAMES, NOT SQL
-----------------------
An earlier version exposed `ctx.sql()` and needed a query engine behind it. The
rules did not want SQL - they wanted to compute a number and hand back the rows
that failed it:

    hourly = base.group_by("hour").agg(pl.col("invoice_value").sum())
    baseline = hourly.filter(pl.col("hour") < LAST_HOUR)["value"].mean()

reads as the audit step it performs. The SQL equivalent needed
`sum(...) FILTER (WHERE hour < 23) / nullif(count(DISTINCT hour) FILTER (...), 0)`
and could not be explained to an auditor without a paragraph.

WHAT ctx DOES NOT GIVE YOU
--------------------------
No file paths, no query strings, no engine handle. A rule cannot read the mount
directly, so the run folder is genuinely a record of what was read rather than a
copy made afterwards.

    import polars as pl   # the only third-party import a rule needs
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl


class RuleContext:
    """Everything a rule is allowed to touch."""

    def __init__(
        self,
        *,
        data_date: date,
        lookback_days: int = 1,
        rule_name: str = "",
        registered: list[str] | None = None,
        snapshot: Any = None,
    ):
        self.date = data_date
        # How far back from `date` this rule reads: 1 = that day, 5 = T-5 to T.
        self.lookback_days = lookback_days
        self.rule_name = rule_name
        self._registered = registered or []
        self._logs: list[str] = []
        self._snapshot = snapshot
        self._cache: dict[str, pl.DataFrame] = {}

    # -- data access ----------------------------------------------------
    def table(self, name: str) -> pl.DataFrame:
        """The rows this run staged for `name`.

        Reads happen once per table per run; the frame is cached so a rule that
        uses the same data twice pays for one read. Column types are inferred
        from the CSV, so `invoice_value` arrives as a float and `is_void` as a
        bool rather than as text.
        """
        if name in self._cache:
            return self._cache[name]

        if self._registered and name not in self._registered:
            raise KeyError(
                f"Unknown datasource '{name}'. "
                f"Registered: {', '.join(sorted(self._registered)) or '(none)'}"
            )

        paths = self._paths_for(name)
        if not paths:
            available = self._available() or "(none)"
            raise LookupError(
                f"No files were staged for '{name}'. This run staged: "
                f"{', '.join(sorted(available))}. A rule must only read tables "
                f"it declared - check DATASOURCES and lookback_days."
            )

        frame = (
            pl.read_csv(paths[0]) if len(paths) == 1
            else pl.read_csv(paths, infer_schema_length=None)
        )
        self._cache[name] = frame
        return frame

    def _paths_for(self, name: str) -> list[str]:
        if self._snapshot is None:
            return []
        return [str(p) for p in self._snapshot.paths_for(name)]

    def _available(self) -> list[str]:
        if self._snapshot is None:
            return []
        return list(self._snapshot.tables.keys())

    def count_rows(self, name: str) -> int:
        """Rows available in a table."""
        return self.table(name).height

    # -- diagnostics ----------------------------------------------------
    def log(self, message: str) -> None:
        self._logs.append(str(message))

    @property
    def logs(self) -> list[str]:
        return list(self._logs)