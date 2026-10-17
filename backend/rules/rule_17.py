def run(ctx):
    rows = ctx.sql('SELECT * FROM ctx.table("bc_vendors")').all()
    return {"status": "no_rows_returned", "rows": []}
