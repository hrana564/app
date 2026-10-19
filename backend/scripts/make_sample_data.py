"""
Generate the SourceData CSV landing zone for both client entities.

Layout:  SourceData/<ENTITY>/<YYYY-MM-DD>/<table>.csv

  first configured client    POS | Warehouse | HRMS
  second configured client   POS | HRMS      | ERP

Deterministic (seeded per day) so rule behaviour is reproducible. Specific days
carry deliberately planted breaches, otherwise you cannot tell a working rule
from a broken one.

The data is deliberately RELATABLE across systems - shared keys, not remapped
ones:

  employee_id   HRMS.employee_id  ==  POS.staff_id  ==  HRMS.approved_by
  store_id      consistent across POS / Warehouse / HRMS / ERP
  sku           POS.sku == Warehouse.sku
  po_number     bc_purchase_orders.po_number == bc_goods_receipts.po_number
  iban          hr_employees.bank_iban can collide with bc_vendors.vendor_bank_iban

That is what lets one rule join three systems.
"""

from __future__ import annotations

import csv
import hashlib
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from faker import Faker

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_DIR.parent
SOURCEDATA_DIR = APP_ROOT / "mount" / "SourceData"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

# Client identity is configuration, so this reads it rather than assuming it.
# `app.settings` is the only backend module pulled in here - it reads the .env
# and builds no engine. The catalogue seeder below still imports app.db and the
# ORM models, and that stays inside seed_catalog() because those DO open the
# database at import time.
from app.engine.datasources import ENTITIES  # noqa: E402

# Two demo estates, mapped onto whatever CLIENT_ENTITIES the deployment
# configured - the same positional mapping the catalogue seeder uses, so the
# CSVs written here always land in a folder the registered datasources expect.
# Writing a folder named after a client the deployment never configured would
# produce data that discovery finds and no rule can reach.
#
# In DECLARATION ORDER, not sorted - the same reason, and the same mapping.
# Which estate a client gets is the deployment's decision, and sorting made it
# alphabetical by accident.
_CODES = list(ENTITIES)
if len(_CODES) < 2:
    raise SystemExit(
        "make_sample_data generates a two-client demo estate. Set "
        f"CLIENT_ENTITIES to at least two clients; only {len(_CODES)} is "
        f"configured."
    )
_FIRST, _SECOND = _CODES[0], _CODES[1]

DAYS = [
    date(2026, 10, 15),
    date(2026, 10, 16),
    date(2026, 10, 17),
    date(2026, 10, 18),
    date(2026, 10, 19),
    date(2026, 10, 20),
]

A_STORES = ["LD-101", "LD-102", "LD-103", "LD-104", "LD-105", "LD-106"]
B_STORES = ["ST-101", "ST-102", "ST-103", "ST-104", "ST-105"]

A_ITEM_GROUPS = ["RETAIL", "BAKERY", "BAR"]
B_ITEM_GROUPS = ["RETAIL", "GROCERY", "ELECTRONICS"]

TRADING_START_HOUR = 10
TRADING_END_HOUR = 23  # stores trade 10:00-23:59, so "last hour" = hour 23

NATIONALITIES = ["UAE", "SAU", "IND", "PHL", "EGY", "GBR", "FRA"]
WASTAGE_REASONS = ["SPOILAGE", "PREPARATION", "QUALITY", "OVERPRODUCTION", "DAMAGE"]

# day -> scenario label, per entity
SCENARIO = {
    (_FIRST, date(2026, 10, 15)): "clean",
    (_FIRST, date(2026, 10, 16)): "last_hour_spike",
    (_FIRST, date(2026, 10, 17)): "single_staff_cash",
    (_FIRST, date(2026, 10, 18)): "both_breaches",
    (_FIRST, date(2026, 10, 19)): "clean",
    (_FIRST, date(2026, 10, 20)): "clean",
    (_SECOND, date(2026, 10, 15)): "bank_match",
    (_SECOND, date(2026, 10, 16)): "bank_match",
    (_SECOND, date(2026, 10, 17)): "bank_match",
    (_SECOND, date(2026, 10, 18)): "bank_match",
    (_SECOND, date(2026, 10, 19)): "bank_match",
    (_SECOND, date(2026, 10, 20)): "bank_match",
}


def scenario_for(entity: str, day: date) -> str:
    return SCENARIO.get((entity, day), "clean")


