"""
Seed data: the datasource catalogue a fresh database starts with.

This lives in scripts/, not app/, and that placement is the point. The running
app has exactly one registry - the `datasources` table. A module under app/ that
also answered "what tables exist" would be a second source of truth, and the two
would drift the first time an admin registered something.

`make_sample_data.py` imports SEED_SOURCES and writes them into the table. After
that an admin owns the catalogue entirely; this file is only ever read at seed
time.

Columns here are what the ETL is expected to send. They are NOT derived from the
CSVs on disk: a real deployment starts from this list and grows it as the admin
registers more, whereas discovery reads the other way round.
"""

from __future__ import annotations

from typing import TypedDict

from app.engine.datasources import ENTITIES

# The seed describes the DEMO estate - two clients with deliberately different
# upstream systems - but it must not hardcode who they are, or a deployment that
# configured its own clients would seed datasources belonging to nobody and the
# Data Sources screen would list tables no rule can ever reach.
#
# So the two demo estates are mapped positionally onto whatever CLIENT_ENTITIES
# names. The demo data generator writes the same two layouts, so the CSVs on disk
# and the registered tables always agree.
_CODES = sorted(ENTITIES)
if len(_CODES) < 2:
    raise SystemExit(
        "seed_datasources seeds a two-client demo estate. Set CLIENT_ENTITIES "
        f"to at least two clients; only {len(_CODES)} is configured."
    )
_FIRST, _SECOND = _CODES[0], _CODES[1]


class SeedSource(TypedDict):
    name: str
    entity: str
    system: str
    description: str
    grain: str
    columns: dict[str, str]


# ---------------------------------------------------------------------------
# First configured client - POS
# ---------------------------------------------------------------------------
_CLIENT_A = [
    SeedSource(
        name="ipos_sales",
        entity=_FIRST,
        system="POS",
        description="Point-of-sale invoice header.",
        grain="one row per invoice",
        columns={
            "invoice_id": "VARCHAR",
            "invoice_ts": "TIMESTAMP",
            "store_id": "VARCHAR",
            "staff_id": "VARCHAR",       # joins to hr_employees.employee_id
            "tender_type": "VARCHAR",
            "item_group": "VARCHAR",     # RETAIL | BAKERY | BAR
            "invoice_value": "DOUBLE",
            "is_void": "BOOLEAN",
            "guest_count": "INTEGER",
        },
    ),
    SeedSource(
        name="ipos_items",
        entity=_FIRST,
        system="POS",
        description="Selling-item master.",
        grain="one row per SKU",
        columns={
            "sku": "VARCHAR",
            "item_name": "VARCHAR",
            "item_group": "VARCHAR",
            "recipe_id": "VARCHAR",      # joins to syn_recipes
            "unit_price": "DOUBLE",
            "is_active": "BOOLEAN",
        },
    ),
    SeedSource(
        name="ipos_voids",
        entity=_FIRST,
        system="POS",
        description="Void and cancellation events, with reason and staff.",
        grain="one row per void",
        columns={
            "void_id": "VARCHAR",
            "invoice_id": "VARCHAR",     # joins to ipos_sales
            "store_id": "VARCHAR",
            "staff_id": "VARCHAR",
            "void_ts": "TIMESTAMP",
            "reason": "VARCHAR",
            "value": "DOUBLE",
            "after_print": "BOOLEAN",
        },
    ),
]

