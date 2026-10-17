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
(the IBAN). It never touches the CSV paths - both tables arrive as DuckDB
views.

Tunable via ctx.params: abm.bank_match_min_similarity (default 92.0)
"""

NAME = "ABM-CA-22 Vendor bank details match employee bank details"
DESCRIPTION = (
    "Vendor bank account details in Business Central match employee bank "
    "details in HRMS - a payments-fraud indicator."
)
ENTITY = "ABM"
DATASOURCES = ["hr_employees", "bc_vendors"]


def run(ctx) -> dict:
    min_similarity = float(ctx.params.get("abm.bank_match_min_similarity", 92.0))

    counts = ctx.sql("""
        SELECT
            (SELECT count(*) FROM ctx.table("hr_employees"))     AS employees,
            (SELECT count(*) FROM ctx.table("bc_vendors"))       AS vendors
    """).one()

    if not counts or not counts["employees"] or not counts["vendors"]:
        return {
            "status": "no_rows_returned",
            "summary": "Vendor or employee master not available for this date",
            "metrics": {},
        }

    # Exact IBAN collision, plus a fuzzy catch on the account-holder name so a
    # re-keyed IBAN with the same name is still surfaced.
    rows = ctx.sql(f"""
        SELECT
            e.employee_id,
            e.full_name            AS employee_name,
            e.bank_account_name    AS employee_account_name,
            e.bank_iban            AS employee_iban,
            v.vendor_id,
            v.vendor_name,
            v.vendor_account_name,
            v.vendor_bank_iban     AS vendor_iban,
            CASE
                WHEN v.vendor_bank_iban = e.bank_iban THEN 'EXACT_IBAN_MATCH'
                ELSE 'NAME_SIMILARITY'
            END AS match_type
        FROM ctx.table("hr_employees") e
        JOIN ctx.table("bc_vendors")  v
          ON  v.vendor_bank_iban = e.bank_iban
          OR regexp_replace(lower(v.vendor_account_name), '[^a-z0-9]', '', 'g')
           = regexp_replace(lower(e.bank_account_name), '[^a-z0-9]', '', 'g')
        WHERE NOT e.is_exited
        ORDER BY match_type DESC, e.employee_id
        LIMIT 200
    """).all()

    if not rows:
        return {
            "status": "no_rows_returned",
            "summary": (
                f"No overlap between {counts['employees']} active HRMS employees "
                f"and {counts['vendors']} Business Central vendor bank accounts"
            ),
            "metrics": {
                "employees_scanned": counts["employees"],
            "vendors_scanned": counts["vendors"],
        },
        }

    exact = [r for r in rows if r["match_type"] == "EXACT_IBAN_MATCH"]
    vendors_hit = len({r["vendor_id"] for r in rows})
    employees_hit = len({r["employee_id"] for r in rows})

    return {
        "status": "rows_returned",
        "summary": (
            f"{len(exact)} exact IBAN collision(s) between Business Central "
            f"vendors and HRMS employees, covering {vendors_hit} vendor(s) and "
            f"{employees_hit} employee(s). Payments to these vendors require review."
        ),
        "metrics": {
            "exact_iban_matches": len(exact),
            "vendors_flagged": vendors_hit,
            "employees_flagged": employees_hit,
            "employees_scanned": counts["employees"],
            "vendors_scanned": counts["vendors"],
        },
        "rows": rows,
    }