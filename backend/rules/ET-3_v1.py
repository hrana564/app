import polars as pl


NAME = "Sales posted after the store closed"
DESCRIPTION = "Identifies non-void invoices posted more than two minutes after the store's end-of-day closing time."
DATASOURCES = ["ipos_sales", "hr_attendance"]

MINUTES_AFTER_CLOSING_LIMIT = 2  # Maximum minutes after closing allowed before a sale is late
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def run(ctx):
    sales = ctx.table("ipos_sales").with_columns(
        pl.col("invoice_ts")
        .cast(pl.String, strict=False)
        .str.to_datetime(TS_FORMAT, strict=False)
        .alias("parsed_invoice_ts")
    ).with_columns(
        pl.col("parsed_invoice_ts").dt.date().alias("sale_date")
    )

    if sales.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": "No sales were provided for the audit window.",
            "metrics": {"invoices_checked": 0, "late_invoices": 0},
            "rows": [],
        }

    attendance = ctx.table("hr_attendance").with_columns(
        pl.col("clock_out")
        .cast(pl.String, strict=False)
        .str.to_datetime(TS_FORMAT, strict=False)
        .alias("parsed_clock_out")
    ).with_columns(
        pl.col("parsed_clock_out").dt.date().alias("attendance_date")
    )

    if attendance.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": "No store closing times were available; no late sales could be identified.",
            "metrics": {
                "invoices_checked": int(sales.height),
                "late_invoices": 0,
            },
            "rows": [],
        }

    closing_times = (
        attendance.group_by("store_id", "attendance_date")
        .agg(
            pl.col("parsed_clock_out")
            .filter(pl.col("eod_closing") == True)  # noqa: E712
            .max()
            .alias("designated_closing_ts"),
            pl.col("parsed_clock_out")
            .max()
            .alias("last_clock_out"),
        )
        .with_columns(
            pl.coalesce(
                [pl.col("designated_closing_ts"), pl.col("last_clock_out")]
            ).alias("closing_ts")
        )
        .select("store_id", "attendance_date", "closing_ts")
    )

    checked = (
        sales.join(
            closing_times,
            left_on=["store_id", "sale_date"],
            right_on=["store_id", "attendance_date"],
            how="inner",
        )
        .filter(
            (pl.col("is_void") == False)  # noqa: E712
            & pl.col("parsed_invoice_ts").is_not_null()
            & pl.col("closing_ts").is_not_null()
        )
        .with_columns(
            (
                (pl.col("parsed_invoice_ts") - pl.col("closing_ts"))
                .dt.total_seconds()
                / 60
            )
            .alias("elapsed_minutes")
        )
        .with_columns(
            pl.col("elapsed_minutes")
            .ceil()
            .cast(pl.Int64)
            .alias("minutes_late")
        )
    )

    breached = checked.filter(
        pl.col("elapsed_minutes") > MINUTES_AFTER_CLOSING_LIMIT
    )

    rows = (
        breached.sort(["store_id", "parsed_invoice_ts", "invoice_id"])
        .select(
            pl.col("store_id"),
            pl.col("staff_id").alias("employee_id"),
            pl.col("invoice_id"),
            pl.col("invoice_value").cast(pl.Float64),
            pl.col("parsed_invoice_ts")
            .dt.strftime(TS_FORMAT)
            .alias("invoice_ts"),
            pl.col("minutes_late"),
        )
        .to_dicts()
    )

    if not rows:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"No non-void sales were posted more than "
                f"{MINUTES_AFTER_CLOSING_LIMIT} minutes after closing."
            ),
            "metrics": {
                "invoices_checked": int(checked.height),
                "late_invoices": 0,
                "minutes_after_closing_limit": MINUTES_AFTER_CLOSING_LIMIT,
            },
            "rows": [],
        }

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Found {len(rows)} non-void sales posted more than "
            f"{MINUTES_AFTER_CLOSING_LIMIT} minutes after closing."
        ),
        "metrics": {
            "invoices_checked": int(checked.height),
            "late_invoices": len(rows),
            "minutes_after_closing_limit": MINUTES_AFTER_CLOSING_LIMIT,
        },
        "rows": rows,
    }