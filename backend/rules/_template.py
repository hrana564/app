"""
Rule template - copy this when writing a rule by hand.

THE CONTRACT
------------
Every rule file must define:

    def run(ctx) -> dict:
        ...

and return a dict matching app.schemas.RuleResult:

    status        : "success" | "failed"   - did the rule EXECUTE?
    error_message : str | None             - if "failed", why; else None
    summary       : str   - one line an auditor can read on a ticket
    metrics       : dict  - the numbers behind the verdict
    rows          : list  - the findings. Non-empty -> ticket. Empty -> no ticket.

A rule that runs and finds nothing:
    return {"status": "success", "error_message": None, "rows": []}

A rule that cannot run (missing data, query error):
    return {"status": "failed", "error_message": "...", "rows": []}

WHAT ctx GIVES YOU
------------------
    ctx.table("ipos_sales")   a polars DataFrame of the rows this run staged
    ctx.date                  the anchor date being audited
    ctx.lookback_days         how far back to read (1 = that day, 5 = T-5 to T)
    ctx.log("...")            appends a note to the run summary
    ctx.count_rows("name")    how many rows a table has

You never see a file path and never write a query. Every table you ask for was
staged for this run, and nothing else is reachable - so the run folder under
Rules_Executions/ is a true record of what the rule read.

HOW YOUR ROWS BECOME A TICKET
------------------------------
There is no delivery policy to configure. Whatever rows you return are the
ticket: non-empty opens one ticket, empty opens none. If you want one ticket
per store rather than one per run, group the rows yourself.

A WORKED EXAMPLE
----------------
    import polars as pl

    NAME = "LAD-CA-XX What this control proves"
    ENTITY = "LAD"
    DATASOURCES = ["ipos_sales"]

    # Thresholds belong to the rule. Changing one is a code change, which is
    # recorded as a new version row - so the audit trail keeps it.
    VALUE_LIMIT = 500.0

    def run(ctx) -> dict:
        sales = ctx.table("ipos_sales").filter(~pl.col("is_void"))
        if sales.height == 0:
            return {
                "status": "success", "error_message": None,
                "summary": f"No sales on {ctx.date}",
                "metrics": {}, "rows": [],
            }

        over = sales.filter(pl.col("invoice_value") > VALUE_LIMIT)

        if over.height == 0:
            return {
                "status": "success", "error_message": None,
                "summary": (
                    f"{sales.height} invoices scanned, none above "
                    f"{VALUE_LIMIT} AED"
                ),
                "metrics": {
                    "scanned": sales.height,
                    "value_limit": VALUE_LIMIT,
                },
                "rows": [],
            }

        largest = over["invoice_value"].max()
        return {
            "status": "success", "error_message": None,
            "summary": (
                f"{over.height} invoice(s) above {VALUE_LIMIT} AED; "
                f"largest {largest:,.2f}"
            ),
            "metrics": {
                "scanned": sales.height,
                "above_limit": over.height,
                "value_limit": VALUE_LIMIT,
                "max_value": largest,
            },
            "rows": over.select(
                "invoice_id", "invoice_ts", "store_id",
                "staff_id", "tender_type", "invoice_value",
            ).head(200).to_dicts(),
        }

KEEP RULES DEFENSIVE
--------------------
  * Declare thresholds as module constants in UPPER_CASE with a comment saying
    what they measure.
  * Derive your own helper columns in the frame. Nothing is pre-computed for
    you - the hour of a timestamp, whether a tender is cash, whether someone
    has exited are all yours to write.
  * An empty table is normal. Say so in the summary and return no rows rather
    than raising.
  * `to_dicts()` on a frame with timestamps hands back datetime objects; the
    runner converts them, so you do not have to.
  * Only import from: polars, json, datetime, statistics, math, decimal,
    difflib, collections, itertools, functools, re, typing.
  * Never import os / sys / subprocess / socket and never call open().
"""

import polars as pl

NAME = "Template rule"
DESCRIPTION = "Copy me. Replace the table, the threshold and the frame work."
ENTITY = "LAD"
DATASOURCES = ["ipos_sales"]

VALUE_LIMIT = 500.0


def run(ctx) -> dict:
    limit = VALUE_LIMIT

    sales = ctx.table("ipos_sales").filter(~pl.col("is_void"))
    if sales.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No sales on {ctx.date}",
            "metrics": {},
            "rows": [],
        }

    over = sales.filter(pl.col("invoice_value") > limit)

    if over.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"{sales.height} invoices scanned, none above {limit} AED"
            ),
            "metrics": {
                "scanned": sales.height,
                "value_limit": limit,
            },
            "rows": [],
        }

    largest = over["invoice_value"].max()

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"{over.height} invoice(s) above {limit} AED; "
            f"largest {largest:,.2f}"
        ),
        "metrics": {
            "scanned": sales.height,
            "above_limit": over.height,
            "value_limit": limit,
            "max_value": largest,
        },
        "rows": (
            over.sort("invoice_value", descending=True)
            .select(
                "invoice_id", "invoice_ts", "store_id",
                "staff_id", "tender_type", "invoice_value",
            )
            .head(200)
            .to_dicts()
        ),
    }