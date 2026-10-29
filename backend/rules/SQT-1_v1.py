"""
SQT-1  -  Last hour of trading volume spike

System : POS  ->  ipos_sales

Source requirement (demo estate continuous auditing area #3):
    "Sales invoices generated during the last hour of store operations
     containing only retail and bar items, with a value exceeding
     AED 150 / average invoice value during the day."

Applied here as: the value settled in the final trading hour must not exceed
LAST_HOUR_RATIO_LIMIT times the average hourly value for the day. Retail and
bar item groups are in scope.

`hour` is derived here rather than pre-computed for us: the source CSV has a
timestamp, and extracting the hour from it is part of what this control does.
"""

import polars as pl

NAME = "Last-hour of trading value spike"
DESCRIPTION = (
    "Value settled in the last hour of store operations exceeds a multiple of "
    "the average hourly value for the day (retail and bar items)."
)
DATASOURCES = ["ipos_sales"]

LAST_HOUR = 23  # the store's last hour of trade is hour 23

# The last hour may settle at most this multiple of the hourly average.
LAST_HOUR_RATIO_LIMIT = 2.0

TS_FORMAT = "%Y-%m-%d %H:%M:%S"

def run(ctx) -> dict:
    ratio_limit = LAST_HOUR_RATIO_LIMIT

    base = (
        ctx.table("ipos_sales")
        .with_columns(
            hour=pl.col("invoice_ts").str.to_datetime(TS_FORMAT, strict=False).dt.hour(),
        )
        .filter(
            (~pl.col("is_void")) & pl.col("item_group").is_in(["RETAIL", "BAR"])
        )
    )

    if base.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No retail/bar invoices on {ctx.date}",
            "metrics": {},
            "rows": [],
        }

    hourly = base.group_by("hour").agg(
        value=pl.col("invoice_value").sum(),
        invoices=pl.len(),
    )

    last_hour_value = float(
        hourly.filter(pl.col("hour") == LAST_HOUR)["value"].sum() or 0
    )
    last_hour_count = int(
        hourly.filter(pl.col("hour") == LAST_HOUR)["invoices"].sum() or 0
    )
    baseline = hourly.filter(pl.col("hour") < LAST_HOUR)
    baseline_hours = baseline.height
    hourly_avg = float(baseline["value"].mean() or 0)
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

    breach = base.filter(pl.col("hour") == LAST_HOUR)
    rows = (
        breach.sort("invoice_value", descending=True)
        .head(200)
        .select(
            "invoice_id", "invoice_ts", "store_id", "staff_id",
            "tender_type", "item_group", "invoice_value",
        )
        .to_dicts()
    )

    top_store = (
        breach.group_by("store_id")
        .agg(value=pl.col("invoice_value").sum())
        .sort("value", descending=True)
        .head(1)["store_id"]
        .to_list()
    )

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Last hour of trading = {last_hour_value:,.0f} AED across "
            f"{last_hour_count} invoices, {ratio:.2f}x the "
            f"{hourly_avg:,.0f} AED hourly average (limit {ratio_limit}x). "
            f"Top store {top_store[0] if top_store else 'n/a'}."
        ),
        "metrics": {
            "ratio": round(ratio, 3),
            "ratio_limit": ratio_limit,
            "last_hour_value": last_hour_value,
            "hourly_avg": round(hourly_avg, 2),
            "baseline_hours": baseline_hours,
            "day_value": float(base["invoice_value"].sum() or 0),
            "last_hour_invoices": last_hour_count,
        },
        "rows": rows,
    }