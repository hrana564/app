"""
LAD-CA-06  -  Ex-staff member still transacting in the POS

Entity : Laduree
Systems: iPOS (SaaS) (ipos_sales)  JOIN  HRMS (hr_employees)

Source requirement (Laduree continuous auditing area #9):
    "Inactive employees in HR but the POS access is still enabled or used."

A second cross-system proof, and a different join shape from ABM-CA-22: here
HRMS is the reference master and iPOS supplies the activity. The join key is
ipos_sales.staff_id == hr_employees.employee_id, which is only meaningful
because the generator used the same key on both sides.

Tunable via ctx.params: lad.ex_staff_min_days_after_exit (default 0)
"""

NAME = "LAD-CA-06 Ex-staff member still transacting in the POS"
DESCRIPTION = (
    "POS transactions settled by employees who have already exited in HRMS - "
    "access has not been revoked."
)
ENTITY = "LAD"
DATASOURCES = ["ipos_sales", "hr_employees"]


def run(ctx) -> dict:
    min_days = float(ctx.params.get("lad.ex_staff_min_days_after_exit", 0))

    rows = ctx.sql(f"""
        SELECT
            e.employee_id,
            e.full_name,
            e.nationality,
            e.employment_status,
            e.exit_date,
            count(*)                    AS transactions,
            sum(s.invoice_value)        AS value,
            min(s.invoice_ts)           AS first_tx,
            max(s.invoice_ts)           AS last_tx,
            count(DISTINCT s.store_id)  AS stores
        FROM ctx.table("ipos_sales") s
        JOIN ctx.table("hr_employees") e
          ON e.employee_id = s.staff_id
        WHERE e.is_exited
          AND NOT s.is_void
          AND date_diff('day', e.exit_date, CAST(s.invoice_ts AS DATE)) >= {min_days}
        GROUP BY e.employee_id, e.full_name, e.nationality,
                 e.employment_status, e.exit_date
        ORDER BY value DESC
        LIMIT 200
    """).all()

    if not rows:
        return {
            "status": "no_rows_returned",
            "summary": (
                "No POS activity by exited employees on this date - HR to POS "
                "access revocation is effective"
            ),
            "metrics": {},
        }

    tx_total = sum(int(r["transactions"]) for r in rows)
    value_total = sum(float(r["value"] or 0) for r in rows)

    return {
        "status": "rows_returned",
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