"""
LAD-CA-03  -  Last hour of trading volume spike

Entity : Laduree
System : iPOS (SaaS)  ->  ipos_sales

Source requirement (Laduree continuous auditing area #3):
    "Sales invoices generated during the last hour of store operations
     containing only retail and bar items, with a value exceeding
     AED 150 / average invoice value during the day."

Applied here as: the value settled in the final trading hour must not exceed
LAST_HOUR_RATIO_LIMIT times the average hourly value for the day. Retail and
bar item groups are in scope.
"""

NAME = "LAD-CA-03 Last-hour of trading value spike"
DESCRIPTION = (
    "Value settled in the last hour of store operations exceeds a multiple of "
    "the average hourly value for the day (retail and bar items)."
)
ENTITY = "LAD"
DATASOURCES = ["ipos_sales"]

LAST_HOUR = 23  # stores trade 10:00-23:59, so the final trading hour is 23

# The last hour may settle at most this multiple of the hourly average.
LAST_HOUR_RATIO_LIMIT = 2.0


def run(ctx) -> dict:
    ratio_limit = LAST_HOUR_RATIO_LIMIT

    totals = ctx.sql(f"""
        SELECT
            count(*)                                        AS invoice_count,
            sum(invoice_value)                              AS day_value,
            sum(invoice_value) FILTER (WHERE hour = {LAST_HOUR})  AS last_hour_value,
            count(*) FILTER (WHERE hour = {LAST_HOUR})      AS last_hour_count,
            sum(invoice_value) FILTER (WHERE hour < {LAST_HOUR})
                / nullif(count(DISTINCT hour) FILTER (WHERE hour < {LAST_HOUR}), 0)
                                                            AS hourly_avg,
            count(DISTINCT hour) FILTER (WHERE hour < {LAST_HOUR}) AS baseline_hours
        FROM ctx.table("ipos_sales")
        WHERE NOT is_void
          AND item_group IN ('RETAIL', 'BAR')
    """).one()

    if not totals or not totals["invoice_count"]:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No retail/bar invoices in LAD SourceData/{ctx.date}",
            "metrics": {},
            "rows": [],
        }

    last_hour_value = float(totals["last_hour_value"] or 0)
    hourly_avg = float(totals["hourly_avg"] or 0)
    ratio = (last_hour_value / hourly_avg) if hourly_avg else 0.0

    if ratio <= ratio_limit:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"Last hour {last_hour_value:,.0f} AED is {ratio:.2f}x the "
                f"{hourly_avg:,.0f} AED hourly average (limit {ratio_limit}x)"
            ),
            "metrics": {
                "ratio": round(ratio, 3),
                "last_hour_value": last_hour_value,
                "hourly_avg": round(hourly_avg, 2),
                "ratio_limit": ratio_limit,
            },
            "rows": [],
        }

    rows = ctx.sql(f"""
        SELECT invoice_id, invoice_ts, store_id, staff_id,
               tender_type, item_group, invoice_value
        FROM ctx.table("ipos_sales")
        WHERE NOT is_void
          AND item_group IN ('RETAIL', 'BAR')
          AND hour = {LAST_HOUR}
        ORDER BY invoice_value DESC
        LIMIT 200
    """).all()

    by_store = ctx.sql(f"""
        SELECT store_id, count(*) AS invoices, sum(invoice_value) AS value
        FROM ctx.table("ipos_sales")
        WHERE NOT is_void AND item_group IN ('RETAIL', 'BAR') AND hour = {LAST_HOUR}
        GROUP BY store_id
        ORDER BY value DESC
    """).all()

    top_store = by_store[0]["store_id"] if by_store else "n/a"

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Last hour of trading = {last_hour_value:,.0f} AED across "
            f"{totals['last_hour_count']} invoices, {ratio:.2f}x the "
            f"{hourly_avg:,.0f} AED hourly average (limit {ratio_limit}x). "
            f"Top store {top_store}."
        ),
        "metrics": {
            "ratio": round(ratio, 3),
            "ratio_limit": ratio_limit,
            "last_hour_value": last_hour_value,
            "hourly_avg": round(hourly_avg, 2),
            "baseline_hours": totals["baseline_hours"],
            "day_value": float(totals["day_value"] or 0),
            "last_hour_invoices": totals["last_hour_count"],
        },
        "rows": rows,
    }