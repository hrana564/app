"""
Rule generation from a natural-language prompt.

The LLM is given the datasource registry plus a real column sample from the
target date, and must return a Python module matching the rule contract. The
output is always stored as a DRAFT - it is never auto-approved. A human reads
the diff and runs it before it goes live.

Provider-agnostic: works with any OpenAI-compatible endpoint (Azure AI Foundry,
Azure OpenAI, Ollama, minimax, ...) via LLM_BASE_URL.
"""

from __future__ import annotations

import csv
from datetime import date
from typing import Any

from app.engine.datasources import ENTITIES, SYSTEMS_BY_ENTITY, registry_for
from app.settings import settings

CONTRACT_INSTRUCTIONS = """\
A rule is a single Python file. It MUST define exactly one function:

    def run(ctx) -> dict:

`ctx` is provided by the platform and offers:
    ctx.table("name")  -> a polars DataFrame holding the rows this run staged
                          for that table. Types are inferred from the CSV.
    ctx.date           -> datetime.date being audited
    ctx.lookback_days  -> how far back this rule reads
    ctx.log("...")     -> appends a note to the run summary
    ctx.count_rows("name") -> number of rows in a table

There is no SQL and no query engine. You write polars operations.

    frame.filter(pl.col("invoice_value") > LIMIT)
    frame.group_by("store_id").agg(value=pl.col("invoice_value").sum())
    frame.join(other, left_on="a", right_on="b", how="inner")
    frame.sort("value", descending=True).head(200)
    frame.to_dicts()          # -> list[dict], which is what "rows" expects

The returned dict MUST validate against:
    {
      "status": "success" | "failed",
      "error_message": str | None,
      "summary": str,
      "metrics": {str: number|str},
      "rows": [ {...}, ... ]
    }

RULES:
  * Return "status": "success" with the offending rows when the control is BREACHED.
  * Return "status": "success" with empty rows when the data is clean.
  * Return "status": "failed" with error_message when the rule cannot run.
  * Only reference datasources you are shown. Every column you use must exist
    in the schema given to you - polars raises on a column that does not.
  * Declare every threshold as a module-level constant in UPPER_CASE with a
    comment saying what it measures, e.g. LAST_HOUR_RATIO_LIMIT = 1.5.
    Thresholds belong to the rule, not to a runtime configuration table.
  * Derive helper columns yourself with with_columns(...) - the hour of a
    timestamp, whether a tender is cash, whether a person has exited. Nothing
    is pre-computed for you.
  * A `.dt.hour()` needs the string parsed first:
        pl.col("invoice_ts").str.to_datetime("%Y-%m-%d %H:%M:%S").dt.hour()
  * An empty frame is a normal outcome. Describe it in the summary and return
    no rows. Do not raise.
  * Cast row values to str/float when embedding them in the summary.
  * Only import from: polars, json, datetime, statistics, math, decimal,
    difflib, collections, itertools, functools, re, typing
  * Do NOT write files, open sockets, or import os/subprocess/sys.
  * Emit ONLY the module source. No markdown fences, no commentary.
"""

EXAMPLE_RULE = """\
# The last trading hour may not exceed this multiple of the hourly average.
LAST_HOUR_RATIO_LIMIT = 1.5

LAST_HOUR = 23
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def run(ctx):
    limit = LAST_HOUR_RATIO_LIMIT

    sales = ctx.table("ipos_sales").with_columns(
        hour=pl.col("invoice_ts").str.to_datetime(TS_FORMAT, strict=False).dt.hour(),
    )

    if sales.height == 0:
        return {
            "status": "failed",
            "error_message": "No trading data for this date",
            "summary": "No trading data",
            "metrics": {},
            "rows": [],
        }

    hourly = sales.group_by("hour").agg(value=pl.col("invoice_value").sum())
    last_hour = float(hourly.filter(pl.col("hour") == LAST_HOUR)["value"].sum() or 0)
    hourly_avg = float(hourly.filter(pl.col("hour") < LAST_HOUR)["value"].mean() or 0)
    ratio = (last_hour / hourly_avg) if hourly_avg else 0.0

    if ratio < limit:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"Last-hour value {last_hour:.0f} is {ratio:.2f}x the "
                f"{hourly_avg:.0f} hourly average (limit {limit})"
            ),
            "metrics": {"ratio": round(ratio, 3), "hourly_avg": hourly_avg},
            "rows": [],
        }

    rows = (
        sales.filter(pl.col("hour") == LAST_HOUR)
        .sort("invoice_value", descending=True)
        .select(
            "invoice_id", "invoice_ts", "staff_id", "store_id",
            "invoice_value", "item_group",
        )
        .head(50)
        .to_dicts()
    )

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Last hour of trading = {last_hour:.0f} AED, {ratio:.2f}x the "
            f"{hourly_avg:.0f} hourly average (limit {limit}x), "
            f"across {len(rows)} invoices"
        ),
        "metrics": {
            "last_hour_value": last_hour,
            "hourly_avg": hourly_avg,
            "ratio": round(ratio, 3),
            "threshold": limit,
        },
        "rows": rows,
    }
"""


def is_configured() -> bool:
    return bool(settings.llm_base_url and settings.llm_model)