# ---------------------------------------------------------------------------
# First configured client - Warehouse (analytics / operations)
# ---------------------------------------------------------------------------
_CLIENT_A_WHAPSE = [
    SeedSource(
        name="syn_inventory",
        entity=_FIRST,
        system="Warehouse",
        description="Store-level stock position and ageing.",
        grain="one row per store per SKU per snapshot",
        columns={
            "store_id": "VARCHAR",
            "sku": "VARCHAR",            # joins to ipos_items.sku
            "on_hand_qty": "DOUBLE",
            "unit_cost": "DOUBLE",
            "last_movement_ts": "TIMESTAMP",
            "age_days": "INTEGER",
        },
    ),
    SeedSource(
        name="syn_recipes",
        entity=_FIRST,
        system="Warehouse",
        description="Recipe master. POS items with no active recipe are an audit exception.",
        grain="one row per recipe",
        columns={
            "recipe_id": "VARCHAR",
            "recipe_name": "VARCHAR",
            "is_active": "BOOLEAN",
            "last_reviewed": "DATE",
        },
    ),
    SeedSource(
        name="syn_wastage",
        entity=_FIRST,
        system="Warehouse",
        description="Wastage postings by store, item, reason and employee.",
        grain="one row per wastage entry",
        columns={
            "wastage_id": "VARCHAR",
            "store_id": "VARCHAR",
            "sku": "VARCHAR",
            "staff_id": "VARCHAR",       # joins to hr_employees.employee_id
            "wastage_ts": "TIMESTAMP",
            "reason": "VARCHAR",
            "qty": "DOUBLE",
            "value": "DOUBLE",
        },
    ),
    SeedSource(
        name="syn_stock_transfers",
        entity=_FIRST,
        system="Warehouse",
        description="Inter-store and warehouse stock transfers.",
        grain="one row per transfer line",
        columns={
            "transfer_id": "VARCHAR",
            "from_store": "VARCHAR",
            "to_store": "VARCHAR",
            "sku": "VARCHAR",
            "qty": "DOUBLE",
            "dispatched_ts": "TIMESTAMP",
            "received_ts": "TIMESTAMP",
        },
    ),
]

# ---------------------------------------------------------------------------
# First configured client - HRMS
# ---------------------------------------------------------------------------
_CLIENT_A_HR = [
    SeedSource(
        name="hr_employees",
        entity=_FIRST,
        system="HRMS",
        description="Employee master. Drives access reviews and payroll.",
        grain="one row per employee",
        columns={
            "employee_id": "VARCHAR",
            "full_name": "VARCHAR",
            "nationality": "VARCHAR",
            "department": "VARCHAR",
            "designation": "VARCHAR",
            "home_store": "VARCHAR",     # joins to store_id
            "employment_status": "VARCHAR",
            "exit_date": "DATE",
            "monthly_salary": "DOUBLE",
            "bank_iban": "VARCHAR",
        },
    ),
    SeedSource(
        name="hr_attendance",
        entity=_FIRST,
        system="HRMS",
        description="Daily attendance and store open/close punches.",
        grain="one row per employee per day",
        columns={
            "employee_id": "VARCHAR",
            "store_id": "VARCHAR",
            "work_date": "DATE",
            "clock_in": "TIMESTAMP",
            "clock_out": "TIMESTAMP",
            "eod_closing": "BOOLEAN",
            "was_regularised": "BOOLEAN",
            "overtime_hours": "DOUBLE",
        },
    ),
]

# ---------------------------------------------------------------------------
# Second configured client - POS
# ---------------------------------------------------------------------------
_CLIENT_B_POS = [
    SeedSource(
        name="ipos_sales",
        entity=_SECOND,
        system="POS",
        description="POS invoice header (retail stores).",
        grain="one row per invoice",
        columns={
            "invoice_id": "VARCHAR",
            "invoice_ts": "TIMESTAMP",
            "store_id": "VARCHAR",
            "staff_id": "VARCHAR",       # joins to hr_employees.employee_id
            "tender_type": "VARCHAR",
            "item_group": "VARCHAR",
            "invoice_value": "DOUBLE",
            "is_void": "BOOLEAN",
        },
    ),
    SeedSource(
        name="ipos_price_overrides",
        entity=_SECOND,
        system="POS",
        description="Manual price override events applied at the till.",
        grain="one row per override",
        columns={
            "override_id": "VARCHAR",
            "invoice_id": "VARCHAR",
            "store_id": "VARCHAR",
            "staff_id": "VARCHAR",
            "sku": "VARCHAR",
            "override_ts": "TIMESTAMP",
            "list_price": "DOUBLE",
            "sold_price": "DOUBLE",
            "quantity": "INTEGER",
        },
    ),
    SeedSource(
        name="ipos_discounts",
        entity=_SECOND,
        system="POS",
        description="Discount and promotion lines applied at the till.",
        grain="one row per discount line",
        columns={
            "discount_id": "VARCHAR",
            "invoice_id": "VARCHAR",
            "store_id": "VARCHAR",
            "staff_id": "VARCHAR",
            "sku": "VARCHAR",
            "discount_ts": "TIMESTAMP",
            "discount_type": "VARCHAR",  # STAFF | CORPORATE | PROMOTION
            "discount_value": "DOUBLE",
        },
    ),
]

