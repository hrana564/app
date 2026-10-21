"""
The tools the rule agent uses to look at the data and test its rule.

The model is a bad database and a good investigator, so it is not handed a
snapshot of the data in its prompt. It is handed tools that read the real bytes
the rule will run against, and it asks. Everything it learns, it learns by
looking.

     list_tables       what it may read at all
     describe_table    a table's columns, types, grain
     sample_rows       a few real rows
     distinct_values   the values a column holds, with counts
     column_stats      count, nulls, distinct, min, max, mean
     join_overlap      whether two tables actually share a key
     filter_count      how many rows a set of conditions selects
     validate_rule     the static gate, no data
     run_rule          run a candidate against real data

`run_rule` is also the universal query tool: the model can write a throwaway
rule that returns whatever diagnostic it wants and run it through the same
sandbox the real rule runs in. That is what makes "check the data" and "test
the rule" one mechanism.

THE SELECTION BINDS
-------------------
Every tool reads only the tables the admin selected. A tool that answered about
any table would reopen the hole the selection exists to close.

THE ANSWERS ARE DATA, NOT OPINION
---------------------------------
Each tool reads the estate and returns it. That is the point: a model asked
"does this value exist?" will answer; `distinct_values` cannot. The final
verdict is still `run_rule`, never the model's word.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from app.engine.catalog import Catalog
from app.engine.snapshot import days_for
from app.engine.trial import run_trial
from app.engine.validation import declared_names, referenced_tables, scan_source
from app.settings import settings

log = logging.getLogger("abm.tools")

# A value list is capped so one identifier column cannot fill the context.
MAX_VALUES = 200
MAX_SAMPLE_ROWS = 50
MAX_ROWS_BACK = 10


# ---------------------------------------------------------------------------
# JSON-safe rendering - a polars value is not always serialisable
# ---------------------------------------------------------------------------
def _text(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):
        return value
    return value


def _records(frame: pl.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    rows = frame.head(limit) if limit is not None else frame
    return [{key: _text(value) for key, value in row.items()} for row in rows.to_dicts()]


def _require_column(frame: pl.DataFrame, column: str) -> None:
    if column not in frame.columns:
        raise ValueError(
            f"'{column}' is not a column of this table. "
            f"Columns: {', '.join(frame.columns)}"
        )


def _condition(frame: pl.DataFrame, column: str, op: str, value: Any) -> pl.Expr:
    """One condition as a polars expression. Structured, never evaluated."""
    col = pl.col(column)
    is_string = frame[column].dtype == pl.String

    if op in ("==", "=", "!=", "in"):
        if op == "in":
            values = value if isinstance(value, list) else [value]
            if is_string:
                return col.is_in([str(v) for v in values])
            try:
                return col.is_in([float(v) for v in values])
            except (TypeError, ValueError):
                return col.cast(pl.String).is_in([str(v) for v in values])
        if is_string:
            return (col == str(value)) if op in ("==", "=") else (col != str(value))
        try:
            number = float(value)
        except (TypeError, ValueError):
            return (col.cast(pl.String) == str(value)) if op in ("==", "=") \
                else (col.cast(pl.String) != str(value))
        return (col == number) if op in ("==", "=") else (col != number)

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"op '{op}' needs a numeric value, got {value!r}")

    if op == ">":
        return col > number
    if op == "<":
        return col < number
    if op == ">=":
        return col >= number
    if op == "<=":
        return col <= number
    raise ValueError(f"unknown op '{op}'. Use one of ==, !=, >, <, >=, <=, in")


# ---------------------------------------------------------------------------
# The toolbox
# ---------------------------------------------------------------------------
class Toolbox:
    """Every tool, bound to one entity, one date and one selection."""

    def __init__(
        self,
        *,
        entity: str,
        data_date: date,
        lookback_days: int,
        tables: list[str],
        catalog: Catalog,
        allowed: list[str] | None = None,
    ):
        self.entity = entity.upper()
        self.data_date = data_date
        self.lookback_days = lookback_days
        self.tables = list(tables)
        self.allowed = list(allowed) if allowed is not None else list(tables)
        self.catalog = catalog
        self._frames: dict[str, pl.DataFrame] = {}

    # -- dispatch --------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        """Run one tool by name. A bad call comes back as an error, not a raise."""
        method = getattr(self, name, None)
        if method is None or name.startswith("_"):
            return {"error": f"unknown tool '{name}'"}
        try:
            return method(**(arguments or {}))
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - the model reads the error
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- data access -----------------------------------------------------
    def _frame(self, table: str) -> pl.DataFrame:
        if table not in self.tables:
            raise ValueError(
                f"'{table}' is not in the selection. You may read only: "
                f"{', '.join(self.tables)}"
            )
        if table in self._frames:
            return self._frames[table]

        paths = [
            settings.sourcedata_dir / self.entity / day.isoformat() / f"{table}.csv"
            for day in days_for(self.data_date, self.lookback_days)
        ]
        landed = [str(path) for path in paths if Path(path).exists()]
        if not landed:
            raise FileNotFoundError(
                f"no data was delivered for '{table}' on {self.data_date}"
            )
        frame = (
            pl.read_csv(landed[0])
            if len(landed) == 1
            else pl.read_csv(landed, infer_schema_length=None)
        )
        self._frames[table] = frame
        return frame

    # -- the tools -------------------------------------------------------
    def list_tables(self) -> dict[str, Any]:
        """The tables the admin selected, and what each holds."""
        return {
            "tables": [
                {
                    "name": entry.name,
                    "system": entry.system,
                    "grain": entry.grain,
                    "description": entry.description,
                }
                for entry in self.catalog.entries_for(self.entity)
                if entry.name in self.tables
            ]
        }

    def describe_table(self, table: str) -> dict[str, Any]:
        """A table's columns, types, grain and row count."""
        frame = self._frame(table)
        entry = next(
            (e for e in self.catalog.entries_for(self.entity) if e.name == table), None
        )
        return {
            "name": table,
            "system": entry.system if entry else "",
            "grain": entry.grain if entry else "",
            "description": entry.description if entry else "",
            "rows": frame.height,
            "columns": {name: str(dtype) for name, dtype in frame.schema.items()},
        }

    def sample_rows(self, table: str, n: int = 5) -> dict[str, Any]:
        """A few real rows, so a field cannot be invented."""
        frame = self._frame(table).head(max(1, min(int(n), MAX_SAMPLE_ROWS)))
        return {"rows": _records(frame)}

    def distinct_values(self, table: str, column: str, limit: int = 50) -> dict[str, Any]:
        """Every value a column holds, with counts. Use before testing a value."""
        frame = self._frame(table)
        _require_column(frame, column)
        cap = max(1, min(int(limit), MAX_VALUES))
        counts = (
            frame.group_by(column)
            .len(name="count")
            .sort("count", descending=True)
            .head(cap)
        )
        values = [
            {"value": _text(row[column]), "count": int(row["count"])}
            for row in counts.to_dicts()
        ]
        return {
            "column": column,
            "distinct": int(frame[column].n_unique()),
            "values": values,
            "truncated": frame[column].n_unique() > len(values),
        }

    def column_stats(self, table: str, column: str) -> dict[str, Any]:
        """Count, nulls, distinct, min, max, mean - for thresholds and formats."""
        frame = self._frame(table)
        _require_column(frame, column)
        series = frame[column]
        stats: dict[str, Any] = {
            "column": column,
            "type": str(series.dtype),
            "count": series.len(),
            "nulls": int(series.null_count()),
            "distinct": int(series.n_unique()),
        }
        if series.dtype.is_numeric():
            stats["min"] = _text(series.min())
            stats["max"] = _text(series.max())
            stats["mean"] = _text(series.mean())
        else:
            present = series.drop_nulls()
            if present.len():
                stats["min"] = _text(present.min())
                stats["max"] = _text(present.max())
        return stats

    def join_overlap(
        self, table_a: str, key_a: str, table_b: str, key_b: str
    ) -> dict[str, Any]:
        """How many keys two tables share. A join on a key that does not match
        finds nothing and still reports a clean day."""
        left = self._frame(table_a)
        right = self._frame(table_b)
        _require_column(left, key_a)
        _require_column(right, key_b)
        left_keys = set(left[key_a].drop_nulls().cast(pl.String).to_list())
        right_keys = set(right[key_b].drop_nulls().cast(pl.String).to_list())
        matched = left_keys & right_keys
        return {
            "left_rows": left.height,
            "right_rows": right.height,
            "left_distinct_keys": len(left_keys),
            "right_distinct_keys": len(right_keys),
            "matched_keys": len(matched),
            "sample_matched": sorted(matched)[:5],
            "sample_unmatched_left": sorted(left_keys - right_keys)[:5],
        }

    def filter_count(
        self, table: str, conditions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """How many rows a set of conditions selects, and a sample of them."""
        frame = self._frame(table)
        expression: pl.Expr | None = None
        for condition in conditions or []:
            piece = _condition(
                frame,
                condition.get("column", ""),
                condition.get("op", "=="),
                condition.get("value"),
            )
            expression = piece if expression is None else (expression & piece)
        matched = frame.filter(expression) if expression is not None else frame
        return {
            "matched": matched.height,
            "total": frame.height,
            "sample": _records(matched, MAX_ROWS_BACK),
        }

    def validate_rule(self, source: str) -> dict[str, Any]:
        """The static gate. No data is read and nothing runs."""
        return {
            "problems": scan_source(source),
            "declared": declared_names(source),
            "referenced": referenced_tables(source),
        }

    def run_rule(self, source: str) -> dict[str, Any]:
        """Run a candidate against real data. This is the test, and the gate."""
        trial = run_trial(
            source,
            entity=self.entity,
            tables=self.tables,
            data_date=self.data_date,
            lookback_days=self.lookback_days,
            allowed=self.allowed,
        )
        result = trial.as_dict()
        rows = result.get("rows") or []
        result["rows"] = rows[:MAX_ROWS_BACK]
        result["row_count"] = len(rows)
        return result


# ---------------------------------------------------------------------------
# The schemas the model is given
# ---------------------------------------------------------------------------
def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_TABLE = {"type": "string", "description": "a table name from the selection"}
_COLUMN = {"type": "string", "description": "a column name in that table"}

TOOL_SCHEMAS: list[dict] = [
    _tool(
        "list_tables",
        "The tables you may read, with what each holds.",
        {}, [],
    ),
    _tool(
        "describe_table",
        "A table's columns and their types. Look before you write a column name.",
        {"table": _TABLE}, ["table"],
    ),
    _tool(
        "sample_rows",
        "A few real rows from a table, to see what the values look like.",
        {"table": _TABLE, "n": {"type": "integer", "description": "how many rows"}},
        ["table"],
    ),
    _tool(
        "distinct_values",
        "Every value a column holds, with counts. Use this before you compare a "
        "column to a literal - a value that is not in the list matches nothing.",
        {"table": _TABLE, "column": _COLUMN, "limit": {"type": "integer"}},
        ["table", "column"],
    ),
    _tool(
        "column_stats",
        "Count, nulls, distinct, min, max and mean for a column. Use it to pick "
        "a threshold and to learn a date or timestamp format.",
        {"table": _TABLE, "column": _COLUMN}, ["table", "column"],
    ),
    _tool(
        "join_overlap",
        "How many keys two tables actually share. Use it before you join, so the "
        "join does not silently match nothing.",
        {
            "table_a": _TABLE, "key_a": _COLUMN,
            "table_b": _TABLE, "key_b": _COLUMN,
        },
        ["table_a", "key_a", "table_b", "key_b"],
    ),
    _tool(
        "filter_count",
        "How many rows a set of conditions selects, and a sample. Use it to check "
        "that your logic selects what you expect.",
        {
            "table": _TABLE,
            "conditions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": _COLUMN,
                        "op": {
                            "type": "string",
                            "enum": ["==", "!=", ">", "<", ">=", "<=", "in"],
                        },
                        "value": {"description": "the value, or a list for 'in'"},
                    },
                    "required": ["column", "op", "value"],
                },
            },
        },
        ["table", "conditions"],
    ),
    _tool(
        "validate_rule",
        "Check a rule against the static gate without running it. Cheap.",
        {"source": {"type": "string", "description": "the full module source"}},
        ["source"],
    ),
    _tool(
        "run_rule",
        "Run a candidate rule against real data and see what it found. This is "
        "how you test. Do not submit until this reports status 'ok'.",
        {"source": {"type": "string", "description": "the full module source"}},
        ["source"],
    ),
]