def _sample_csv(
    sourcedata_dir, entity: str, name: str, data_date: date, limit: int
) -> str:
    """Render a few real rows straight from the CSV as an aligned table."""
    path = sourcedata_dir / entity.upper() / data_date.isoformat() / f"{name}.csv"
    if not path.exists():
        return ""

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    if len(rows) <= 1:
        return ""

    header, data = rows[0], rows[1 : limit + 1]
    widths = [
        max(len(str(r[i])) if i < len(r) else 0 for r in [header, *data])
        for i in range(len(header))
    ]

    def line(cells: list[str]) -> str:
        padded = [
            (cells[i] if i < len(cells) else "").ljust(widths[i])
            for i in range(len(header))
        ]
        return "  " + "  ".join(padded).rstrip()

    return "\n".join([line(header), *(line(r) for r in data)])


def sample_datasources(entity: str, data_date: date, limit: int = 3) -> str:
    """Render declared columns plus real sample rows so the LLM cannot invent fields."""
    registry = registry_for(entity)
    base = settings.sourcedata_dir / entity.upper()
    if not base.exists():
        return "(SourceData not generated yet - run scripts/make_sample_data.py)"

    blocks: list[str] = []
    for name in sorted(registry):
        src = registry[name]
        cols = ", ".join(f"{c} {t}" for c, t in src.columns.items())
        block = (
            f"### {name}\n"
            f"system: {src.system}\n"
            f"grain: {src.grain}\n"
            f"columns:\n  {cols}"
        )
        try:
            sample = _sample_csv(
                settings.sourcedata_dir, entity, name, data_date, limit
            )
            block += f"\nsample rows:\n{sample}" if sample else "\n(no rows available)"
        except Exception as exc:  # noqa: BLE001
            block += f"\n(no rows available: {exc})"
        blocks.append(block)

    return "\n\n".join(blocks)


def build_prompt(natural_language: str, entity: str, data_date: date) -> str:
    return f"""
{natural_language.strip()}

---
CLIENT ENTITY: {ENTITIES.get(entity.upper(), entity)}
AVAILABLE SYSTEMS: {', '.join(SYSTEMS_BY_ENTITY.get(entity.upper(), []))}
AVAILABLE DATASOURCES (date {data_date.isoformat()})
{sample_datasources(entity, data_date)}

---
RULE CONTRACT
{CONTRACT_INSTRUCTIONS}

---
REFERENCE IMPLEMENTATION
{EXAMPLE_RULE}

---
Write the Python module for the rule described above.
""".strip()


def generate_rule_source(prompt: str, entity: str, data_date: date) -> dict[str, Any]:
    """Call the LLM and return {source, explanation, datasources_used}."""
    if not is_configured():
        raise RuntimeError(
            "LLM not configured. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in backend/.env"
        )

    from openai import OpenAI

    client = OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "not-needed",
        timeout=120.0,
    )

    response = client.chat.completions.create(
        model=settings.llm_model,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You write precise, defensive Python audit rules for a "
                    "continuous auditing platform. You always honour the given "
                    "contract exactly. You never invent columns and you only "
                    "reference the datasources you are shown."
                ),
            },
            {"role": "user", "content": build_prompt(prompt, entity, data_date)},
        ],
    )

    source = (response.choices[0].message.content or "").strip()
    source = _strip_fences(source)
    _validate_shape(source)

    return {
        "source": source,
        "explanation": _explain_usage(source, entity),
        "datasources_used": sorted(
            {
                n
                for n in registry_for(entity)
                if f'"{n}"' in source or f'ctx.table("{n}")' in source
            }
        ),
    }


def _strip_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _validate_shape(source: str) -> None:
    """Cheap structural gate before we ever store the file."""
    problems: list[str] = []
    if "def run(" not in source:
        problems.append("missing 'def run(ctx)' function")
    for status in ("success", "failed"):
        if status not in source:
            problems.append(f"never returns status '{status}'")
    if "ctx." not in source:
        problems.append("does not use the provided ctx object")
    banned = [
        m
        for m in ("import os", "import sys", "import subprocess", "open(", "socket")
        if m in source
    ]
    if banned:
        problems.append("contains banned constructs: " + ", ".join(banned))
    if problems:
        raise ValueError("Generated rule rejected - " + "; ".join(problems))


def _explain_usage(source: str, entity: str) -> str:
    used = sorted(
        {
            n
            for n in registry_for(entity)
            if f'"{n}"' in source or f'ctx.table("{n}")' in source
        }
    )
    if not used:
        return "No registered datasource referenced - check the generated logic."
    return "Reads: " + ", ".join(used)


def preview_context(entity: str, data_date: date) -> dict[str, Any]:
    """What the LLM will be shown - also handy for debugging in the UI."""
    registry = registry_for(entity)
    return {
        "entity": entity,
        "entity_name": ENTITIES.get(entity.upper(), entity),
        "systems": SYSTEMS_BY_ENTITY.get(entity.upper(), []),
        "data_date": data_date.isoformat(),
        "datasources": {
            name: {
                "system": src.system,
                "grain": src.grain,
                "columns": src.columns,
            }
            for name, src in registry.items()
        },
        "llm_configured": is_configured(),
    }