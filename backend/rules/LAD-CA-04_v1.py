"""
LAD-CA-04  -  Single staff member dominates a day's cash settlement

Entity : Laduree
System : iPOS (SaaS)  ->  ipos_sales

Source requirement (Laduree continuous auditing areas #4 and #6):
    "Sales invoices settled by a single staff member accounting for more than
     a specified % of the total invoices settled during the day (say 50%)."
    "Sales invoices with tender type 'cash collections' settled by a single
     staff member beyond a specified % (say 50%)"

Applied to cash-tendered invoices per the stricter of the two clauses.
"""

NAME = "LAD-CA-04 Single staff member dominates daily cash settlement"
DESCRIPTION = (
    "One staff member settles more than the permitted share of the day's "
    "cash-tendered invoices."
)
ENTITY = "LAD"
DATASOURCES = ["ipos_sales"]

# One staff member may settle at most this share of the day's cash invoices.
SINGLE_STAFF_CASH_SHARE_LIMIT = 50.0


def run(ctx) -> dict:
    share_limit = SINGLE_STAFF_CASH_SHARE_LIMIT

    totals = ctx.sql("""
        SELECT count(*) AS cash_invoices
        FROM ctx.table("ipos_sales")
        WHERE NOT is_void AND is_cash
    """).one()

    if not totals or not totals["cash_invoices"]:
        return {
            "status": "no_rows_returned",
            "summary": f"No cash invoices in LAD SourceData/{ctx.date}",
            "metrics": {},
        }

    cash_invoices = int(totals["cash_invoices"])

    leaders = ctx.sql(f"""
        SELECT staff_id,
               count(*)           AS invoices,
               sum(invoice_value) AS value,
               100.0 * count(*) / {cash_invoices} AS share_pct
        FROM ctx.table("ipos_sales")
        WHERE NOT is_void AND is_cash
        GROUP BY staff_id
        HAVING 100.0 * count(*) / {cash_invoices} > {share_limit}
        ORDER BY invoices DESC
    """).all()

    if not leaders:
        return {
            "status": "no_rows_returned",
            "summary": (
                f"No staff member settled more than {share_limit}% of the "
                f"{cash_invoices} cash invoices"
            ),
            "metrics": {"cash_invoices": cash_invoices, "share_limit": share_limit},
        }

    top = leaders[0]

    # Ticket rows = the cash invoices settled by the dominant staff member.
    rows = ctx.sql(f"""
        SELECT invoice_id, invoice_ts, store_id, staff_id,
               tender_type, item_group, invoice_value
        FROM ctx.table("ipos_sales")
        WHERE NOT is_void AND is_cash AND staff_id = '{top['staff_id']}'
        ORDER BY invoice_value DESC
        LIMIT 200
    """).all()

    also = ", ".join(f"{r['staff_id']} ({float(r['share_pct']):.1f}%)" for r in leaders)

    return {
        "status": "rows_returned",
        "summary": (
            f"Staff {top['staff_id']} settled {top['invoices']} of "
            f"{cash_invoices} cash invoices ({float(top['share_pct']):.1f}%, "
            f"limit {share_limit}%) worth {float(top['value']):,.0f} AED. "
            f"Also above limit: {also}"
        ),
        "metrics": {
            "cash_invoices": cash_invoices,
            "share_limit": share_limit,
            "top_staff_id": top["staff_id"],
            "top_staff_invoices": top["invoices"],
            "top_staff_share_pct": round(float(top["share_pct"]), 2),
            "staff_above_limit": len(leaders),
        },
        "rows": rows,
    }