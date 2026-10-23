import polars as pl

NAME = "Top 5 stores by daily sales value"
DESCRIPTION = (
    "Ranks stores by total non-void invoice value for the audited day and "
    "reports the top 5 stores with their net sales value."
)
DATASOURCES = ["ipos_sales"]

# Number of stores reported in the daily sales ranking.
TOP_N_STORES = 5

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def run(ctx) -> dict:
    sales = ctx.table("ipos_sales")

    if sales.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No sales data staged for {ctx.date}",
            "metrics": {},
            "rows": [],
        }

    valid = sales.filter(pl.col("is_void") == False).with_columns(
        sale_date=pl.col("invoice_ts").str.to_datetime(TS_FORMAT, strict=False).dt.date()
    )

    if valid.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No non-void invoices for {ctx.date}",
            "metrics": {},
            "rows": [],
        }

    ranked = (
        valid.group_by("store_id")
        .agg(
            sales_value=pl.col("invoice_value").sum(),
            invoice_count=pl.len(),
        )
        .sort("sales_value", descending=True)
    )

    top = ranked.head(TOP_N_STORES)

    rows = []
    for i, r in enumerate(top.to_dicts(), start=1):
        rows.append(
            {
                "rank": i,
                "store_id": str(r["store_id"]),
                "sales_value": round(float(r["sales_value"]), 2),
                "invoice_count": int(r["invoice_count"]),
            }
        )

    total_value = float(ranked["sales_value"].sum() or 0)
    top_value = float(top["sales_value"].sum() or 0)
    share = (top_value / total_value * 100) if total_value else 0.0

    return {
        "status": "success",
        "error_message": None,
        "summary": (
            f"Top {len(rows)} stores on {ctx.date} account for "
            f"{top_value:.2f} AED of {total_value:.2f} AED total non-void sales "
            f"({share:.1f}%); leader {rows[0]['store_id']} at {rows[0]['sales_value']:.2f} AED"
        ),
        "metrics": {
            "stores_ranked": int(ranked.height),
            "top_n": TOP_N_STORES,
            "top_stores_value": round(top_value, 2),
            "total_sales_value": round(total_value, 2),
            "top_stores_share_pct": round(share, 2),
            "invoices_considered": int(valid.height),
            "voids_excluded": int(sales.filter(pl.col("is_void") == True).height),
        },
        "rows": rows,
    }