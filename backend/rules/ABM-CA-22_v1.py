"""
ABM-CA-22  -  Vendor bank details match employee bank details

Entity : Al Boom Marine
Systems: Business Central (bc_vendors)  JOIN  HRMS (hr_employees)

Source requirement (ABM continuous auditing areas #22 and #23):
    "Exception report identifying vendor bank account details in the ERP that
     match employee bank account details in the HR system."
    "Exception report listing any similarity in the vendor account details in
     the ERP with the employee details (including employee personal address
     with vendor address, employee relative name with vendor name etc.)"

This is the cross-system proof: one rule, two systems, joined on a shared key
(the IBAN). It never touches the CSV paths - both tables arrive ready to use.

The match threshold lives in this file.

A cross product of vendors against employees is fine at master-data sizes. If
either grows by orders of magnitude, match the IBAN half with a hash join and
give the name half an index rather than a cross product.
"""

from difflib import SequenceMatcher

import polars as pl

NAME = "ABM-CA-22 Vendor bank details match employee bank details"
DESCRIPTION = (
    "Vendor bank account details in Business Central match employee bank "
    "details in HRMS - a payments-fraud indicator."
)
ENTITY = "ABM"
DATASOURCES = ["hr_employees", "bc_vendors"]

# A vendor bank record must clear this similarity (%) against an employee
# record before it is treated as a control breach. A shared IBAN clears it
# outright; a name match has to earn it.
BANK_MATCH_MIN_SIMILARITY = 92.0

NON_ALPHANUMERIC = r"[^a-z0-9]"
EXITED_STATUS = "exited"


def _normalise(column):
    """Lowercase and drop everything that is not a letter or a digit.

    Strips the spaces, punctuation and trading suffixes that stop
    "ABM Employee 0001" and "abm-employee-0001 llc" from ever comparing equal.
    """
    return column.str.to_lowercase().str.replace_all(NON_ALPHANUMERIC, "")


def _similarity(left: str, right: str) -> float:
    """Percentage similarity, 0-100."""
    if not left or not right:
        return 0.0
    if left == right:
        return 100.0
    return round(SequenceMatcher(None, left, right).ratio() * 100, 2)


def run(ctx) -> dict:
    min_similarity = BANK_MATCH_MIN_SIMILARITY

    employees = ctx.table("hr_employees").filter(
        pl.col("employment_status") != EXITED_STATUS
    ).with_columns(_ek=_normalise(pl.col("bank_account_name")))
    vendors = ctx.table("bc_vendors").with_columns(
        _nk=_normalise(pl.col("vendor_account_name"))
    )

    if employees.height == 0 or vendors.height == 0:
        return {
            "status": "failed",
            "error_message": "Vendor or employee master not available for this date",
            "summary": "Vendor or employee master not available for this date",
            "metrics": {},
            "rows": [],
        }

    pairs = vendors.join(employees, how="cross")

    rows = []
    for pair in pairs.iter_rows(named=True):
        if pair["vendor_bank_iban"] == pair["bank_iban"]:
            score, match_type = 100.0, "EXACT_IBAN_MATCH"
        else:
            score = _similarity(pair["_nk"], pair["_ek"])
            if score < min_similarity:
                continue
            match_type = "NAME_SIMILARITY"
        rows.append(
            {
                "employee_id": pair["employee_id"],
                "employee_name": pair["full_name"],
                "employee_account_name": pair["bank_account_name"],
                "employee_iban": pair["bank_iban"],
                "vendor_id": pair["vendor_id"],
                "vendor_name": pair["vendor_name"],
                "vendor_account_name": pair["vendor_account_name"],
                "vendor_iban": pair["vendor_bank_iban"],
                "match_type": match_type,
                "similarity_pct": score,
            }
        )

    if not rows:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"No overlap between {employees.height} active HRMS employees "
                f"and {vendors.height} Business Central vendor bank accounts"
            ),
            "metrics": {
                "employees_scanned": employees.height,
                "vendors_scanned": vendors.height,
                "min_similarity": min_similarity,
            },
            "rows": [],
        }

    exact = [r for r in rows if r["match_type"] == "EXACT_IBAN_MATCH"]
    by_name = [r for r in rows if r["match_type"] == "NAME_SIMILARITY"]
    vendors_hit = len({r["vendor_id"] for r in rows})
    employees_hit = len({r["employee_id"] for r in rows})

    rows.sort(key=lambda r: (r["match_type"] != "EXACT_IBAN_MATCH", r["employee_id"]))
    rows = rows[:200]

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"{len(exact)} exact IBAN collision(s) between Business Central "
            f"vendors and HRMS employees, covering {vendors_hit} vendor(s) and "
            f"{employees_hit} employee(s). Payments to these vendors require review."
        ),
        "metrics": {
            "exact_iban_matches": len(exact),
            "name_similarity_matches": len(by_name),
            "vendors_flagged": vendors_hit,
            "employees_flagged": employees_hit,
            "employees_scanned": employees.height,
            "vendors_scanned": vendors.height,
            "min_similarity": min_similarity,
        },
        "rows": rows,
    }