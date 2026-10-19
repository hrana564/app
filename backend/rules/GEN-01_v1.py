import polars as pl

NAME = "POS transactions settled by departed employees"
DESCRIPTION = "Identifies POS transactions settled by employees on or after their exit date, when access should already have been revoked, and groups the value settled per person."
ENTITY = "CLIENT-A"
DATASOURCES = ["ipos_sales", "hr_employees"]

EXIT_DATE_FORMAT = "%Y-%m-%d"
INVOICE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def run(ctx) -> dict:
    sales = ctx.table("ipos_sales").with_columns(
        invoice_ts_parsed=pl.col("invoice_ts")
        .cast(pl.String)
        .str.to_datetime(INVOICE_TIMESTAMP_FORMAT, strict=False),
    )

    if sales.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": "No POS transaction data was available for the audited period.",
            "metrics": {},
            "rows": [],
        }

    employees = ctx.table("hr_employees").with_columns(
        exit_date_parsed=pl.col("exit_date")
        .cast(pl.String, strict=False)
        .str.to_date(EXIT_DATE_FORMAT, strict=False),
    )

    if employees.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": "No employee data was available to test POS access after employee exit.",
            "metrics": {},
            "rows": [],
        }

    offending = (
        sales.filter(pl.col("is_void") == pl.lit(False))
        .with_columns(
            invoice_date=pl.col("invoice_ts_parsed").dt.date(),
        )
        .join(
            employees.select(
                pl.col("employee_id").alias("staff_id"),
                pl.col("full_name"),
                pl.col("exit_date_parsed"),
                pl.col("department"),
                pl.col("home_store"),
            ),
            on="staff_id",
            how="inner",
        )
        .filter(
            pl.col("exit_date_parsed").is_not_null()
            & pl.col("invoice_ts_parsed").is_not_null()
            & (pl.col("invoice_date") >= pl.col("exit_date_parsed"))
        )
    )

    if offending.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": "No POS transactions were settled by employees on or after their exit dates.",
            "metrics": {},
            "rows": [],
        }

    findings = (
        offending.group_by(
            "staff_id",
            "full_name",
            "exit_date_parsed",
            "department",
            "home_store",
        )
        .agg(
            transaction_count=pl.len(),
            total_settled=pl.col("invoice_value").cast(pl.Float64, strict=False).sum(),
            first_transaction=pl.col("invoice_ts_parsed").min(),
            last_transaction=pl.col("invoice_ts_parsed").max(),
        )
        .sort("total_settled", descending=True)
        .select(
            pl.col("staff_id").alias("employee_id"),
            pl.col("full_name"),
            pl.col("department"),
            pl.col("home_store"),
            pl.col("exit_date_parsed").alias("exit_date"),
            pl.col("transaction_count"),
            pl.col("total_settled"),
            pl.col("first_transaction").alias("first_transaction_ts"),
            pl.col("last_transaction").alias("last_transaction_ts"),
        )
        .to_dicts()
    )

    total_settled = sum(float(row.get("total_settled") or 0) for row in findings)
    employee_count = len(findings)
    transaction_count = sum(int(row.get("transaction_count") or 0) for row in findings)

    for row in findings:
        row["total_settled"] = float(row.get("total_settled") or 0)
        row["transaction_count"] = int(row.get("transaction_count") or 0)

        if row.get("exit_date") is not None:
            row["exit_date"] = str(row["exit_date"])

        if row.get("first_transaction_ts") is not None:
            row["first_transaction_ts"] = str(row["first_transaction_ts"])

        if row.get("last_transaction_ts") is not None:
            row["last_transaction_ts"] = str(row["last_transaction_ts"])

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Found {transaction_count} POS transactions worth {total_settled:.2f} "
            f"settled by {employee_count} departed employee(s) on or after their exit dates."
        ),
        "metrics": {
            "employee_count": employee_count,
            "transaction_count": transaction_count,
            "total_settled": total_settled,
        },
        "rows": findings,
    }