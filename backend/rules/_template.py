"""
Rule template - copy this when writing a rule by hand.

THE CONTRACT
------------
Every rule file must define:

    def run(ctx) -> dict:
        ...

and return a dict matching app.schemas.RuleResult:

    status  : "no_rows_returned" | "rows_returned" | "error"
    summary : str   - one line an auditor can read on a ticket
    metrics : dict  - the numbers behind the verdict
    rows    : list  - the rows that caused the breach (drives the ticket)

WHAT ctx GIVES YOU
------------------
    ctx.table("pos_sales")   -> quoted view name for FROM / JOIN
    ctx.sql(query)           -> Result (.all() / .one() / .scalar() / .count())
    ctx.date                 -> the date being audited
    ctx.log("...")           -> appends a note to the run summary
    ctx.count_rows("name")   -> row count of a view

KEEP RULES DEFENSIVE
--------------------
  * Declare thresholds as module constants in UPPER_CASE with a comment saying what
    they measure. Saving the file creates a new rule version, so the change is
    recorded rather than lost.
  * Guard against empty data - a missing day folder must not crash the run.
  * Interpolate numbers only after casting to float, never raw user strings.
  * Only import from: json, datetime, statistics, math, decimal.
  * Never import os / sys / subprocess / socket and never call open().
"""

NAME = "Template rule"
DESCRIPTION = "Copy me. Replace the SQL and the threshold."
DATASOURCES = ["pos_sales"]

# Thresholds belong to the rule. Changing one is a code change, which is
# recorded as a new entry in rule_versions - so the audit trail keeps it.
VALUE_LIMIT = 10.0


def run(ctx) -> dict:
    threshold = VALUE_LIMIT

    # --- 1. Gather the numbers behind the verdict -------------------------
    totals = ctx.sql("""
        SELECT count(*)           AS invoice_count,
               sum(invoice_value) AS total_value,
               max(invoice_value) AS max_value
        FROM ctx.table("pos_sales")
        WHERE NOT is_void
    """).one()

    if not totals or not totals["invoice_count"]:
        return {
            "status": "no_rows_returned",
            "summary": f"No data in SourceData/{ctx.date}",
            "metrics": {},
        }

    max_value = float(totals["max_value"] or 0)

    # --- 2. Decide ---------------------------------------------------------
    if max_value <= threshold:
        return {
            "status": "no_rows_returned",
            "summary": (
                f"{totals['invoice_count']} invoices scanned, max value "
                f"{max_value:.2f} within limit {threshold}"
            ),
            "metrics": {
                "invoice_count": totals["invoice_count"],
                "max_value": max_value,
            },
        }

    # --- 3. Return the offending rows (these become the ticket) ------------
    # threshold is cast to float above, so interpolation is safe here.
    rows = ctx.sql(f"""
        SELECT invoice_id, invoice_ts, store_id, staff_id,
               tender_type, invoice_value
        FROM ctx.table("pos_sales")
        WHERE NOT is_void AND invoice_value > {threshold}
        ORDER BY invoice_value DESC
        LIMIT 200
    """).all()

    summary = f"{len(rows)} invoice(s) above {threshold} AED; largest {max_value:.2f}"

    return {
        "status": "rows_returned",
        "summary": summary,
        "metrics": {
            "threshold": threshold,
            "max_value": max_value,
            "total_value": float(totals["total_value"] or 0),
        },
        "rows": rows,
    }