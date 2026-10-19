"""
Client entities.

Two constants, and that is the whole module. It used to also hold the
datasource registry - eighteen hardcoded table definitions - which made it a
second source of truth competing with the `datasources` table. An admin could
retire a table in the UI and the runner would still hand it to every rule, so the
two had to agree and nothing guaranteed they would.

The catalogue now lives in the database and is read through
`app.engine.catalog`. What remains here is client identity: who the two tenants
are and which upstream systems each draws from. That is configuration about the
business, not a list of tables, and it does not change when an admin registers
something.

CROSS-SYSTEM KEYS
-----------------
Shared keys are what let a rule join across systems without a mapping layer:

    employee_id   HRMS  <->  iPOS (staff_id)
    store_id      all systems
    invoice_id    iPOS  <->  Business Central posted invoices
    sku / item    iPOS  <->  Synapse inventory, recipes, wastage
    po_number     Business Central POs <-> goods receipts
"""

from __future__ import annotations

ENTITIES = {
    "LAD": "Laduree",
    "ABM": "Al Boom Marine",
}

SYSTEMS_BY_ENTITY = {
    "LAD": ["iPOS (SaaS)", "Azure Synapse", "HRMS"],
    "ABM": ["iPOS", "HRMS", "Business Central"],
}