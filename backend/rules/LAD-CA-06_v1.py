"""
LAD-CA-06  -  Ex-staff member still transacting in the POS

Systems: POS (ipos_sales)  JOIN  HRMS (hr_employees)

Source requirement (demo estate continuous auditing area #9):
    "Inactive employees in HR but the POS access is still enabled or used."

A second cross-system proof, and a different join shape from CA-22: here
HRMS is the reference master and POS supplies the activity. The join key is
ipos_sales.staff_id == hr_employees.employee_id, which is only meaningful
because the generator used the same key on both sides.

"Who has exited" is read from employment_status rather than a pre-computed
flag: the status vocabulary belongs to HRMS, so this rule is where it is
interpreted.

The grace period lives in this file.
"""

import polars as pl

NAME = "Ex-staff member still transacting in the POS"
DESCRIPTION = (
    "POS transactions settled by employees who have already exited in HRMS - "
    "access has not been revoked."
)
DATASOURCES = ["ipos_sales", "hr_employees"]

# Days after exit before POS activity counts as a breach.
EX_STAFF_GRACE_DAYS = 0

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"
EXITED_STATUS = "exited"

def run(ctx) -> dict:
    min_days = EX_STAFF_GRACE_DAYS

    sales = ctx.table("ipos_sales").filter(~pl.col("is_void")).with_columns(
        tx_date=pl.col("invoice_ts").str.to_datetime(TS_FORMAT, strict=False).dt.date(),
    )
    exited = ctx.table("hr_employees").filter(
        pl.col("employment_status") == EXITED_STATUS
    ).with_columns(
        exit_date=pl.col("exit_date").str.to_date(DATE_FORMAT, strict=False),
    )

    activity = (
        sales.join(
            exited,
            left_on="staff_id",
            right_on="employee_id",
            how="inner",
        )
        .filter(
            pl.col("exit_date").is_not_null()
            & (
                (pl.col("tx_date") - pl.col("exit_date")).dt.total_days()
                >= min_days
            )
        )
    )

    if activity.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                "No POS activity by exited employees on this date - HR to POS "
                "access revocation is effective"
            ),
            "metrics": {},
            "rows": [],
        }

    rows = (
        activity.group_by(
            "staff_id", "full_name", "nationality",
            "employment_status", "exit_date",
        )
        .agg(
            transactions=pl.len(),
            value=pl.col("invoice_value").sum(),
            first_tx=pl.col("invoice_ts").min(),
            last_tx=pl.col("invoice_ts").max(),
            stores=pl.col("store_id").n_unique(),
        )
        .sort("value", descending=True)
        .head(200)
        # The join guarantees staff_id == employee_id; polars keeps only the
        # left key, so the employee id is renamed back for the reader.
        .rename({"staff_id": "employee_id"})
        .to_dicts()
    )

    tx_total = sum(int(r["transactions"]) for r in rows)
    value_total = sum(float(r["value"] or 0) for r in rows)

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"{len(rows)} exited employee(s) settled {tx_total} POS transaction(s) "
            f"worth {value_total:,.0f} AED after leaving. Worst: "
            f"{rows[0]['full_name']} ({rows[0]['transactions']} txns, "
            f"last active {rows[0]['last_tx']})."
        ),
        "metrics": {
            "ex_staff_with_activity": len(rows),
            "transactions": tx_total,
            "value": round(value_total, 2),
        },
        "rows": rows,
    }