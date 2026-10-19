"""
CA-04  -  Single staff member dominates a day's cash settlement

System : POS  ->  ipos_sales

Source requirement (demo estate continuous auditing areas #4 and #6):
    "Sales invoices settled by a single staff member accounting for more than
     a specified % of the total invoices settled during the day (say 50%)."
    "Sales invoices with tender type 'cash collections' settled by a single
     staff member beyond a specified % (say 50%)"

Applied to cash-tendered invoices per the stricter of the two clauses.

`is_cash` is derived here from tender_type rather than pre-computed: deciding
what counts as a cash tender is this control's business, not the loader's.
"""

import polars as pl

NAME = "CA-04 Single staff member dominates daily cash settlement"
DESCRIPTION = (
    "One staff member settles more than the permitted share of the day's "
    "cash-tendered invoices."
)
DATASOURCES = ["ipos_sales"]

# One staff member may settle at most this share of the day's cash invoices.
SINGLE_STAFF_CASH_SHARE_LIMIT = 50.0

CASH_TENDER = "cash"

def run(ctx) -> dict:
    share_limit = SINGLE_STAFF_CASH_SHARE_LIMIT

    cash = ctx.table("ipos_sales").filter(
        (~pl.col("is_void")) & (pl.col("tender_type") == CASH_TENDER)
    )
    cash_invoices = cash.height

    if cash_invoices == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No cash invoices on {ctx.date}",
            "metrics": {},
            "rows": [],
        }

    leaders = (
        cash.group_by("staff_id")
        .agg(
            invoices=pl.len(),
            value=pl.col("invoice_value").sum(),
        )
        .with_columns(share_pct=100.0 * pl.col("invoices") / cash_invoices)
        .filter(pl.col("share_pct") > share_limit)
        .sort("invoices", descending=True)
    )

    if leaders.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": (
                f"No staff member settled more than {share_limit}% of the "
                f"{cash_invoices} cash invoices"
            ),
            "metrics": {"cash_invoices": cash_invoices, "share_limit": share_limit},
            "rows": [],
        }

    top = leaders.row(0, named=True)

    # Ticket rows = the cash invoices settled by the dominant staff member.
    rows = (
        cash.filter(pl.col("staff_id") == top["staff_id"])
        .sort("invoice_value", descending=True)
        .head(200)
        .select(
            "invoice_id", "invoice_ts", "store_id", "staff_id",
            "tender_type", "item_group", "invoice_value",
        )
        .to_dicts()
    )

    also = ", ".join(
        f"{r['staff_id']} ({r['share_pct']:.1f}%)"
        for r in leaders.iter_rows(named=True)
    )

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Staff {top['staff_id']} settled {top['invoices']} of "
            f"{cash_invoices} cash invoices ({top['share_pct']:.1f}%, "
            f"limit {share_limit}%) worth {top['value']:,.0f} AED. "
            f"Also above limit: {also}"
        ),
        "metrics": {
            "cash_invoices": cash_invoices,
            "share_limit": share_limit,
            "top_staff_id": top["staff_id"],
            "top_staff_invoices": top["invoices"],
            "top_staff_share_pct": round(top["share_pct"], 2),
            "staff_above_limit": leaders.height,
        },
        "rows": rows,
    }