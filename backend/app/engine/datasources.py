"""
Client entities.

Two names, derived from configuration. It used to also hold the datasource
registry - eighteen hardcoded table definitions - which made it a second source
of truth competing with the `datasources` table. An admin could retire a table
in the UI and the runner would still hand it to every rule, so the two had to
agree and nothing guaranteed they would.

The catalogue now lives in the database and is read through
`app.engine.catalog`. What remains here is client identity: who is being
audited and which upstream systems each draws from. That is configuration about
the business, not a list of tables, and it does not change when an admin
registers something - so it lives in `settings.client_entities`, and this module
is the one place that reads it. `ENTITIES` is what the rest of the backend asks
for; nothing else should reach into settings for a client name.

The defaults are a neutral demo estate. A deployment that names real businesses
sets CLIENT_ENTITIES in its .env and everything downstream follows, which is
what lets the product ship without a client's name in the source.

CROSS-SYSTEM KEYS
-----------------
Shared keys are what let a rule join across systems without a mapping layer:

    employee_id   HRMS   <->  POS (staff_id)
    store_id      all systems
    invoice_id    POS    <->  ERP posted invoices
    sku / item    POS    <->  warehouse inventory, recipes, wastage
    po_number     ERP purchase orders  <->  goods receipts
"""

from __future__ import annotations

from app.settings import settings

ENTITIES = {e.code: e.name for e in settings.client_entities}

SYSTEMS_BY_ENTITY = {e.code: list(e.systems) for e in settings.client_entities}


def default_entity() -> str:
    """The client to assume when a request did not name one.

    Endpoints take `entity` as a query parameter and need something to fall back
    to. Naming a literal there would put a client's identity into the API
    surface and make a deployment whose clients are named differently default
    to a client that does not exist - which is not a 404, it is an empty screen
    that reads as "no data yet".

    First configured client, always. Sorted, so the answer does not change
    because somebody reordered CLIENT_ENTITIES in the .env.
    """
    return sorted(ENTITIES)[0]