def rng_for(entity: str, day: date) -> random.Random:
    return random.Random(f"{entity}{day:%Y%m%d}")


# ---------------------------------------------------------------------------
# Realistic names
#
# An earlier version wrote "First Staff 0042" and "Second Employee 0007" as
# names. Every one of those differs from every other by a single digit, which
# scores 93% under character-similarity - so the CA-22 name check flagged
# 87 unrelated pairs and the fixture could not distinguish a working control
# from a broken one. Real names put the highest-scoring unrelated pair at 81%.
#
# Faker is seeded per key so the same person gets the same name on every
# regeneration: the fixture stays stable across runs, which is what makes the
# planted breaches assertable.
# ---------------------------------------------------------------------------
_NAME_HONORIFICS = ("Mr ", "Mrs ", "Ms ", "Miss ", "Dr ", "Prof ")


def _person_name(rng: random.Random, key: str) -> str:
    """A stable, realistic person name for `key`."""
    fake = _faker_for(key)
    name = fake.name().strip()
    for prefix in _NAME_HONORIFICS:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name


def _faker_for(key: str) -> Faker:
    faker = Faker("en_GB")
    faker.seed_instance(_stable_seed(key))
    return faker


def _stable_seed(key: str) -> int:
    """A seed derived from `key`, stable across processes and platforms.

    `hash()` is salted per process, so it cannot be used here - regenerating
    the fixture tomorrow must produce yesterday's names.
    """
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big")


# ---------------------------------------------------------------------------
# First configured client
# ---------------------------------------------------------------------------
def a_employees(rng: random.Random) -> list[dict]:
    """Employee master. Written to every day folder - it is master data."""
    rows = []
    for i in range(1, 61):
        emp_id = f"EMP-L{i:04d}"
        exited = i >= 55  # last six have left
        rows.append(
            {
                "employee_id": emp_id,
                "full_name": _person_name(rng, f"{_FIRST}-{emp_id}"),
                "nationality": rng.choice(NATIONALITIES),
                "department": rng.choice(["Retail Ops", "Kitchen", "Bar", "Supply Chain"]),
                "designation": rng.choice(
                    ["Barista", "Chef", "Shift Supervisor", "Store Manager", "Packer"]
                ),
                "home_store": rng.choice(A_STORES),
                "employment_status": "exited" if exited else "active",
                "exit_date": (date(2026, 9, 30).isoformat() if exited else ""),
                "monthly_salary": f"{rng.randrange(4500, 24000)}",
                "bank_iban": _iban(rng, f"AE{i:06d}"),
            }
        )
    return rows


def a_items(rng: random.Random) -> list[dict]:
    rows = []
    groups = ["RETAIL", "BAKERY", "BAR"]
    for i in range(1, 41):
        sku = f"LD-SKU-{i:04d}"
        group = rng.choices(groups, weights=[46, 32, 22], k=1)[0]
        # Most items have an active recipe; a handful deliberately do not.
        has_recipe = rng.random() > 0.12
        rows.append(
            {
                "sku": sku,
                "item_name": f"Item {i:03d}",
                "item_group": group,
                "recipe_id": (f"RCP-{i:04d}" if has_recipe else ""),
                "unit_price": f"{round(rng.lognormvariate(3.5, 0.5), 2)}",
                "is_active": "true",
            }
        )
    return rows


