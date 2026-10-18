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
    ctx.table("name")  -> quoted table name, usable in FROM / JOIN
    ctx.sql(query)     -> run read-only SQL; returns Result
    ctx.date           -> datetime.date being audited
    ctx.log("...")     -> append a note to the run summary
    ctx.count_rows("name") -> row count of a table

Result helpers:
    .all()   -> list[dict]
    .one()   -> dict | None
    .scalar()-> single value
    .count() -> int

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
  * Only reference datasources you are shown. Every column you select must exist.
  * Declare every threshold as a module-level constant in UPPER_CASE with a
    comment saying what it measures, e.g. LAST_HOUR_RATIO_LIMIT = 1.5.
    Thresholds belong to the rule, not to a runtime configuration table.
  * Compute helper columns (is_cash, is_exited, hour, ...) in your own SQL.
    They are NOT provided.
  * Cast row values to str/float when embedding them in the summary.
  * Only import from: json, datetime, statistics, math, decimal
  * Do NOT write files, open sockets, or import os/subprocess/sys.
  * Emit ONLY the module source. No markdown fences, no commentary.
"""

EXAMPLE_RULE = """\
# Last-hour value may not exceed this multiple of the hourly average.
LAST_HOUR_RATIO_LIMIT = 1.5


def run(ctx):
    \"\"\"FSCS-CA-03  Last-hour invoice value spike.\"\"\"
    threshold = LAST_HOUR_RATIO_LIMIT

    totals = ctx.sql(\"\"\"
        SELECT
            sum(invoice_value) FILTER (WHERE hour = 23)          AS last_hour,
            sum(invoice_value) FILTER (WHERE hour < 23) / 23.0   AS hourly_avg
        FROM ctx.table("pos_sales")
        WHERE NOT is_void
    \"\"\").one()

    if not totals or not totals["last_hour"]:
        return {
            "status": "failed",
            "error_message": "No trading data for this date",
            "summary": "No trading data",
            "metrics": {},
            "rows": [],
        }

    ratio = totals["last_hour"] / totals["hourly_avg"] if totals["hourly_avg"] else 0.0

    if ratio < threshold:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"Last-hour value {totals['last_hour']:.0f} is {ratio:.2f}x "
                f"hourly average (limit {threshold})"
            ),
            "metrics": {"ratio": round(ratio, 3)},
            "rows": [],
        }

    rows = ctx.sql(\"\"\"
        SELECT invoice_id, invoice_ts, staff_id, store_id,
               invoice_value, item_group
        FROM ctx.table("pos_sales")
        WHERE hour = 23 AND NOT is_void
        ORDER BY invoice_value DESC
        LIMIT 50
    \"\"\").all()

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Last hour of trading = {totals['last_hour']:.0f} AED, "
            f"{ratio:.2f}x the {totals['hourly_avg']:.0f} hourly average "
            f"(limit {threshold}x), across {len(rows)} invoices"
        ),
        "metrics": {
            "last_hour_value": totals["last_hour"],
            "hourly_avg": totals["hourly_avg"],
            "ratio": round(ratio, 3),
            "threshold": threshold,
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