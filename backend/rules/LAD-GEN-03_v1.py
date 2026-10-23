import polars as pl

NAME = "Discount given beyond the agreed per-day limit"
DESCRIPTION = (
    "Flags till discount lines where the discount value exceeds the agreed "
    "limit of 300 AED per employee per day, with the invoice it was given on."
)
DATASOURCES = ["ipos_discounts", "ipos_sales", "hr_employees"]

# Maximum discount value a single employee may give on one invoice line.
DAILY_DISCOUNT_LIMIT = 300.0

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def run(ctx) -> dict:
    limit = DAILY_DISCOUNT_LIMIT

    disc = ctx.table("ipos_discounts")
    sales = ctx.table("ipos_sales")
    hr = ctx.table("hr_employees")

    if disc.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No discount lines staged on {ctx.date}",
            "metrics": {"discount_lines": 0},
            "rows": [],
        }

    disc = disc.with_columns(
        discount_dt=pl.col("discount_ts").str.to_datetime(TS_FORMAT, strict=False),
        discount_value=pl.col("discount_value").cast(pl.Float64),
    )
    disc = disc.with_columns(discount_day=pl.col("discount_dt").dt.date())

    agg = (
        disc.group_by(["staff_id", "discount_day"])
        .agg(day_total=pl.col("discount_value").sum())
    )

    joined = (
        disc.join(agg, on=["staff_id", "discount_day"], how="left")
        .join(
            sales.select(
                "invoice_id", "invoice_ts", "invoice_value", "is_void",
                "store_id", "staff_id",
            ).rename({"store_id": "sales_store_id", "staff_id": "sales_staff_id"}),
            on="invoice_id",
            how="left",
        )
        .join(
            hr.select("employee_id", "full_name", "department", "designation",
                      "home_store", "employment_status"),
            left_on="staff_id",
            right_on="employee_id",
            how="left",
        )
    )

    breach = joined.filter(pl.col("discount_value") > limit).sort(
        "discount_value", descending=True
    )

    rows = breach.select(
        "discount_id", "invoice_id", "discount_ts", "discount_type", "sku",
        "store_id", "staff_id", "full_name", "department", "designation",
        "home_store", "employment_status", "discount_value", "day_total",
        "invoice_ts", "invoice_value", "is_void",
    ).to_dicts()

    per_employee = (
        breach.group_by("staff_id")
        .agg(lines=pl.len(), total=pl.col("discount_value").sum())
        .sort("total", descending=True)
    )

    metrics = {
        "discount_lines": int(disc.height),
        "breach_lines": int(breach.height),
        "employees_flagged": int(per_employee.height),
        "threshold": limit,
    }

    if breach.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"No discount line exceeds the {limit:.0f} limit; "
                f"{int(disc.height)} discount lines checked on {ctx.date}"
            ),
            "metrics": metrics,
            "rows": [],
        }

    top = per_employee.head(5).to_dicts()
    names = ", ".join(
        f"{r['staff_id']} ({float(r['total']):.2f} AED)" for r in top
    )
    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"{int(breach.height)} discount line(s) exceed the {limit:.0f} AED "
            f"limit across {int(per_employee.height)} employee(s). "
            f"Highest: {names}"
        ),
        "metrics": metrics,
        "rows": rows,
    }