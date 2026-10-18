"""
Datasource registry - the "ready connections" to the CSV landing zone.

Two client entities, each with its own systems:

  LAD  Laduree        iPOS (SaaS)  |  Azure Synapse  |  HRMS
  ABM  Al Boom Marine iPOS          |  HRMS  |  Business Central

Each table is declared once: where its CSVs live, what the columns mean, what
grain the data is at. Rules never reference a file path - they call
ctx.table("ipos_sales") and get a table scoped to that rule's entity.

CROSS-SYSTEM KEYS are what make the data relatable. A rule can join across
systems because the keys are shared, not remapped:

  employee_id   HRMS  <->  iPOS (staff_id)
  store_id      all systems
  invoice_id    iPOS  <->  Business Central posted invoices
  sku / item    iPOS  <->  Synapse inventory, recipes, wastage
  po_number     Business Central POs <-> goods receipts
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    name: str
    entity: str
    system: str
    description: str
    grain: str
    columns: dict[str, str]

    @property
    def path_template(self) -> str:
        return f"SourceData/{self.entity}/{{date}}/{self.name}.csv"


# ---------------------------------------------------------------------------
# Laduree - iPOS (SaaS)
# ---------------------------------------------------------------------------
_LAD_IPOS = {
    "ipos_sales": Source(
        name="ipos_sales",
        entity="LAD",
        system="iPOS (SaaS)",
        description="Point-of-sale invoice header for Laduree stores.",
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
    "ipos_items": Source(
        name="ipos_items",
        entity="LAD",
        system="iPOS (SaaS)",
        description="Laduree selling-item master.",
        grain="one row per SKU",
        columns={
            "sku": "VARCHAR",            # joins to Synapse tables
            "item_name": "VARCHAR",
            "item_group": "VARCHAR",
            "recipe_id": "VARCHAR",      # joins to syn_recipes
            "unit_price": "DOUBLE",
            "is_active": "BOOLEAN",
        },
    ),
    "ipos_voids": Source(
        name="ipos_voids",
        entity="LAD",
        system="iPOS (SaaS)",
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
}

# ---------------------------------------------------------------------------
# Laduree - Azure Synapse (analytics / operations warehouse)
# ---------------------------------------------------------------------------
_LAD_SYNAPSE = {
    "syn_inventory": Source(
        name="syn_inventory",
        entity="LAD",
        system="Azure Synapse",
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
    "syn_recipes": Source(
        name="syn_recipes",
        entity="LAD",
        system="Azure Synapse",
        description="Recipe master. POS items with no active recipe are an audit exception.",
        grain="one row per recipe",
        columns={
            "recipe_id": "VARCHAR",
            "recipe_name": "VARCHAR",
            "is_active": "BOOLEAN",
            "last_reviewed": "DATE",
        },
    ),
    "syn_wastage": Source(
        name="syn_wastage",
        entity="LAD",
        system="Azure Synapse",
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
    "syn_stock_transfers": Source(
        name="syn_stock_transfers",
        entity="LAD",
        system="Azure Synapse",
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
}

# ---------------------------------------------------------------------------
# Laduree - HRMS
# ---------------------------------------------------------------------------
_LAD_HRMS = {
    "hr_employees": Source(
        name="hr_employees",
        entity="LAD",
        system="HRMS",
        description="Laduree employee master. Drives access reviews and payroll.",
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
    "hr_attendance": Source(
        name="hr_attendance",
        entity="LAD",
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
}


# ---------------------------------------------------------------------------
# Al Boom Marine - iPOS
# ---------------------------------------------------------------------------
_ABM_IPOS = {
    "ipos_sales": Source(
        name="ipos_sales",
        entity="ABM",
        system="iPOS",
        description="Al Boom Marine POS invoice header (retail stores).",
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
    "ipos_price_overrides": Source(
        name="ipos_price_overrides",
        entity="ABM",
        system="iPOS",
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
    "ipos_discounts": Source(
        name="ipos_discounts",
        entity="ABM",
        system="iPOS",
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
}

# ---------------------------------------------------------------------------
# Al Boom Marine - HRMS
# ---------------------------------------------------------------------------
_ABM_HRMS = {
    "hr_employees": Source(
        name="hr_employees",
        entity="ABM",
        system="HRMS",
        description="ABM employee master.",
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
    "hr_attendance": Source(
        name="hr_attendance",
        entity="ABM",
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
}

# ---------------------------------------------------------------------------
# Al Boom Marine - Business Central (ERP)
# ---------------------------------------------------------------------------
_ABM_BC = {
    "bc_purchase_orders": Source(
        name="bc_purchase_orders",
        entity="ABM",
        system="Business Central",
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
    "bc_goods_receipts": Source(
        name="bc_goods_receipts",
        entity="ABM",
        system="Business Central",
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
    "bc_vendors": Source(
        name="bc_vendors",
        entity="ABM",
        system="Business Central",
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
    "bc_customer_credit": Source(
        name="bc_customer_credit",
        entity="ABM",
        system="Business Central",
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
}


# ---------------------------------------------------------------------------
# Entity -> tables
# ---------------------------------------------------------------------------
REGISTRY_BY_ENTITY: dict[str, dict[str, Source]] = {
    "LAD": {**_LAD_IPOS, **_LAD_SYNAPSE, **_LAD_HRMS},
    "ABM": {**_ABM_IPOS, **_ABM_HRMS, **_ABM_BC},
}

ENTITIES = {
    "LAD": "Laduree",
    "ABM": "Al Boom Marine",
}

SYSTEMS_BY_ENTITY = {
    "LAD": ["iPOS (SaaS)", "Azure Synapse", "HRMS"],
    "ABM": ["iPOS", "HRMS", "Business Central"],
}


def registry_for(entity: str) -> dict[str, Source]:
    try:
        return REGISTRY_BY_ENTITY[entity.upper()]
    except KeyError as exc:
        raise KeyError(
            f"Unknown entity '{entity}'. Known: {', '.join(sorted(REGISTRY_BY_ENTITY))}"
        ) from exc


def get_source(entity: str, name: str) -> Source:
    reg = registry_for(entity)
    try:
        return reg[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown datasource '{name}' for entity {entity}. "
            f"Available: {', '.join(sorted(reg))}"
        ) from exc


def all_sources() -> list[Source]:
    out: list[Source] = []
    for entity in sorted(REGISTRY_BY_ENTITY):
        reg = REGISTRY_BY_ENTITY[entity]
        out.extend(reg[k] for k in sorted(reg))
    return out