# ---------------------------------------------------------------------------
# Second configured client - HRMS
# ---------------------------------------------------------------------------
_CLIENT_B_HR = [
    SeedSource(
        name="hr_employees",
        entity=_SECOND,
        system="HRMS",
        description="Employee master.",
        grain="one row per employee",
        columns={
            "employee_id": "VARCHAR",
            "full_name": "VARCHAR",
            "nationality": "VARCHAR",
            "department": "VARCHAR",
            "designation": "VARCHAR",
            "home_store": "VARCHAR",
            "employment_status": "VARCHAR",
            "exit_date": "DATE",
            "monthly_salary": "DOUBLE",
            "bank_iban": "VARCHAR",      # joins to bc_vendors.vendor_bank_iban
            "bank_account_name": "VARCHAR",
        },
    ),
    SeedSource(
        name="hr_attendance",
        entity=_SECOND,
        system="HRMS",
        description="Biometric attendance and EOD closing participation.",
        grain="one row per employee per day",
        columns={
            "employee_id": "VARCHAR",
            "store_id": "VARCHAR",
            "work_date": "DATE",
            "clock_in": "TIMESTAMP",
            "clock_out": "TIMESTAMP",
            "eod_closing": "BOOLEAN",
            "was_regularised": "BOOLEAN",
            "overtime_hours": "DOUBLE",
        },
    ),
]

# ---------------------------------------------------------------------------
# Second configured client - ERP
# ---------------------------------------------------------------------------
_CLIENT_B_ERP = [
    SeedSource(
        name="bc_purchase_orders",
        entity=_SECOND,
        system="ERP",
        description="Purchase order header.",
        grain="one row per purchase order",
        columns={
            "po_number": "VARCHAR",
            "vendor_id": "VARCHAR",      # joins to bc_vendors.vendor_id
            "po_date": "DATE",
            "store_id": "VARCHAR",
            "po_value": "DOUBLE",
            "approved_by": "VARCHAR",    # employee_id
            "po_status": "VARCHAR",
        },
    ),
    SeedSource(
        name="bc_goods_receipts",
        entity=_SECOND,
        system="ERP",
        description="Goods received notes. Un-invoiced GRNs are an audit exception.",
        grain="one row per goods receipt",
        columns={
            "grn_number": "VARCHAR",
            "po_number": "VARCHAR",      # joins to bc_purchase_orders.po_number
            "vendor_id": "VARCHAR",
            "grn_date": "DATE",
            "store_id": "VARCHAR",
            "grn_value": "DOUBLE",
            "quantity_received": "INTEGER",
            "invoiced": "BOOLEAN",
        },
    ),
    SeedSource(
        name="bc_vendors",
        entity=_SECOND,
        system="ERP",
        description="Vendor master with bank details - the ERP side of the "
                    "vendor/employee bank match control.",
        grain="one row per vendor",
        columns={
            "vendor_id": "VARCHAR",
            "vendor_name": "VARCHAR",
            "vendor_bank_iban": "VARCHAR",
            "vendor_account_name": "VARCHAR",
            "vendor_address": "VARCHAR",
            "vendor_phone": "VARCHAR",
            "is_active": "BOOLEAN",
        },
    ),
    SeedSource(
        name="bc_customer_credit",
        entity=_SECOND,
        system="ERP",
        description="Distribution customer credit limits and AR ageing.",
        grain="one row per customer",
        columns={
            "customer_id": "VARCHAR",
            "customer_name": "VARCHAR",
            "country": "VARCHAR",
            "credit_limit": "DOUBLE",
            "ar_days": "INTEGER",
            "outstanding_amount": "DOUBLE",
        },
    ),
]

SEED_SOURCES: list[SeedSource] = [
    *_CLIENT_A, *_CLIENT_A_WHAPSE, *_CLIENT_A_HR,
    *_CLIENT_B_POS, *_CLIENT_B_HR, *_CLIENT_B_ERP,
]