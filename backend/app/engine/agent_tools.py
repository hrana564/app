from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
        spec = TOOLS.get(name)
        if spec is None:
            return {"error": f"unknown tool '{name}'"}
        model, _ = spec
        method = getattr(self, name, None)
        if method is None:
            return {"error": f"unknown tool '{name}'"}
        try:
            parsed = model.model_validate(arguments or {})
        except ValidationError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        try:
            return method(**parsed.model_dump())
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
        frame = self._frame(table).head(max(1, min(int(n), MAX_SAMPLE_ROWS)))
        return {"rows": _records(frame)}

    def distinct_values(self, table: str, column: str, limit: int = 50) -> dict[str, Any]:
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
        return {
            "problems": scan_source(source),
            "declared": declared_names(source),
            "referenced": referenced_tables(source),
        }

    def run_rule(self, source: str) -> dict[str, Any]:
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
# Each tool's arguments are a model. The schema the model is shown is generated
# from that model and the arguments it sends are validated against it, so the
# schema and the contract cannot drift apart: there is only one declaration of
# what a tool takes.
TableName = Annotated[str, Field(description="a table name from the selection")]
ColumnName = Annotated[str, Field(description="a column name in that table")]
SourceText = Annotated[str, Field(description="the full module source")]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListTablesArgs(_Args):
    pass


class DescribeTableArgs(_Args):
    table: TableName


class SampleRowsArgs(_Args):
    table: TableName
    n: int = Field(default=5, description="how many rows")


class DistinctValuesArgs(_Args):
    table: TableName
    column: ColumnName
    limit: int = 50


class ColumnStatsArgs(_Args):
    table: TableName
    column: ColumnName


class JoinOverlapArgs(_Args):
    table_a: TableName
    key_a: ColumnName
    table_b: TableName
    key_b: ColumnName


class Condition(BaseModel):
    column: ColumnName
    op: Literal["==", "!=", ">", "<", ">=", "<=", "in"]
    value: Any = Field(description="the value, or a list for 'in'")


class FilterCountArgs(_Args):
    table: TableName
    conditions: list[Condition]


class ValidateRuleArgs(_Args):
    source: SourceText


class RunRuleArgs(_Args):
    source: SourceText


# The name the model calls, the arguments it takes, and what the tool is for.
TOOLS: dict[str, tuple[type[_Args], str]] = {
    "list_tables": (
        ListTablesArgs,
        "The tables you may read, with what each holds.",
    ),
    "describe_table": (
        DescribeTableArgs,
        "A table's columns and their types. Look before you write a column name.",
    ),
    "sample_rows": (
        SampleRowsArgs,
        "A few real rows from a table, to see what the values look like.",
    ),
    "distinct_values": (
        DistinctValuesArgs,
        "Every value a column holds, with counts. Use this before you compare a "
        "column to a literal - a value that is not in the list matches nothing.",
    ),
    "column_stats": (
        ColumnStatsArgs,
        "Count, nulls, distinct, min, max and mean for a column. Use it to pick "
        "a threshold and to learn a date or timestamp format.",
    ),
    "join_overlap": (
        JoinOverlapArgs,
        "How many keys two tables actually share. Use it before you join, so the "
        "join does not silently match nothing.",
    ),
    "filter_count": (
        FilterCountArgs,
        "How many rows a set of conditions selects, and a sample. Use it to check "
        "that your logic selects what you expect.",
    ),
    "validate_rule": (
        ValidateRuleArgs,
        "Check a rule against the static gate without running it. Cheap.",
    ),
    "run_rule": (
        RunRuleArgs,
        "Run a candidate rule against real data and see what it found. This is "
        "how you test. Do not submit until this reports status 'ok'.",
    ),
}


def _parameters(model: type[_Args]) -> dict[str, Any]:
    defs = model.model_json_schema().get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                merged = walk(defs.get(ref.rsplit("/", 1)[-1], {}))
                for key, value in node.items():
                    if key != "$ref":
                        merged[key] = walk(value)
                return merged
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    schema = walk(model.model_json_schema())
    schema.pop("$defs", None)
    schema.pop("title", None)
    return schema


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _parameters(model),
        },
    }
    for name, (model, description) in TOOLS.items()
]
