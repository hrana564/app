"""
ET-1  -  Vendor bank details match employee bank details

Systems: ERP (bc_vendors)  JOIN  HRMS (hr_employees)

Source requirement (demo estate continuous auditing area #22):
    "Exception report identifying vendor bank account details in the ERP that
     match employee bank account details in the HR system."

This is the cross-system proof: one rule, two systems, joined on a shared key
(the IBAN). It never touches the CSV paths - both tables arrive ready to use.

The IBAN is the whole of the test. Two records are the same bank account or
they are not, so this file holds no threshold and no tolerance.

A cross product of vendors against employees is fine at master-data sizes. If
either grows by orders of magnitude, match on the IBAN with a hash join rather
than a cross product.
"""

import polars as pl

NAME = "Vendor bank details match employee bank details"
DESCRIPTION = (
    "Vendor bank account details in ERP match employee bank "
    "details in HRMS - a payments-fraud indicator."
)
DATASOURCES = ["hr_employees", "bc_vendors"]

EXITED_STATUS = "exited"


def run(ctx) -> dict:
    employees = ctx.table("hr_employees").filter(
        pl.col("employment_status") != EXITED_STATUS
    )
    vendors = ctx.table("bc_vendors")

    if employees.height == 0 or vendors.height == 0:
        return {
            "status": "failed",
            "error_message": "Vendor or employee master not available for this date",
            "summary": "Vendor or employee master not available for this date",
            "metrics": {},
            "rows": [],
        }

    # The rename is not decoration. After the join both account numbers are in
    # one row, and the reader - the person, and the rule page's own reading of
    # this file - has to know which side each one came from.
    pairs = vendors.join(employees, how="cross").rename(
        {"vendor_bank_iban": "vendor_iban", "bank_iban": "employee_iban"}
    )

    rows = []
    for pair in pairs.iter_rows(named=True):
        if pair["vendor_iban"] != pair["employee_iban"]:
            continue
        rows.append(
            {
                "employee_id": pair["employee_id"],
                "employee_name": pair["full_name"],
                "employee_account_name": pair["bank_account_name"],
                "employee_iban": pair["employee_iban"],
                "vendor_id": pair["vendor_id"],
                "vendor_name": pair["vendor_name"],
                "vendor_account_name": pair["vendor_account_name"],
                "vendor_iban": pair["vendor_iban"],
            }
        )

    if not rows:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"No vendor bank account is shared with any of the "
                f"{employees.height} active HRMS employees "
                f"({vendors.height} ERP vendors checked)"
            ),
            "metrics": {
                "employees_scanned": employees.height,
                "vendors_scanned": vendors.height,
            },
            "rows": [],
        }

    vendors_hit = len({r["vendor_id"] for r in rows})
    employees_hit = len({r["employee_id"] for r in rows})

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"{len(rows)} vendor bank account(s) are also held by an employee, "
            f"covering {vendors_hit} vendor(s) and {employees_hit} "
            f"employee(s). Payments to these vendors require review."
        ),
        "metrics": {
            "iban_collisions": len(rows),
            "vendors_flagged": vendors_hit,
            "employees_flagged": employees_hit,
            "employees_scanned": employees.height,
            "vendors_scanned": vendors.height,
        },
        "rows": rows,
    }