def a_sales(day: date, rng: random.Random, employees: list[dict]) -> list[dict]:
    scenario = scenario_for(_FIRST, day)
    hours = list(range(TRADING_START_HOUR, TRADING_END_HOUR + 1))
    base_counts = {h: rng.randint(24, 32) for h in hours}

    # --- plant the last-hour spike (CA-03) -----------------------------
    if scenario in ("last_hour_spike", "both_breaches"):
        base_counts[TRADING_END_HOUR] = rng.randint(160, 180)

    live = [e for e in employees if e["employment_status"] == "active"]
    dominant = rng.choice(live)["employee_id"]
    dominant_share = 0.62

    rows: list[dict] = []
    seq = 0
    for hour in hours:
        for _ in range(base_counts[hour]):
            seq += 1
            group = rng.choices(A_ITEM_GROUPS, weights=[46, 32, 22], k=1)[0]
            tender = rng.choices(["card", "cash", "credit"], weights=[64, 26, 10], k=1)[0]
            value = round(rng.lognormvariate(4.6, 0.7), 2)

            # --- plant the single-staff cash dominance (CA-04) ---------
            if (
                scenario in ("single_staff_cash", "both_breaches")
                and tender == "cash"
                and rng.random() < dominant_share
            ):
                staff_id = dominant
                value = round(rng.lognormvariate(6.2, 0.5), 2)
            else:
                staff_id = rng.choice(live)["employee_id"]

            ts = datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)))
            rows.append(
                {
                    "invoice_id": f"{_FIRST}-{day:%Y%m%d}-{seq:06d}",
                    "invoice_ts": ts.isoformat(sep=" "),
                    "store_id": rng.choice(A_STORES),
                    "staff_id": staff_id,
                    "tender_type": tender,
                    "item_group": group,
                    "invoice_value": f"{value:.2f}",
                    "is_void": "false",
                    "guest_count": str(rng.randint(1, 6)),
                }
            )

    for row in rng.sample(rows, k=max(2, len(rows) // 100)):
        row["is_void"] = "true"

    # A few invoices from an employee who has already exited - CA-06.
    # Only planted on two days so the control has clean days to stay quiet on.
    exited = [e for e in employees if e["employment_status"] == "exited"]
    plant_ex_staff = day in (date(2026, 10, 15), date(2026, 10, 18))
    for _ in range(rng.randint(2, 4) if plant_ex_staff else 0):
        if not exited:
            break
        seq += 1
        ts = datetime.combine(day, time(rng.randrange(10, 24), rng.randrange(60)))
        rows.append(
            {
                "invoice_id": f"{_FIRST}-{day:%Y%m%d}-{seq:06d}",
                "invoice_ts": ts.isoformat(sep=" "),
                "store_id": rng.choice(A_STORES),
                "staff_id": rng.choice(exited)["employee_id"],
                "tender_type": "card",
                "item_group": "RETAIL",
                "invoice_value": f"{round(rng.lognormvariate(4.2, 0.5), 2):.2f}",
                "is_void": "false",
                "guest_count": str(rng.randint(1, 3)),
            }
        )

    return rows


def a_voids(day: date, rng: random.Random, sales: list[dict], employees: list[dict]) -> list[dict]:
    live = [e["employee_id"] for e in employees if e["employment_status"] == "active"]
    rows = []
    for i in range(rng.randint(12, 22)):
        base = rng.choice(sales)
        rows.append(
            {
                "void_id": f"LVOID-{day:%Y%m%d}-{i + 1:04d}",
                "invoice_id": base["invoice_id"],
                "store_id": base["store_id"],
                "staff_id": rng.choice(live),
                "void_ts": datetime.combine(
                    day, time(rng.randrange(10, 24), rng.randrange(60))
                ).isoformat(sep=" "),
                "reason": rng.choice(["WRONG_ITEM", "CUSTOMER_REQUEST", "TILL_ERROR", "TRAINING"]),
                "value": f"{round(rng.lognormvariate(4.0, 0.6), 2):.2f}",
                "after_print": str(rng.random() < 0.3).lower(),
            }
        )
    return rows


def a_recipes(rng: random.Random, items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        if not item["recipe_id"]:
            continue
        rows.append(
            {
                "recipe_id": item["recipe_id"],
                "recipe_name": f"Recipe for {item['item_name']}",
                "is_active": str(rng.random() > 0.15).lower(),
                "last_reviewed": (day_before(rng)).isoformat(),
            }
        )
    return rows


def a_inventory(day: date, rng: random.Random, items: list[dict]) -> list[dict]:
    rows = []
    for store in A_STORES:
        for item in rng.sample(items, k=min(12, len(items))):
            qty = round(rng.uniform(0, 140), 1)
            rows.append(
                {
                    "store_id": store,
                    "sku": item["sku"],
                    "on_hand_qty": f"{qty}",
                    "unit_cost": item["unit_price"],
                    "last_movement_ts": datetime.combine(
                        day - timedelta(days=rng.randint(0, 40)),
                        time(rng.randrange(8, 20), rng.randrange(60)),
                    ).isoformat(sep=" "),
                    "age_days": str(rng.randint(0, 180)),
                }
            )
    return rows


def a_wastage(day: date, rng: random.Random, items: list[dict], employees: list[dict]) -> list[dict]:
    live = [e["employee_id"] for e in employees if e["employment_status"] == "active"]
    rows = []
    for i in range(rng.randint(18, 30)):
        item = rng.choice(items)
        qty = round(rng.uniform(0.5, 12), 1)
        rows.append(
            {
                "wastage_id": f"WST-{day:%Y%m%d}-{i + 1:04d}",
                "store_id": rng.choice(A_STORES),
                "sku": item["sku"],
                "staff_id": rng.choice(live),
                "wastage_ts": datetime.combine(
                    day, time(rng.randrange(9, 23), rng.randrange(60))
                ).isoformat(sep=" "),
                "reason": rng.choice(WASTAGE_REASONS),
                "qty": f"{qty}",
                "value": f"{round(qty * float(item['unit_price']), 2):.2f}",
            }
        )
    return rows


def a_stock_transfers(day: date, rng: random.Random, items: list[dict]) -> list[dict]:
    rows = []
    for i in range(rng.randint(10, 18)):
        dispatched = day - timedelta(days=rng.randint(0, 9))
        # A few transfers sit in transit far beyond the 5-day control threshold.
        if rng.random() < 0.22:
            transit = rng.randint(6, 14)
        else:
            transit = rng.randint(1, 4)
        rows.append(
            {
                "transfer_id": f"TRF-{day:%Y%m%d}-{i + 1:04d}",
                "from_store": rng.choice(A_STORES),
                "to_store": rng.choice(A_STORES),
                "sku": rng.choice(items)["sku"],
                "qty": f"{round(rng.uniform(5, 90), 1)}",
                "dispatched_ts": datetime.combine(
                    dispatched, time(rng.randrange(8, 18), rng.randrange(60))
                ).isoformat(sep=" "),
                "received_ts": datetime.combine(
                    dispatched + timedelta(days=transit),
                    time(rng.randrange(8, 18), rng.randrange(60)),
                ).isoformat(sep=" "),
            }
        )
    return rows


def a_attendance(day: date, rng: random.Random, employees: list[dict]) -> list[dict]:
    live = [e for e in employees if e["employment_status"] == "active"]
    rows = []
    for emp in live:
        if rng.random() < 0.18:
            continue  # no-show
        store = emp["home_store"]
        rows.append(
            {
                "employee_id": emp["employee_id"],
                "store_id": store,
                "work_date": day.isoformat(),
                "clock_in": datetime.combine(day, time(9, rng.randrange(60))).isoformat(sep=" "),
                "clock_out": datetime.combine(day, time(23, rng.randrange(60))).isoformat(sep=" "),
                "eod_closing": str(rng.random() < 0.22).lower(),
                "was_regularised": str(rng.random() < 0.18).lower(),
                "overtime_hours": f"{round(rng.uniform(0, 4.5), 1)}",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Second configured client
# ---------------------------------------------------------------------------
def b_employees(rng: random.Random, shared_ibans: list[str]) -> list[dict]:
    rows = []
    for i in range(1, 71):
        emp_id = f"EMP-A{i:04d}"
        exited = i >= 66
        # The first three employees deliberately share a bank account with a
        # vendor - that is the CA-22 / CA-23 control breach.
        iban = shared_ibans[i - 1] if i <= len(shared_ibans) else _iban(rng, f"AE{i:06d}")
        rows.append(
            {
                "employee_id": emp_id,
                "full_name": _person_name(rng, f"{_SECOND}-{emp_id}"),
                "nationality": rng.choice(NATIONALITIES),
                "department": rng.choice(
                    ["Retail", "Distribution", "Finance", "HR", "Buying", "E-Commerce"]
                ),
                "designation": rng.choice(
                    ["Sales Associate", "Store Supervisor", "Accountant", "Buyer", "Store Manager"]
                ),
                "home_store": rng.choice(B_STORES),
                "employment_status": "exited" if exited else "active",
                "exit_date": (date(2026, 9, 30).isoformat() if exited else ""),
                "monthly_salary": f"{rng.randrange(4200, 26000)}",
                "bank_iban": iban,
                "bank_account_name": _person_name(rng, f"{_SECOND}-{emp_id}"),
            }
        )
    return rows


def b_vendors(rng: random.Random, shared_ibans: list[str],
                b_emp: list[dict]) -> list[dict]:
    rows = []
    for i in range(1, 19):
        vendor_id = f"VND-A{i:04d}"
        fake = _faker_for(f"VND-{vendor_id}")
        # The first three vendors are held in the bank accounts of the first
        # three employees: that is the CA-22 / CA-23 control breach,
        # and it is planted on BOTH the IBAN and the account holder name so
        # either detection path finds it.
        breach = i <= len(shared_ibans)
        iban = shared_ibans[i - 1] if breach else _iban(rng, f"AE5{i:05d}")
        account_name = (
            b_emp[i - 1]["bank_account_name"] if breach else fake.company()
        )
        rows.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": fake.company(),
                "vendor_bank_iban": iban,
                "vendor_account_name": account_name,
                "vendor_address": (
                    f"Warehouse {rng.randrange(1, 90)}, Dubai, UAE"
                ),
                "vendor_phone": f"+9715{rng.randrange(10000000, 99999999)}",
                "is_active": str(rng.random() > 0.12).lower(),
            }
        )
    return rows


def b_sales(day: date, rng: random.Random, employees: list[dict]) -> list[dict]:
    live = [e["employee_id"] for e in employees if e["employment_status"] == "active"]
    hours = list(range(TRADING_START_HOUR, TRADING_END_HOUR + 1))
    rows, seq = [], 0
    for hour in hours:
        for _ in range(rng.randint(18, 28)):
            seq += 1
            rows.append(
                {
                    "invoice_id": f"{_SECOND}-{day:%Y%m%d}-{seq:06d}",
                    "invoice_ts": datetime.combine(
                        day, time(hour, rng.randrange(60), rng.randrange(60))
                    ).isoformat(sep=" "),
                    "store_id": rng.choice(B_STORES),
                    "staff_id": rng.choice(live),
                    "tender_type": rng.choices(["card", "cash", "credit"], weights=[58, 32, 10], k=1)[0],
                    "item_group": rng.choices(B_ITEM_GROUPS, weights=[62, 24, 14], k=1)[0],
                    "invoice_value": f"{round(rng.lognormvariate(5.5, 0.65), 2)}",
                    "is_void": "false",
                }
            )
    for row in rng.sample(rows, k=max(1, len(rows) // 110)):
        row["is_void"] = "true"
    return rows


def b_price_overrides(day: date, rng: random.Random, sales: list[dict], employees: list[dict]) -> list[dict]:
    live = [e["employee_id"] for e in employees if e["employment_status"] == "active"]
    rows = []
    for i in range(rng.randint(22, 38)):
        list_price = round(rng.uniform(50, 900), 2)
        drop = rng.choice([0.05, 0.10, 0.20, 0.35, 0.50, 0.65])
        rows.append(
            {
                "override_id": f"POV-{day:%Y%m%d}-{i + 1:04d}",
                "invoice_id": rng.choice(sales)["invoice_id"] if sales else "",
                "store_id": rng.choice(B_STORES),
                "staff_id": rng.choice(live),
                "sku": f"{_SECOND}-SKU-{rng.randrange(1000, 9999)}",
                "override_ts": datetime.combine(
                    day, time(rng.randrange(10, 24), rng.randrange(60))
                ).isoformat(sep=" "),
                "list_price": f"{list_price:.2f}",
                "sold_price": f"{round(list_price * (1 - drop), 2):.2f}",
                "quantity": str(rng.choice([1, 1, 1, 2, 3, 4, 6])),
            }
        )
    return rows


def b_discounts(day: date, rng: random.Random, sales: list[dict], employees: list[dict]) -> list[dict]:
    live = [e["employee_id"] for e in employees if e["employment_status"] == "active"]
    rows = []
    for i in range(rng.randint(30, 50)):
        rows.append(
            {
                "discount_id": f"DSC-{day:%Y%m%d}-{i + 1:04d}",
                "invoice_id": rng.choice(sales)["invoice_id"] if sales else "",
                "store_id": rng.choice(B_STORES),
                "staff_id": rng.choice(live),
                "sku": f"{_SECOND}-SKU-{rng.randrange(1000, 9999)}",
                "discount_ts": datetime.combine(
                    day, time(rng.randrange(10, 24), rng.randrange(60))
                ).isoformat(sep=" "),
                "discount_type": rng.choices(
                    ["STAFF", "CORPORATE", "PROMOTION"], weights=[40, 25, 35], k=1
                )[0],
                "discount_value": f"{round(rng.uniform(5, 120), 2)}",
            }
        )
    return rows


def b_procurement(day: date, rng: random.Random, vendors: list[dict]) -> tuple[list[dict], list[dict]]:
    employees = [f"EMP-A{rng.randrange(1, 66):04d}" for _ in range(1)]
    pos_rows, grn_rows = [], []
    for i in range(rng.randint(30, 44)):
        po_number = f"PO-{day:%Y%m%d}-{i + 1:04d}"
        vendor = rng.choice(vendors)
        po_value = round(rng.lognormvariate(9.4, 0.85), 2)
        pos_rows.append(
            {
                "po_number": po_number,
                "vendor_id": vendor["vendor_id"],
                "po_date": day.isoformat(),
                "store_id": rng.choice(B_STORES),
                "po_value": f"{po_value:.2f}",
                "approved_by": rng.choice(employees),
                "po_status": rng.choice(["OPEN", "RECEIVED", "CLOSED"]),
            }
        )
        # ~72% received same day; some stay open past the 7-day control threshold.
        if rng.random() < 0.72:
            grn_rows.append(
                {
                    "grn_number": f"GRN-{day:%Y%m%d}-{i + 1:04d}",
                    "po_number": po_number,
                    "vendor_id": vendor["vendor_id"],
                    "grn_date": day.isoformat(),
                    "store_id": pos_rows[-1]["store_id"],
                    "grn_value": f"{round(po_value * rng.uniform(0.85, 1.02), 2):.2f}",
                    "quantity_received": str(rng.randint(5, 400)),
                    "invoiced": str(rng.random() < 0.8).lower(),
                }
            )
    return pos_rows, grn_rows


def b_customer_credit(rng: random.Random) -> list[dict]:
    rows = []
    for i in range(1, 25):
        rows.append(
            {
                "customer_id": f"CUS-A{i:04d}",
                "customer_name": f"Distribution Customer {i:03d}",
                "country": rng.choice(["UAE", "SAU", "OMN", "KWT", "QAT", "BHR"]),
                "credit_limit": f"{round(rng.choice([50000, 250000, 3000000, 750000]), 2)}",
                "ar_days": str(rng.randint(0, 75)),
                "outstanding_amount": f"{round(rng.lognormvariate(11.2, 0.9), 2)}",
            }
        )
    return rows


def b_attendance(day: date, rng: random.Random, employees: list[dict]) -> list[dict]:
    live = [e for e in employees if e["employment_status"] == "active"]
    rows = []
    for emp in live:
        if rng.random() < 0.2:
            continue
        rows.append(
            {
                "employee_id": emp["employee_id"],
                "store_id": emp["home_store"],
                "work_date": day.isoformat(),
                "clock_in": datetime.combine(day, time(9, rng.randrange(60))).isoformat(sep=" "),
                "clock_out": datetime.combine(day, time(22, rng.randrange(60))).isoformat(sep=" "),
                "eod_closing": str(rng.random() < 0.25).lower(),
                "was_regularised": str(rng.random() < 0.15).lower(),
                "overtime_hours": f"{round(rng.uniform(0, 5), 1)}",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _iban(rng: random.Random, seed: str) -> str:
    body = "".join(str(rng.randrange(10)) for _ in range(14))
    return f"AE{abs(hash(seed + body)) % 10**16:016d}"


def day_before(rng: random.Random) -> date:
    return date(2026, 9, 1) + timedelta(days=rng.randint(0, 120))


def _write(entity: str, day: date, table: str, rows: list[dict]) -> None:
    folder = SOURCEDATA_DIR / entity / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{table}.csv"
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def seed_catalog(verbose: bool = True) -> int:
    """Write the starting datasource catalogue into the database.

    The catalogue is DATA now - an admin owns it from the Data Sources screen -
    so a fresh database needs seeding rather than deriving it on first read. That
    change is why this exists: `GET /api/datasources` used to silently mirror a
    hardcoded registry into the table on every call.

    Idempotent. An existing row is left exactly as the admin left it, including
    a retirement, so re-running the generator never resurrects a table somebody
    deliberately switched off.
    """
    import asyncio

    from sqlalchemy import select

    from app.db import SessionLocal, init_models
    from app.models import Datasource

    from seed_datasources import SEED_SOURCES

    async def run() -> int:
        await init_models()
        async with SessionLocal() as session:
            existing = {
                (entity, name)
                for entity, name in (
                    await session.execute(select(Datasource.entity, Datasource.name))
                ).all()
            }
            added = 0
            for src in SEED_SOURCES:
                if (src["entity"], src["name"]) in existing:
                    continue
                session.add(
                    Datasource(
                        name=src["name"],
                        entity=src["entity"],
                        system=src["system"],
                        description=src["description"],
                        path_template=(
                            f"mount/SourceData/{src['entity']}/{{date}}/{src['name']}.csv"
                        ),
                        grain=src["grain"],
                        columns=dict(src["columns"]),
                    )
                )
                added += 1
            if added:
                await session.commit()
            total = (
                await session.execute(select(Datasource))
            ).scalars().all()
        if verbose:
            print(f"  Datasource catalogue: {added} added, {len(total)} registered")
        return 0

    return asyncio.run(run())


def main() -> int:
    # --- master data, stable across all days -------------------------------
    master_rng = random.Random("demo-b-2026")

    # Seeded ONCE, then drawn three times. This used to be
    #     [f"AE{random.Random('shared').randrange(...):016d}" for _ in range(3)]
    # which built a fresh Random seeded 'shared' on every iteration and so
    # produced the SAME IBAN three times. All six records then shared one
    # account, and the vendor/employee join returned a 3x3 cross product where
    # three one-to-one collisions were intended.
    shared_rng = random.Random("demo-b-shared-ibans")
    shared_ibans = [f"AE{shared_rng.randrange(10**16):016d}" for _ in range(3)]

    a_emp = a_employees(random.Random("demo-a-master"))
    a_item = a_items(random.Random("demo-a-items"))
    a_recipe = a_recipes(random.Random("demo-a-recipes"), a_item)

    b_emp = b_employees(master_rng, shared_ibans)
    b_vendor = b_vendors(master_rng, shared_ibans, b_emp)
    b_credit = b_customer_credit(master_rng)

    for day in DAYS:
        # --- first configured client ------------------------------------------------------
        rng = rng_for(_FIRST, day)
        sales = a_sales(day, rng, a_emp)
        a_rows = {
            "ipos_sales": sales,
            "ipos_items": a_item,
            "ipos_voids": a_voids(day, rng, sales, a_emp),
            "syn_inventory": a_inventory(day, rng, a_item),
            "syn_recipes": a_recipe,
            "syn_wastage": a_wastage(day, rng, a_item, a_emp),
            "syn_stock_transfers": a_stock_transfers(day, rng, a_item),
            "hr_employees": a_emp,
            "hr_attendance": a_attendance(day, rng, a_emp),
        }
        for table, rows in a_rows.items():
            _write(_FIRST, day, table, rows)

        # --- second configured client ------------------------------------------------
        rng = rng_for(_SECOND, day)
        b_sales_rows = b_sales(day, rng, b_emp)
        pos, grn = b_procurement(day, rng, b_vendor)
        b_rows = {
            "ipos_sales": b_sales_rows,
            "ipos_price_overrides": b_price_overrides(day, rng, b_sales_rows, b_emp),
            "ipos_discounts": b_discounts(day, rng, b_sales_rows, b_emp),
            "hr_employees": b_emp,
            "hr_attendance": b_attendance(day, rng, b_emp),
            "bc_purchase_orders": pos,
            "bc_goods_receipts": grn,
            "bc_vendors": b_vendor,
            "bc_customer_credit": b_credit,
        }
        for table, rows in b_rows.items():
            _write(_SECOND, day, table, rows)

        print(
            f"  {day}  {_FIRST}[{scenario_for(_FIRST, day):<26}] "
            f"ipos_sales={len(a_rows['ipos_sales']):<5} "
            f"| {_SECOND} ipos_sales={len(b_rows['ipos_sales']):<5} "
            f"po={len(pos):<4} grn={len(grn)}"
        )

    print()
    print(f"  {_FIRST}  9 tables x {len(DAYS)} days  (POS, Warehouse, HRMS)")
    print(f"  {_SECOND}  9 tables x {len(DAYS)} days  (POS, HRMS, ERP)")
    print(f"  Planted cross-system bank matches: {len(shared_ibans)} "
          f"(hr_employees.bank_iban == bc_vendors.vendor_bank_iban)")
    print(f"  Output: {SOURCEDATA_DIR}")
    print()

    # The CSVs on disk are not the catalogue. An admin registers what we consume
    # from the Data Sources screen, so the two are deliberately separate steps.
    seed_catalog()
    return 0


if __name__ == "__main__":
    sys.exit(main())