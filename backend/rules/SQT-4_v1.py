import polars as pl

NAME = "Discounts exceeding the single-discount and daily employee limits"
DESCRIPTION = "Flags discount lines above the single-discount limit and discounts where an employee at one store exceeds the daily limit, excluding agreed promotions."
DATASOURCES = ["ipos_discounts", "ipos_sales"]

# Maximum value permitted for one individual discount line.
SINGLE_DISCOUNT_LIMIT = 200.0

# Maximum total discount value permitted for one employee at one store on one day.
DAILY_EMPLOYEE_DISCOUNT_LIMIT = 200.0

# Promotion discounts are excluded because they may represent agreed promotional activity.
AGREED_DISCOUNT_TYPE = "PROMOTION"

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def run(ctx):
    discounts = ctx.table("ipos_discounts")
    sales = ctx.table("ipos_sales")

    if discounts.height == 0 or sales.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": "No discount or sales data available for the audit period.",
            "metrics": {},
            "rows": [],
        }

    discounts = discounts.with_columns(
        discount_date=pl.col("discount_ts")
        .str.to_datetime(TS_FORMAT, strict=False)
        .dt.date()
        .cast(pl.String),
    )

    sales = sales.with_columns(
        invoice_date=pl.col("invoice_ts")
        .str.to_datetime(TS_FORMAT, strict=False)
        .dt.date()
        .cast(pl.String),
    )

    eligible_discounts = (
        discounts.filter(pl.col("discount_type") != AGREED_DISCOUNT_TYPE)
        .join(
            sales.select(
                "invoice_id",
                "invoice_value",
                "is_void",
            ),
            on="invoice_id",
            how="inner",
        )
        .filter(pl.col("is_void") == False)  # noqa: E712
    )

    if eligible_discounts.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": "No eligible non-promotion discounts were found for the audit period.",
            "metrics": {
                "single_discount_limit": SINGLE_DISCOUNT_LIMIT,
                "daily_employee_discount_limit": DAILY_EMPLOYEE_DISCOUNT_LIMIT,
            },
            "rows": [],
        }

    daily_totals = (
        eligible_discounts.group_by(
            "discount_date",
            "store_id",
            "staff_id",
        )
        .agg(
            employee_daily_discount_total=pl.col("discount_value").sum(),
        )
    )

    findings = (
        eligible_discounts.join(
            daily_totals,
            on=["discount_date", "store_id", "staff_id"],
            how="left",
        )
        .filter(
            (pl.col("discount_value") > SINGLE_DISCOUNT_LIMIT)
            | (pl.col("employee_daily_discount_total") > DAILY_EMPLOYEE_DISCOUNT_LIMIT)
        )
        .select(
            "store_id",
            "staff_id",
            "invoice_id",
            "discount_id",
            "discount_value",
            "invoice_value",
            "employee_daily_discount_total",
        )
        .sort(
            "employee_daily_discount_total",
            descending=True,
        )
        .to_dicts()
    )

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Found {len(findings)} discount lines breaching the "
            f"{SINGLE_DISCOUNT_LIMIT:.2f} single-discount limit or the "
            f"{DAILY_EMPLOYEE_DISCOUNT_LIMIT:.2f} daily employee limit."
        ),
        "metrics": {
            "single_discount_limit": SINGLE_DISCOUNT_LIMIT,
            "daily_employee_discount_limit": DAILY_EMPLOYEE_DISCOUNT_LIMIT,
            "finding_count": len(findings),
        },
        "rows": findings,
    }