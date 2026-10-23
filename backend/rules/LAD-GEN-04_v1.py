import polars as pl

NAME = "Top 5 stores by net sales value"
DESCRIPTION = (
    "Ranks stores by total non-void invoice value for the audited day and "
    "returns the top 5 stores."
)
DATASOURCES = ["ipos_sales"]

# Number of stores reported in the ranking.
TOP_N_STORES = 5


def run(ctx) -> dict:
    sales = ctx.table("ipos_sales")

    if sales.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"No sales data for {ctx.date}",
            "metrics": {},
            "rows": [],
        }

    net = sales.filter(pl.col("is_void") == False)

    if net.height == 0:
        return {
            "status": "success",
            "error_message": None,
            "summary": f"All invoices on {ctx.date} are void; no store ranking available",
            "metrics": {},
            "rows": [],
        }

    ranked = (
        net.group_by("store_id")
        .agg(
            total_sales=pl.col("invoice_value").sum(),
            invoice_count=pl.len(),
        )
        .sort("total_sales", descending=True)
        .head(TOP_N_STORES)
        .with_columns(rank=pl.int_range(1, pl.len() + 1))
        .select("rank", "store_id", "total_sales", "invoice_count")
        .with_columns(total_sales=pl.col("total_sales").round(2))
    )

    rows = ranked.to_dicts()
    best = rows[0]
    summary = (
        f"Top {len(rows)} stores by net sales on {ctx.date}: "
        + ", ".join(f"{r['store_id']} {r['total_sales']:.2f}" for r in rows)
    )

    return {
        "status": "success",
        "error_message": None,
        "summary": summary,
        "metrics": {
            "top_store_id": str(best["store_id"]),
            "top_store_sales": float(best["total_sales"]),
            "stores_ranked": len(rows),
        },
        "rows": rows,
    }