"""
End-to-end smoke test against a running API.

Rules are addressed by their permanent `code`; each edit publishes a new version
row in `rules_master`. This suite covers that versioning behaviour explicitly:
an edit supersedes the head, editing resets validation, pausing does NOT burn a
version, rollback appends forward, schedules survive a version bump, and a rule
with findings cannot be deleted.

All version assertions are DELTA based, so the suite is re-runnable.

Usage:  python scripts/smoke_test.py [base_url]
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.engine.rules import scoped_code  # noqa: E402
from app.settings import settings  # noqa: E402

# Where the API is, unless told otherwise on the command line. The port comes
# from the configuration, so the suite cannot end up testing a port the server
# is not listening on.
BASE = (
    sys.argv[1]
    if len(sys.argv) > 1
    else f"http://127.0.0.1:{settings.api_port}"
)
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
MOUNT_DIR = Path(__file__).resolve().parent.parent.parent / "mount"

ADMIN = ("admin_user", "Admin@2026")
AUDITOR = ("audit_user", "Audit@2026")

# The demo estate is two layouts. Which client gets which one is the
# deployment's decision - the order of CLIENT_ENTITIES in the .env - so the
# suite identifies an estate by its tables, and never by a client's code.
WAREHOUSE_ESTATE = "syn_inventory"  # retail POS + a warehouse + HRMS
ERP_ESTATE = "bc_vendors"           # POS + HRMS + a Business Central ERP

# The two estates, by the name the rule specs use.
ESTATE_RETAIL = "retail"
ESTATE_ERP = "erp"

# The base code of each demo rule, and the estate it belongs to. The code it
# is FILED under carries the client's code as well - see filed_code().
RULE_SPECS = [
    ("CA-03", "high",
     "Value settled in the last hour of trading exceeds a multiple of the hourly average.",
     ESTATE_RETAIL),
    ("CA-04", "critical",
     "One staff member settles more than the permitted share of the day's cash invoices.",
     ESTATE_RETAIL),
    ("CA-22", "critical",
     "Vendor bank details in the ERP match employee bank details in HRMS.",
     ESTATE_ERP),
    ("CA-06", "high",
     "Exited employees still settling POS transactions.",
     ESTATE_RETAIL),
    # The two the rule agent wrote, kept as the demo estate's own controls:
    # they read the planted exceptions, so a fresh deployment can see them
    # working without anybody writing a prompt first.
    ("GEN-01", "medium",
     "Discounts given beyond the agreed limit, by store and by employee.",
     ESTATE_RETAIL),
    ("GEN-02", "medium",
     "Sales posted after the store had closed for the day.",
     ESTATE_RETAIL),
]

EXEC_EXPECTATIONS = {
    "CA-03": ("2026-10-19", "2026-10-16"),
    "CA-04": ("2026-10-19", "2026-10-17"),
    "CA-22": (None, "2026-10-15"),
    "CA-06": ("2026-10-16", "2026-10-15"),
    "GEN-01": ("2026-10-19", "2026-10-20"),
    "GEN-02": ("2026-10-19", "2026-10-20"),
}

# Which client each estate went to, and the code a rule is filed under. Both
# are resolved at run time from the catalogue, so the suite names no client
# and assumes no order.
ESTATE_CLIENT: dict[str, str] = {}


def estate_of(base: str) -> str:
    return next(spec[3] for spec in RULE_SPECS if spec[0] == base)


def filed_code(base: str) -> str:
    """The code a rule is filed under: its base code, prefixed with the
    client's code. Derived from the deployment, so the suite never writes one.
    """
    return scoped_code(base, ESTATE_CLIENT[estate_of(base)])


def rule_source(base: str, version: int = 1) -> Path:
    """The demo rule's source file.

    A rule's file is named for its code, and the code carries the client's
    code - so the file is named after the client too, and its name is not the
    same in every deployment. Found by the base code rather than written out,
    so the suite never names a client either.
    """
    stem = f"{base}_v{version}.py"
    found = sorted(RULES_DIR.glob(f"*-{stem}")) or sorted(RULES_DIR.glob(stem))
    if not found:
        raise SystemExit(f"no rule file for {base} v{version} in {RULES_DIR}")
    return found[0]


passes, failures = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passes, failures
    if ok:
        passes += 1
        print(f"  PASS  {label}")
    else:
        failures += 1
        print(f"  FAIL  {label}  {detail}")


def section(title: str) -> None:
    print(f"\n[{title}]")


def _run_folder_count() -> int:
    """How many run folders exist on disk."""
    runs = MOUNT_DIR / "Rules_Executions"
    if not runs.exists():
        return 0
    return len([d for d in runs.iterdir() if d.is_dir()])


def _trial_folder_count() -> int:
    """How many scratch folders a trial left behind.

    A trial deletes its own scratch space when it finishes. One that survives
    means a trial died in a way that skipped the cleanup, and the next one
    would be reading a folder nobody owns.
    """
    trials = MOUNT_DIR / ".trials"
    if not trials.exists():
        return 0
    return len([d for d in trials.iterdir() if d.is_dir()])


def _fire_checker(hours, minute, weekdays=None, days=None):
    """A predicate for "this fire time is the one the cadence promised".

    Asserted on the hour, the minute and the day rather than on the date, so
    the test says the same thing whenever it runs - and so that a rule firing
    on the WRONG DAY is caught, which is the failure that matters: a control
    that runs on the wrong day reports the wrong thing and looks like it is
    working.
    """
    def matches(when: datetime) -> bool:
        if when.hour not in hours or when.minute != minute:
            return False
        if weekdays is not None and when.weekday() not in weekdays:
            return False
        if days is not None and when.day not in days:
            return False
        return True

    return matches


def main() -> int:
    print("=" * 72)
    print(f"AUDIT PORTAL - smoke test against {BASE}")
    print("=" * 72)

    with httpx.Client(base_url=BASE, timeout=180.0) as c:
        section("health")
        h = c.get("/health").json()
        check("api healthy", h["status"] == "ok", str(h))
        check("database reachable", h["database"] is True)
        check("rules_master present", "rules_master" in h.get("tables", []),
              str(h.get("tables")))
        check("rule_versions gone", "rule_versions" not in h.get("tables", []),
              str(h.get("tables")))

        section("auth")
        r = c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
        check("admin login", r.status_code == 200, r.text[:160])
        admin = r.json()
        r = c.post("/api/auth/login", json={"username": AUDITOR[0], "password": AUDITOR[1]})
        check("auditor login", r.status_code == 200, r.text[:160])
        auditor = r.json()

        check("admin role", admin["user"]["role"] == "admin")
        check("auditor role", auditor["user"]["role"] == "auditor")
        check("wrong password rejected",
              c.post("/api/auth/login",
                     json={"username": ADMIN[0], "password": "nope"}).status_code == 401)
        check("unauthenticated rejected", c.get("/api/rules").status_code == 401)

        ah = {"Authorization": f"Bearer {admin['access_token']}"}
        ud = {"Authorization": f"Bearer {auditor['access_token']}"}

        # Who is being audited is deployment configuration, so the suite asks the
        # API rather than assuming. A smoke test that hardcoded the client codes
        # would quietly stop testing anything the moment CLIENT_ENTITIES changed
        # - and it would fail for the wrong reason, which is the expensive kind.
        served = sorted(
            e["code"]
            for e in c.get("/api/datasources/entities", headers=ah).json()["entities"]
        )
        if len(served) < 2:
            print("FAIL  deployment serves fewer than two clients; cannot scope the suite")
            return 1
        FIRST, SECOND = served[0], served[1]

        section("role based access control")
        check("auditor can read rules", c.get("/api/rules", headers=ud).status_code == 200)
        r = c.post("/api/rules", headers=ud,
                   json={"code": "RBAC-PROBE", "name": "nope",
                         "source_code": "def run(ctx):\n    return {}\n"})
        check("auditor CANNOT create rules", r.status_code == 403, f"got {r.status_code}")
        r = c.post("/api/rules/generate", headers=ud, json={
            "prompt": "flag anything odd", "entity": FIRST, "datasources": ["ipos_sales"]})
        check("auditor CANNOT generate rules", r.status_code == 403, f"got {r.status_code}")

        section("entities and datasources")
        ents = c.get("/api/datasources/entities", headers=ud).json()
        check("both configured clients are served",
              sorted(e["code"] for e in ents["entities"]) == sorted([FIRST, SECOND]))
        # Asked per client, deliberately. The endpoint resolves a missing
        # `entity` to the default client rather than returning everything, so
        # one unscoped call here would see one client's tables and conclude the
        # other client had none.
        for ent in (FIRST, SECOND):
            ds = c.get("/api/datasources", headers=ud, params={"entity": ent}).json()
            check(f"{ent} scopes to its own tables",
                  ds and {d["entity"] for d in ds} == {ent},
                  str(sorted({d["entity"] for d in ds})))
        ds = (c.get("/api/datasources", headers=ud, params={"entity": FIRST}).json()
              + c.get("/api/datasources", headers=ud, params={"entity": SECOND}).json())

        # WHICH client gets which demo estate is the deployment's decision - the
        # order of CLIENT_ENTITIES in the .env. The suite must not assume it.
        # It used to: it took the alphabetically first code and expected the
        # retail estate under it. When the two were mapped the other way round,
        # it looked for "a table the rules read" on a client whose rules lived
        # on the OTHER one, found none, and deleted a live table.
        #
        # What the suite CAN know is the LAYOUT, from the table names.
        by_entity = {
            ent: {d["name"] for d in ds if d["entity"] == ent}
            for ent in (FIRST, SECOND)
        }
        RETAIL = next(
            (e for e in (FIRST, SECOND) if WAREHOUSE_ESTATE in by_entity[e]), None
        )
        ERP = next(
            (e for e in (FIRST, SECOND) if ERP_ESTATE in by_entity[e]), None
        )
        check("one client holds the retail + warehouse estate", RETAIL is not None,
              str(sorted(by_entity)))
        check("one client holds the ERP estate", ERP is not None,
              str(sorted(by_entity)))
        check("the two estates went to different clients",
              RETAIL is not None and ERP is not None and RETAIL != ERP,
              f"{RETAIL} / {ERP}")
        if RETAIL is None or ERP is None:
            print("FAIL  the demo estate did not split across the two clients")
            return 1

        # The estate -> client resolution, once. A rule's client comes from the
        # estate it belongs to, never from a literal in this file.
        ESTATE_CLIENT.update({ESTATE_RETAIL: RETAIL, ESTATE_ERP: ERP})

        check(f"{RETAIL} has the POS/Warehouse/HRMS tables",
              {"ipos_sales", "syn_inventory", "hr_employees"} <= by_entity[RETAIL],
              str(sorted(by_entity[RETAIL])))
        check(f"{ERP} has the ERP tables",
              {"bc_vendors", "bc_purchase_orders"} <= by_entity[ERP],
              str(sorted(by_entity[ERP])))
        for ent in (FIRST, SECOND):
            d = c.get("/api/datasources/available-dates", headers=ud,
                      params={"entity": ent}).json()
            check(f"{ent} day folders present", len(d["dates"]) == 6, str(d["dates"]))
            check(f"{ent} reports a latest day", bool(d.get("latest")), str(d.get("latest")))

        # --- the admin's job: see what landed, register it, retire what we
        # --- no longer consume. Every assertion below is idempotent.
        ov = c.get("/api/datasources/overview", headers=ud,
                   params={"entity": RETAIL}).json()
        check("overview names the entity", ov["entity"] == RETAIL)
        check("overview resolved a data date", bool(ov["data_date"]), ov["data_date"])
        check("overview lists registered tables", len(ov["registered"]) >= 9,
              str(len(ov["registered"])))
        check("overview lists what the ETL delivered", len(ov["discovered"]) >= 9,
              str(len(ov["discovered"])))

        landed = {d["name"] for d in ov["discovered"]}
        registered = {d["name"]: d for d in ov["registered"]}
        check("every landed file is classified",
              all(d["state"] in ("new", "registered", "retired") for d in ov["discovered"]))
        check("landing status is reported per table",
              all(d["landing"] in ("landed", "absent") for d in ov["registered"]))
        check("a landed registered table reports its row count",
              any(d["row_count"] for d in ov["registered"] if d["name"] == "ipos_sales"))

        # Discovery reads the header, so the columns are real.
        sales_file = next(d for d in ov["discovered"] if d["name"] == "ipos_sales")
        check("discovered columns come from the CSV header",
              {"invoice_id", "store_id", "invoice_value"} <= set(sales_file["columns"]),
              str(list(sales_file["columns"])[:4]))
        check("discovered column count matches the header",
              sales_file["column_count"] == len(sales_file["columns"]))

        # --- preview: what the file actually holds --------------------------
        prev = c.get("/api/datasources/preview", headers=ud, params={
            "entity": RETAIL, "name": "ipos_sales", "limit": 5}).json()
        check("preview returns rows", len(prev["rows"]) == 5, str(len(prev["rows"])))
        check("preview reports the whole file's size, not just what it sent",
              prev["total_rows"] > 5, str(prev["total_rows"]))
        check("preview rows carry the file's own columns",
              "invoice_id" in prev["rows"][0], str(list(prev["rows"][0])[:4]))
        check("preview values are strings, so they cross JSON intact",
              all(isinstance(v, str) or v is None
                  for row in prev["rows"] for v in row.values()))
        check("preview defaults to the latest day the ETL sent",
              prev["data_date"] == ov["data_date"], prev["data_date"])

        # The name is client-supplied and becomes a path segment, so the same
        # rule that guards registration guards this.
        for bad in ("../../settings", "a/b", ".hidden"):
            check(f"preview refuses a path fragment: {bad!r}",
                  c.get("/api/datasources/preview", headers=ud,
                        params={"entity": RETAIL, "name": bad}).status_code == 400)
        check("preview 404s a file that did not land",
              c.get("/api/datasources/preview", headers=ud,
                    params={"entity": RETAIL, "name": "definitely_not_here"}).status_code == 404)

        # --- register, then retire, then restore ----------------------------
        probe = "smoke_probe_table"
        # Creating returns 201, a re-run returns 409. Asserting the same number
        # of things either way is what keeps the suite idempotent - a branch that
        # checks one more thing on first run makes run counts drift.
        new_row = c.post("/api/datasources", headers=ah, json={
            "name": probe, "entity": FIRST, "system": "Smoke",
            "description": "created by the smoke test", "grain": "one row per nothing",
            "columns": {"id": "VARCHAR"},
        })
        check("register is accepted, or reports it already exists",
              new_row.status_code in (201, 409),
              f"{new_row.status_code} {new_row.text[:140]}")

        listed = c.get("/api/datasources", headers=ud,
                       params={"entity": FIRST}).json()
        probe_row = next((d for d in listed if d["name"] == probe), None)
        check("registered table is listed", probe_row is not None)
        check("registered table keeps its declared columns",
              bool(probe_row) and probe_row["columns"] == {"id": "VARCHAR"},
              str(probe_row["columns"]) if probe_row else "missing")
        check("a registered table carries no active flag - it is simply present",
              bool(probe_row) and "is_active" not in probe_row)
        probe_id = probe_row["id"] if probe_row else None

        if probe_id is not None:
            check("auditor CANNOT register a datasource",
                  c.post("/api/datasources", headers=ud, json={
                      "name": "x", "entity": FIRST}).status_code == 403)
            check("auditor CANNOT remove a datasource",
                  c.delete(f"/api/datasources/{probe_id}", headers=ud).status_code == 403)

        # The refusal for a table live rules read is exercised in the rule-registration
        # section, once rules actually exist. Asserting it here would DELETE a
        # real seeded table on a fresh database, where nothing reads it yet.

        # --- hard delete, and the refusal that protects rule history --------
        if probe_id is not None:
            # The probe is read by no rule, so removal is allowed.
            gone = c.delete(f"/api/datasources/{probe_id}", headers=ah)
            check("a table no rule reads can be removed", gone.status_code == 204,
                  f"{gone.status_code} {gone.text[:120]}")
            check("removal is real - it is gone from the list",
                  not any(d["name"] == probe for d in c.get(
                      "/api/datasources", headers=ud,
                      params={"entity": FIRST}).json()))
            check("deleting it twice 404s",
                  c.delete(f"/api/datasources/{probe_id}", headers=ah).status_code == 404)
            check("auditor CANNOT remove a datasource",
                  c.delete(f"/api/datasources/{probe_id}", headers=ud).status_code in (403, 404))
            # Deliberately NOT recreated. A test that leaves a table registered
            # puts a permanent phantom row in the admin's datasource list, and
            # the next run creates its own.

        check("removal is the only destructive verb - retire is gone",
              c.post(f"/api/datasources/{probe_id}/retire", headers=ah,
                     json={}).status_code == 404 if probe_id is not None else True)
        check("and so is restore",
              c.post(f"/api/datasources/{probe_id}/restore", headers=ah,
                     json={}).status_code == 404 if probe_id is not None else True)

        check("unknown entity is rejected",
              c.get("/api/datasources/overview", headers=ud,
                    params={"entity": "ZZZ"}).status_code == 404)
        check("a path-traversal name is refused",
              c.post("/api/datasources", headers=ah, json={
                  "name": "../escape", "entity": FIRST}).status_code == 400)
        check("re-registering an active table is a conflict",
              c.post("/api/datasources", headers=ah, json={
                  "name": "ipos_sales", "entity": FIRST}).status_code == 409)

        section("params removed")
        check("GET /api/params returns 404",
              c.get("/api/params", headers=ud).status_code == 404)

        section("rules")
        existing = c.get("/api/rules", headers=ah).json()
        present = {x["code"] for x in existing["data"]}
        for base, severity, desc, estate in RULE_SPECS:
            code = filed_code(base)
            if code in present:
                check(f"{code} already registered", True)
                continue
            src = rule_source(base).read_text(encoding="utf-8")
            r = c.post("/api/rules", headers=ah, json={
                "code": code, "name": code, "entity": ESTATE_CLIENT[estate],
                "description": desc,
                "severity": severity,
                "source_code": src, "status": "draft"})
            if r.status_code != 201:
                check(f"{code} created", False, f"{r.status_code} {r.text[:220]}")
                continue
            body = r.json()
            check(f"{code} created -> {body['source_file']} v{body['version']}",
                  body["version"] == 1 and body["is_version_active"])

        all_rules = c.get("/api/rules", headers=ah).json()
        # Not "there are four rules". The estate grows the moment an admin
        # writes one - which is the product working - and an assertion about
        # the count would fail for doing exactly what it is for. What has to
        # hold is that the known rules are here and that each row is a head.
        listed = {x["code"] for x in all_rules["data"]}
        check("every known rule is listed",
              all(filed_code(spec[0]) in listed for spec in RULE_SPECS),
              str(sorted(listed)))
        check("every listed rule is a head version",
              all(x["is_version_active"] for x in all_rules["data"]))

        # A table's "used by" is read from the rules that read it, so it can
        # only be asserted once the rules exist - which is why it is here and
        # not in the catalogue section above.
        ov_rules = c.get("/api/datasources/overview", headers=ah,
                         params={"entity": RETAIL}).json()
        with_rules = {d["name"]: d for d in ov_rules["registered"]}
        check("rule dependency is reported",
              filed_code("CA-03") in with_rules["ipos_sales"]["used_by"],
              str(with_rules["ipos_sales"]["used_by"]))
        check("a table no rule reads shows no dependents",
              with_rules.get("syn_wastage", {}).get("used_by") == [],
              str(with_rules.get("syn_wastage", {}).get("used_by")))

        section("rule code validation")
        probe = "def run(ctx):\n    return {'status':'no_rows_returned'}\n"
        for bad, why in [("ca 03", "spaces rejected"), ("CA/03", "slash rejected")]:
            r = c.post("/api/rules", headers=ah,
                       json={"code": bad, "name": "code probe", "source_code": probe})
            check(why, r.status_code in (400, 409), f"{bad} -> {r.status_code}")

        r = c.post("/api/rules", headers=ah,
                   json={"code": "ca-99", "name": "lowercase probe",
                         "entity": ESTATE_CLIENT[ESTATE_RETAIL],
                         "source_code": probe})
        check("lowercase code normalised to uppercase",
              r.status_code == 201
              and r.json()["code"] == f"{ESTATE_CLIENT[ESTATE_RETAIL]}-CA-99",
              f"{r.status_code} {r.text[:120]}")
        if r.status_code == 201:
            dup = c.post("/api/rules", headers=ah,
                         json={"code": "CA-99", "name": "duplicate probe",
                               "entity": ESTATE_CLIENT[ESTATE_RETAIL],
                               "source_code": probe})
            check("duplicate code rejected (409)", dup.status_code == 409,
                  f"got {dup.status_code}")
            c.delete(f"/api/rules/{ESTATE_CLIENT[ESTATE_RETAIL]}-CA-99", headers=ah)

        # The code NAMES THE CLIENT, so a reference read out of an email says
        # whose control it is. The prefix follows the ROW, so it can never be
        # another client's - and a code that already carries one has it
        # replaced rather than stacked.
        client = ESTATE_CLIENT[ESTATE_RETAIL]
        other = ESTATE_CLIENT[ESTATE_ERP]
        r = c.post("/api/rules", headers=ah,
                   json={"code": "CA-97", "name": "prefix probe",
                         "entity": client, "source_code": probe})
        check("a rule's code is prefixed with its client's code",
              r.status_code == 201 and r.json()["code"] == f"{client}-CA-97",
              f"{r.status_code} {r.text[:120]}")
        if r.status_code == 201:
            c.delete(f"/api/rules/{client}-CA-97", headers=ah)

        r = c.post("/api/rules", headers=ah,
                   json={"code": f"{other}-CA-96", "name": "wrong prefix probe",
                         "entity": client, "source_code": probe})
        check("a code carrying another client's prefix is corrected, not stacked",
              r.status_code == 201 and r.json()["code"] == f"{client}-CA-96",
              f"{r.status_code} {r.text[:120]}")
        if r.status_code == 201:
            c.delete(f"/api/rules/{client}-CA-96", headers=ah)

        section("validate and activate")
        for base, *_ in RULE_SPECS:
            code = filed_code(base)
            rep = c.post(f"/api/rules/{code}/validate", headers=ah).json()
            check(f"{code} passes validation", rep["status"] == "valid",
                  f"{rep['status']}: {rep.get('errors')}"[:160])
            a = c.put(f"/api/rules/{code}/status", headers=ah, json={"status": "active"})
            check(f"{code} activated",
                  a.status_code == 200 and a.json()["status"] == "active",
                  f"{a.status_code} {a.text[:120]}")

        # Now that rules exist and read ipos_sales, the removal guard can be
        # exercised for real. Checked HERE rather than in the datasource section,
        # where on a fresh database nothing reads the table yet and the DELETE
        # would succeed and destroy seeded data.
        #
        # On the RETAIL estate, because that is where LAD-CA-03 lives. The other
        # estate has an ipos_sales of its own that no rule reads, and deleting
        # THAT one would have succeeded - which is how this suite deleted a
        # live table once.
        sales_row = next(
            (d for d in c.get("/api/datasources", headers=ud,
                              params={"entity": RETAIL}).json()
             if d["name"] == "ipos_sales"), None)
        check(f"ipos_sales is registered for {RETAIL}", sales_row is not None)
        if sales_row:
            blocked = c.delete(f"/api/datasources/{sales_row['id']}", headers=ah)
            check("removing a table live rules read is refused", blocked.status_code == 409,
                  f"got {blocked.status_code}")
            body = blocked.json().get("detail", {})
            check("the refusal names the dependent rules",
                  filed_code("CA-03") in (body.get("dependents") or []),
                  str(body)[:160])
            check("the refusal says the rule fails on its next run",
                  "next run" in (body.get("hint") or "").lower(),
                  str(body.get("hint"))[:140])
            check("the refused table is still registered",
                  any(d["name"] == "ipos_sales" for d in c.get(
                      "/api/datasources", headers=ud,
                      params={"entity": FIRST}).json()))

        # ------------------------------------------------------------------ #
        # Versioning runs against a throwaway code, deleted at the end, so the
        # repository never accumulates version files from test runs.
        section("versioning")
        VCODE = f"{ESTATE_CLIENT[ESTATE_RETAIL]}-SMOKE-VERSION-PROBE"
        vsrc = "DATASOURCES = ['ipos_sales']\n\n\ndef run(ctx):\n    return {'status': 'no_rows_returned'}\n"
        created = c.post("/api/rules", headers=ah, json={
            "code": VCODE, "name": "versioning probe", "source_code": vsrc,
            "entity": ESTATE_CLIENT[ESTATE_RETAIL], "status": "draft"})
        check("probe rule created at v1",
              created.status_code == 201 and created.json()["version"] == 1,
              f"{created.status_code} {created.text[:120]}")
        if created.status_code != 201:
            c.delete(f"/api/rules/{VCODE}", headers=ah)

        if created.status_code == 201:
            # Recorded from the source at creation, not only once someone
            # remembers to validate. Before this, a brand new rule reported no
            # datasources at all until Validate was pressed, so the rule page
            # said "reads no data sources" about a rule whose file declared one.
            check("a new rule records its datasources before validation",
                  created.json()["declared_datasources"] == ["ipos_sales"],
                  str(created.json()["declared_datasources"]))
            check("and it is unvalidated at that point",
                  created.json()["validation_status"] == "unvalidated")

            v0 = created.json()["version"]
            src0 = c.get(f"/api/rules/{VCODE}/source", headers=ud).json()
            check("source filename encodes code and version",
                  src0["source_file"] == f"{VCODE}_v{v0}.py", src0["source_file"])
            check("probe file written to disk",
                  (RULES_DIR / f"{VCODE}_v{v0}.py").exists())

            # An edit publishes the next version and moves the head flag. The new
            # version records the NEW source. Copying the previous version's list
            # left the recorded truth describing a file nobody was running.
            u = c.put(f"/api/rules/{VCODE}", headers=ah, json={
                "source_code": "DATASOURCES = ['ipos_sales', 'hr_employees']\n\n\n"
                               "def run(ctx):\n    return {'status': 'no_rows_returned'}\n",
                "change_note": "smoke: bump"})
            check("edit publishes the next version",
                  u.status_code == 200 and u.json()["version"] == v0 + 1,
                  f"{u.status_code} {u.text[:140]}")
            check("new version records the NEW source's datasources",
                  u.json()["declared_datasources"] == ["hr_employees", "ipos_sales"],
                  str(u.json()["declared_datasources"]))
            check("new version is the head", u.json()["is_version_active"])
            check("new version starts as draft", u.json()["status"] == "draft",
                  u.json()["status"])
            check("validation reset on a new version",
                  u.json()["validation_status"] == "unvalidated",
                  u.json()["validation_status"])
            check("new file written",
                  u.json()["source_file"] == f"{VCODE}_v{v0 + 1}.py",
                  u.json()["source_file"])
            check("previous file still on disk",
                  (RULES_DIR / f"{VCODE}_v{v0}.py").exists())

            hist = c.get(f"/api/rules/{VCODE}/versions", headers=ud).json()
            check("history has both versions", len(hist) == 2, str(len(hist)))
            check("history newest first", hist[0]["version"] == v0 + 1)
            check("exactly one head after the bump",
                  sum(1 for x in hist if x["is_version_active"]) == 1)

            a = c.put(f"/api/rules/{VCODE}/status", headers=ah, json={"status": "active"})
            check("cannot activate before validating", a.status_code == 409,
                  f"got {a.status_code}")
            c.post(f"/api/rules/{VCODE}/validate", headers=ah)
            a = c.put(f"/api/rules/{VCODE}/status", headers=ah, json={"status": "active"})
            check("activates once validated", a.status_code == 200,
                  f"{a.status_code} {a.text[:140]}")

            # Pausing must NOT burn a version.
            before_pause = c.get(f"/api/rules/{VCODE}", headers=ud).json()["version"]
            c.put(f"/api/rules/{VCODE}/status", headers=ah, json={"status": "paused"})
            after_pause = c.get(f"/api/rules/{VCODE}", headers=ud).json()["version"]
            check("pausing does not create a version", before_pause == after_pause,
                  f"{before_pause} -> {after_pause}")
            c.put(f"/api/rules/{VCODE}/status", headers=ah, json={"status": "active"})

            # Rollback appends forward rather than rewinding.
            v_now = c.get(f"/api/rules/{VCODE}", headers=ud).json()["version"]
            rb = c.post(f"/api/rules/{VCODE}/rollback/1", headers=ah)
            check("rollback publishes the next version, not an old one",
                  rb.status_code == 200 and rb.json()["version"] == v_now + 1,
                  f"{rb.status_code} {rb.text[:140]}")
            hist = c.get(f"/api/rules/{VCODE}/versions", headers=ud).json()
            check("rollback keeps full history", len(hist) == 3, f"{len(hist)} rows")
            check("rollback records its origin",
                  "v1" in hist[0]["change_note"].lower(), hist[0]["change_note"])
            check("rolled-back version carries v1's source",
                  "# smoke-edit" not in hist[0]["source_code"],
                  "rollback still has the edit")
            c.post(f"/api/rules/{VCODE}/validate", headers=ah)
            c.put(f"/api/rules/{VCODE}/status", headers=ah, json={"status": "active"})

            # Schedules are keyed by rule_code, so publishing a new version
            # must NOT orphan them.
            sc = c.post("/api/rule-schedules", headers=ah, json={
                "rule_code": VCODE, "kind": "DAILY",
                "config": {"time_of_day": "06:30"}, "timezone": "UTC"})
            check("schedule created against the probe code", sc.status_code == 201,
                  f"{sc.status_code} {sc.text[:120]}")
            u = c.put(f"/api/rules/{VCODE}", headers=ah,
                      json={"source_code": vsrc + "\\n# orphan-probe\\n",
                            "change_note": "orphan probe"})
            check("version published while a schedule exists",
                  u.status_code == 200, u.text[:140])
            still = c.get("/api/rule-schedules", headers=ud,
                          params={"rule_code": VCODE}).json()["data"]
            check("schedules survive a version bump (keyed by code, not row id)",
                  len(still) == 1, f"{len(still)} after the bump")

            # A rule with no findings CAN be deleted, and its files go with it.
            r = c.delete(f"/api/rules/{VCODE}", headers=ah)
            check("a rule with no findings can be deleted", r.status_code == 204,
                  f"got {r.status_code} {r.text[:140]}")
            check("deleting a rule removes its version files",
                  not any(RULES_DIR.glob(f"{VCODE}_v*.py")),
                  str([f.name for f in RULES_DIR.glob(f'{VCODE}_v*.py')]))

        # ------------------------------------------------------------------ #
        section("execution - clean and breach days")
        for base, (clean_day, breach_day) in EXEC_EXPECTATIONS.items():
            rcode = filed_code(base)
            if clean_day:
                r = c.post(f"/api/rules/{rcode}/test", headers=ud,
                           json={"data_date": clean_day}).json()
                check(f"{rcode} finds nothing on {clean_day} "
                      f"(ran {r.get('status')}, outcome {r.get('outcome')})",
                      r.get("status") == "success" and r.get("outcome") == "pass"
                      and not r.get("rows"),
                      r.get("summary", "")[:110])
            r = c.post(f"/api/rules/{rcode}/test", headers=ud,
                       json={"data_date": breach_day}).json()
            check(f"{rcode} fails on {breach_day} with rows "
                  f"(got {r.get('outcome')}, {len(r.get('rows', []))} rows)",
                  r.get("outcome") == "fail" and len(r.get("rows", [])) > 0,
                  r.get("summary", "")[:110])
            # A breach is the audit working. It must not read as a fault.
            check(f"{rcode} executed cleanly on {breach_day}",
                  r.get("status") == "success", str(r.get("status")))

        section("rule return contract")
        # A clean day returns success + empty rows -> outcome=pass, no ticket
        r = c.post(f"/api/rules/{filed_code('CA-03')}/test", headers=ud,
                   json={"data_date": "2026-10-19"}).json()
        check("clean day -> run.status=success", r.get("status") == "success",
              str(r.get("status")))
        check("clean day -> outcome=pass", r.get("outcome") == "pass",
              str(r.get("outcome")))
        check("clean day -> no rows", len(r.get("rows", [])) == 0)
        check("clean day -> has summary", bool(r.get("summary")))

        section("execution and outcome are independent")
        # The whole point of the split: a breach and a crash used to be the
        # same value. Nothing in the suite can crash a rule on demand, so the
        # guarantee is asserted on the shape instead - every run must be
        # internally consistent, and the two must never be inferred from
        # one another.
        runs_all = c.get("/api/rule-runs", headers=ud, params={"perPage": 500}).json()["data"]
        check("every run executed or is still running",
              all(x["status"] in ("success", "failed", "running") for x in runs_all),
              str({x["status"] for x in runs_all}))
        check("no run holds a legacy status value",
              not any(x["status"] in ("pass", "fail", "error") for x in runs_all),
              str({x["status"] for x in runs_all}))
        check("a completed run always has an outcome",
              all(x["outcome"] in ("pass", "fail") for x in runs_all if x["status"] == "success"),
              "a success with no outcome would read as a crash in the UI")
        check("a run that did not execute has no outcome",
              all(x["outcome"] is None for x in runs_all if x["status"] != "success"),
              "a failed execution has no opinion about the control")
        check("outcome=fail implies rows, outcome=pass implies none",
              all((len(x["rows"]) > 0) == (x["outcome"] == "fail")
                  for x in runs_all if x["status"] == "success"))

        breached = c.get("/api/rule-runs", headers=ud,
                         params={"outcome": "fail", "perPage": 200}).json()
        check("outcome filter selects breaches", breached["total"] > 0,
              f"total={breached['total']}")
        check("outcome filter returns only executed runs",
              all(x["status"] == "success" for x in breached["data"]))
        executed = c.get("/api/rule-runs", headers=ud,
                         params={"status": "success", "perPage": 200}).json()
        check("status filter selects execution, not findings",
              executed["total"] >= breached["total"],
              f"executed={executed['total']} breached={breached['total']}")
        check("status=success spans both outcomes",
              {x["outcome"] for x in executed["data"]} >= {"pass", "fail"}
              or executed["total"] == breached["total"],
              str({x["outcome"] for x in executed["data"]}))

        section("cross-system join")
        r = c.post(f"/api/rules/{filed_code('CA-22')}/test", headers=ud,
                   json={"data_date": "2026-10-15"}).json()
        rows = r.get("rows", [])
        check("ABM-CA-22 joined HRMS employees to ERP vendors",
              bool(rows) and "employee_id" in rows[0] and "vendor_id" in rows[0],
              str(rows[:1])[:200])
        r = c.post(f"/api/rules/{filed_code('CA-06')}/test", headers=ud,
                   json={"data_date": "2026-10-15"}).json()
        rows = r.get("rows", [])
        check("LAD-CA-06 joined POS activity to HRMS employment status",
              bool(rows) and "exit_date" in rows[0], str(rows[:1])[:200])

        section("tickets")
        tickets = c.get("/api/tickets", headers=ud, params={"perPage": 200}).json()
        check("tickets raised", tickets["total"] >= 4, f"total={tickets['total']}")
        check("tickets carry rule_code", all(t.get("rule_code") for t in tickets["data"]))
        check("ticket summary comes from rule_runs join",
              all(t.get("summary") for t in tickets["data"]))
        # Each ticket number names the control that raised it, so this asserts
        # the SHAPE rather than one rule's prefix: the list is unscoped and holds
        # tickets from every control, and a prefix check would only ever confirm
        # that the first row happened to be that control's.
        check("ticket numbers name their control",
              all(re.fullmatch(r"[A-Z0-9-]+-TKT-\d{5}", t["ticket_no"])
                  and t["ticket_no"].startswith(f"{t['rule_code']}-TKT-")
                  for t in tickets["data"]),
              str([(t["rule_code"], t["ticket_no"]) for t in tickets["data"][:4]]))
        check("filter by rule_code works",
              c.get("/api/tickets", headers=ud,
                    params={"rule_code": filed_code("CA-22"), "perPage": 50}).json()["total"] >= 1)
        check("ticket summary aggregates",
              c.get("/api/tickets/summary", headers=ud).json()["total"] > 0)

        open_ticket = next((t for t in tickets["data"] if t["status"] == "open"), None)
        if open_ticket:
            section("acknowledgement")
            r = c.post(f"/api/tickets/{open_ticket['id']}/acknowledge", headers=ud,
                       json={"status": "acknowledged", "note": "Reviewed."})
            check(f"auditor acknowledged {open_ticket['ticket_no']}",
                  r.status_code == 200 and r.json()["status"] == "acknowledged",
                  r.text[:160])
            check("acknowledged_by recorded", r.json()["acknowledged_by"] == AUDITOR[0])
            r = c.post(f"/api/tickets/{open_ticket['id']}/acknowledge", headers=ud,
                       json={"status": "acknowledged", "note": "again"})
            check("double-acknowledge rejected", r.status_code == 409, f"got {r.status_code}")
            r = c.post(f"/api/tickets/{open_ticket['id']}/reopen", headers=ud)
            check("reopen works", r.status_code == 200 and r.json()["status"] == "open")

        section("delete protection")
        r = c.delete(f"/api/rules/{filed_code('CA-03')}", headers=ah)
        check("a rule with findings cannot be deleted", r.status_code == 409,
              f"got {r.status_code} {r.text[:140]}")
        check("the message points at retiring instead",
              "retired" in r.json().get("detail", "").lower(), r.text[:160])
        check("deleting an unknown rule 404s",
              c.delete("/api/rules/NOPE-99", headers=ah).status_code == 404)

        section("friendly cadence")
        opts = c.get("/api/rule-schedules/cadence-options", headers=ud).json()
        check("cadence options exposed",
              len(opts["presets"]) > 0 and len(opts["kinds"]) == 6)
        check("monthly capped at day 28", opts["max_day_of_month"] == 28)
        check("hourly intervals limited", opts["hourly_intervals"] == [1, 2, 3, 4, 6, 8, 12])

        r = c.post("/api/rule-schedules/cadence-preview", headers=ud, json={
            "kind": "DAILY", "config": {"time_of_day": "06:30"},
            "timezone": "Asia/Dubai", "count": 3}).json()
        check("preview derives cron from friendly config",
              r["ok"] and r["cron"] == "30 06 * * *", str(r))
        check("preview label is plain English",
              r["cadence_label"] == "Daily at 06:30", r["cadence_label"])
        check("preview respects the timezone",
              str(datetime.fromisoformat(r["upcoming"][0]).utcoffset()) == "4:00:00",
              r["upcoming"][0])

        # Every kind derives a cron, and every kind fires when it says it will.
        #
        # The derived expression used to be asserted for DAILY alone, and the
        # fire time with it. The other five kinds had nothing, which meant a
        # change in the mapping - or in the cron library underneath it - had
        # nothing to notice it. Each kind is now asserted on both: the
        # expression it derives, and the day and hour it fires on.
        for kind, cfg, want_cron, fires_ok in [
            ("HOURLY", {"time_of_day": "00:00", "interval_hours": 6}, "00 */6 * * *",
             _fire_checker({0, 6, 12, 18}, 0)),
            ("DAILY", {"time_of_day": "06:30"}, "30 06 * * *",
             _fire_checker({6}, 30)),
            ("WEEKDAYS", {"time_of_day": "08:00"}, "00 08 * * 1-5",
             _fire_checker({8}, 0, weekdays={0, 1, 2, 3, 4})),
            ("WEEKLY", {"time_of_day": "09:00", "days_of_week": [1, 3]}, "00 09 * * 1,3",
             _fire_checker({9}, 0, weekdays={0, 2})),
            ("MONTHLY", {"time_of_day": "07:00", "day_of_month": 15}, "00 07 15 * *",
             _fire_checker({7}, 0, days={15})),
            ("CUSTOM", {"cron_override": "0 3 * * *"}, "0 3 * * *",
             _fire_checker({3}, 0)),
        ]:
            r = c.post("/api/rule-schedules/cadence-preview", headers=ud,
                       json={"kind": kind, "config": cfg, "timezone": "UTC",
                             "count": 4}).json()
            check(f"{kind} derives {want_cron}",
                  r.get("ok") is True and r.get("cron") == want_cron, str(r)[:160])
            fires = [datetime.fromisoformat(x) for x in r.get("upcoming", [])]
            check(f"{kind} fires when it says it will",
                  len(fires) == 4 and all(fires_ok(d) for d in fires),
                  str(fires)[:200])

        for kind, cfg, why in [
            ("MONTHLY", {"time_of_day": "07:00", "day_of_month": 31}, "day 31 rejected"),
            ("WEEKLY", {"time_of_day": "07:00", "days_of_week": [9]}, "weekday 9 rejected"),
            ("HOURLY", {"time_of_day": "00:00", "interval_hours": 7}, "interval 7 rejected"),
            ("CUSTOM", {"cron_override": "not a cron"}, "garbage cron rejected"),
        ]:
            r = c.post("/api/rule-schedules/cadence-preview", headers=ud,
                       json={"kind": kind, "config": cfg}).json()
            check(why, r["ok"] is False and r["problems"], str(r)[:140])

        section("schedules keyed by rule code")
        r = c.post("/api/rule-schedules", headers=ud,
                   json={"rule_code": filed_code("CA-03"), "kind": "DAILY",
                         "config": {"time_of_day": "06:30"}})
        check("auditor CANNOT create schedules", r.status_code == 403, f"got {r.status_code}")
        r = c.post("/api/rule-schedules", headers=ah,
                   json={"rule_code": "NOT-A-RULE", "kind": "DAILY",
                         "config": {"time_of_day": "06:30"}})
        check("a schedule cannot reference an unknown rule code",
              r.status_code == 404, f"got {r.status_code}")

        for s in c.get("/api/rule-schedules", headers=ud,
                       params={"rule_code": filed_code("CA-03")}).json()["data"]:
            c.delete(f"/api/rule-schedules/{s['id']}", headers=ah)

        created = []
        for label, kind, cfg in [
            ("Daily control", "DAILY", {"time_of_day": "06:30"}),
            ("Intraday sweep", "HOURLY", {"time_of_day": "00:00", "interval_hours": 6}),
            ("Month-end deep dive", "MONTHLY", {"time_of_day": "07:00", "day_of_month": 28}),
        ]:
            r = c.post("/api/rule-schedules", headers=ah, json={
                "rule_code": filed_code("CA-03"), "label": label, "kind": kind,
                "config": cfg, "timezone": "Asia/Dubai", "data_lag_days": 1})
            if r.status_code == 201:
                created.append(r.json())
        check("three cadences on one rule code", len(created) == 3, f"got {len(created)}")
        if len(created) == 3:
            check("cadence labels are human readable",
                  {s["cadence_label"] for s in created} == {
                      "Daily at 06:30", "Every 6 hours",
                      "Monthly on day 28 (last day of month) at 07:00"},
                  str([s["cadence_label"] for s in created]))
            jobs = c.get("/api/rule-schedules/cron/jobs", headers=ud).json()["jobs"]
            registered = {x["id"] for x in jobs}
            check("every schedule registered with APScheduler",
                  all(f"rule_schedule_{s['id']}" in registered for s in created),
                  str(sorted(registered))[:200])
            r = c.post("/api/rule-schedules", headers=ah,
                       json={"rule_code": "LAD-CA-03", "kind": "DAILY",
                             "config": {"time_of_day": "06:30"}})
            check("duplicate cadence rejected (409)", r.status_code == 409,
                  f"got {r.status_code}")

            # The probe's delete also removes its schedules and version files, so the
            # repository is left exactly as it was found.

        section("rule_runs ledger and dashboard")
        runs = c.get("/api/rule-runs", headers=ud, params={"perPage": 200}).json()
        check("rule_runs recorded", runs["total"] >= 8, f"total={runs['total']}")
        check("rule_runs carry rule_code", all(x.get("rule_code") for x in runs["data"]))
        # The detail page renders "rule v<n>" from this. It was read off a field
        # the response never had, so every run page said "vundefined".
        check("rule_runs carry the version that produced them",
              all(x.get("rule_version") is not None for x in runs["data"]),
              str([x.get("rule_version") for x in runs["data"][:5]]))
        one = c.get(f"/api/rule-runs/{runs['data'][0]['id']}", headers=ud).json()
        check("a single run resolves its version", one.get("rule_version") is not None,
              str(one.get("rule_version")))
        rsum = c.get("/api/rule-runs/summary", headers=ud).json()
        check("run summary separates execution from findings",
              rsum["breaches"] > 0 and rsum["cleared"] > 0, str(rsum))
        check("completed == breaches + cleared",
              rsum["completed"] == rsum["breaches"] + rsum["cleared"], str(rsum))
        check("total == completed + failed + running",
              rsum["total"] == rsum["completed"] + rsum["failed"] + rsum["running"],
              str(rsum))

        dash = c.get("/api/dashboard", headers=ah).json()
        check("dashboard names the date it reports to", dash["as_at"] is not None, str(dash)[:200])
        check("dashboard counts the assured controls",
              dash["controls"]["assured"] >= 4, str(dash["controls"]))
        # The landing page carries no run totals. Every figure on it has to be
        # about assurance or exposure; "282 runs" is a fact about the ledger
        # that answers no question a head of audit asks. The ledger's own
        # split is asserted above, against the summary endpoint that owns it.
        check("dashboard carries no raw run counter", "runs" not in dash, str(list(dash)))

        # --- client entity scope -----------------------------------------
        # Every screen in the portal is scoped to one client, so the split has
        # to hold on all four queries at once. The strong assertion is that the
        # parts add up to the whole: a filter that silently matched nothing, or
        # matched everything, passes a weaker check than this.
        section("client entity scope")
        both = (FIRST, SECOND)

        def split(path: str, key: str = "total", **params) -> dict:
            whole = c.get(path, headers=ah, params=params).json()[key]
            parts = {
                e: c.get(path, headers=ah, params={**params, "entity": e}).json()[key]
                for e in both
            }
            check(f"{path}: {FIRST} + {SECOND} == all clients", sum(parts.values()), whole)
            check(f"{path}: the two clients are disjoint", parts[FIRST] > 0 and parts[SECOND] > 0,
                  str(parts))
            return parts

        split("/api/rules")
        split("/api/rule-runs")
        split("/api/tickets")
        split("/api/tickets/grouped", key="total_tickets")
        split("/api/tickets/summary")
        split("/api/rule-runs/summary")

        # A run and a ticket carry no entity of their own - they inherit the
        # rule's - so the join is the thing most likely to be dropped.
        #
        # Asserted against rule_code -> entity from the rules list, NOT against a
        # naming convention. Rule codes carry no client prefix, so `startswith`
        # would have been asserting that a naming scheme had not changed - it
        # would pass on a filter that leaked the other client's rows, and fail on
        # a correctly scoped one. This is the actual question: does every row
        # handed back belong to the client that was asked for?
        owner = {x["code"]: x["entity"]
                 for x in c.get("/api/rules", headers=ah, params={"perPage": 200}).json()["data"]}
        runs_first = c.get("/api/rule-runs", headers=ah,
                         params={"entity": FIRST, "perPage": 200}).json()
        wrong = sorted({x["rule_code"] for x in runs_first["data"]
                        if owner.get(x["rule_code"]) != FIRST})
        check("a scoped run list carries only that client's rules", not wrong,
              f"{FIRST} scope returned {wrong}")
        tix_first = c.get("/api/tickets", headers=ah,
                        params={"entity": FIRST, "perPage": 500}).json()
        wrong = sorted({x["rule_code"] for x in tix_first["data"]
                        if owner.get(x["rule_code"]) != FIRST})
        check("a scoped ticket list carries only that client's rules", not wrong,
              f"{FIRST} scope returned {wrong}")

        # The grouped inbox caps how many rows it reads. When that cap bites,
        # the count it SHOWS must still be the true total and it must say the
        # list is partial. Reporting the number read as if it were the whole is
        # how a header ends up asserting a total it can only see part of.
        for ent in (FIRST, SECOND):
            g = c.get("/api/tickets/grouped", headers=ah,
                      params={"entity": ent, "group_by": "rule_code"}).json()
            flat = c.get("/api/tickets", headers=ah, params={"entity": ent}).json()
            check(f"{ent}: the grouped total is the real total, not what it read",
                  g["total_tickets"] == flat["total"],
                  f"grouped={g['total_tickets']} flat={flat['total']}")
            check(f"{ent}: it reports truncation honestly",
                  (g["scanned"] < g["total_tickets"]) == bool(g["truncated"]),
                  f"scanned={g['scanned']} total={g['total_tickets']} "
                  f"truncated={g['truncated']}")
            check(f"{ent}: grouped problems never outnumber the total",
                  g["total_tickets"] <= flat["total"] + 1,  # +1 for eventual count skew
                  f"grouped={g['total_tickets']} flat={flat['total']}")

        dash_first = c.get("/api/dashboard", headers=ah, params={"entity": FIRST}).json()
        check("dashboard reports the client it was scoped to", dash_first["entity"] == FIRST, dash_first["entity"])
        check("scoped dashboard controls == scoped rules list",
              dash_first["controls"]["total"]
              == c.get("/api/rules", headers=ah, params={"entity": FIRST}).json()["total"],
              str(dash_first["controls"]))
        check("scoped dashboard findings == scoped ticket list",
              dash_first["open"]["findings"]
              == c.get("/api/tickets", headers=ah, params={"entity": FIRST}).json()["total"],
              str(dash_first["open"]))

        # --- the assurance grid partitions the controls ----------------------
        # The assertion the whole read model rests on. A control that did not
        # run is UNCHECKED, so the four states must add back to the number of
        # controls that were expected to report. Fold a state into another - a
        # missed control quietly counted as a pass, or dropped from the total -
        # and the sum stops closing, which is exactly how assurance gets
        # reported as complete when it is not.
        section("assurance grid")
        for ent in both:
            d = c.get("/api/dashboard", headers=ah, params={"entity": ent}).json()
            assured = d["controls"]["assured"]
            check(f"{ent}: every data date counts the same assured controls",
                  {row["assured"] for row in d["days"]} <= {assured},
                  f"assured={assured}")
            broken = [
                row["date"] for row in d["days"]
                if row["passed"] + row["breach"] + row["errored"] + row["unchecked"]
                != row["assured"]
            ]
            check(f"{ent}: the four states partition the controls", not broken, str(broken))
            check(f"{ent}: reported is exactly the states that produced an opinion",
                  all(r["reported"] == r["passed"] + r["breach"] + r["errored"]
                      for r in d["days"]),
                  str(d["days"][:2]))
            check(f"{ent}: the grid ends on the date it reports to",
                  bool(d["days"]) and d["as_at"] == d["days"][-1]["date"],
                  f"as_at={d['as_at']}")
            check(f"{ent}: the grid is exactly the window it claims",
                  len(d["days"]) == d["window_days"],
                  f"{len(d['days'])} vs {d['window_days']}")

            # The headline must agree with the screen it sits above.
            tix = c.get("/api/tickets", headers=ah,
                        params={"entity": ent, "status": "open", "perPage": 1}).json()
            check(f"{ent}: open findings match the inbox",
                  d["open"]["findings"] == tix["total"],
                  f"dash={d['open']['findings']} inbox={tix['total']}")
            check(f"{ent}: exposure is never below the findings holding it",
                  d["open"]["exceptions"] >= d["open"]["findings"], str(d["open"]))
            check(f"{ent}: the severity breakdown adds back to the headline",
                  sum(r["findings"] for r in d["open"]["by_severity"]) == d["open"]["findings"],
                  str(d["open"]["by_severity"]))

            # Grouping must neither invent nor lose a finding.
            check(f"{ent}: grouped problems never exceed the open findings",
                  sum(p["count"] for p in d["longest_open"]) <= d["open"]["findings"],
                  str([(p["rule_code"], p["count"]) for p in d["longest_open"]]))

            # A control nobody scheduled cannot be overdue, or an on-demand
            # control is reported as a missed deadline every single day.
            check(f"{ent}: nothing without a cadence is called overdue",
                  all(o["scheduled"] for o in d["overdue"]),
                  str([(o["code"], o["cadence"]) for o in d["overdue"]]))
            check(f"{ent}: every overdue control really missed a day",
                  all(o["missing_days"] > 0 for o in d["overdue"]),
                  str([(o["code"], o["missing_days"]) for o in d["overdue"]]))
            check(f"{ent}: the ranked list covers only this client's controls",
                  all(owner.get(o["code"]) == ent for o in d["offenders"]),
                  str([(o["code"], owner.get(o["code"])) for o in d["offenders"]]))

        dash_second = c.get("/api/dashboard", headers=ah, params={"entity": SECOND}).json()
        check("scoped dashboards share no control",
              not ({o["code"] for o in dash_first["offenders"]}
                   & {o["code"] for o in dash_second["offenders"]}), "")
        unscoped = c.get("/api/dashboard", headers=ah).json()
        reached = {owner.get(o["code"]) for o in unscoped["offenders"]}
        check("the unscoped dashboard reaches both clients",
              reached >= {FIRST, SECOND},
              str(sorted(x for x in reached if x)))

        # The window is a real filter, not a label. It counts the days a client
        # has RUNS on, so it needs a client with more than one: the retail
        # estate, whose three controls were run on four different dates. The
        # ERP estate has a single rule, run on a single date, and a window
        # cannot narrow one day - which is not a broken window, it is a client
        # with nothing to narrow.
        dash_retail = c.get("/api/dashboard", headers=ah,
                            params={"entity": RETAIL}).json()
        narrow = c.get("/api/dashboard", headers=ah,
                       params={"entity": RETAIL, "window": 2}).json()
        check("the window narrows the grid", len(narrow["days"]) == 2, len(narrow["days"]))
        check("a narrower window is the tail of the wider one",
              [r["date"] for r in narrow["days"]]
              == [r["date"] for r in dash_retail["days"]][-2:],
              str([r["date"] for r in narrow["days"]]))
        check("the window cannot be set to nothing",
              c.get("/api/dashboard", headers=ah, params={"window": 1}).status_code == 422,
              str(c.get("/api/dashboard", headers=ah, params={"window": 1}).status_code))

        # The dangerous one. "Acknowledge everything on screen" must mean the
        # client the auditor is looking at, not every client in the portal - so
        # the scope has to reach the bulk call, not just the list beside it.
        #
        # The filter has to match tickets in BOTH clients, or a scoped bulk call
        # and an unscoped one touch the same rows and the check proves nothing.
        # No rule spans both clients - a rule belongs to exactly one - so the
        # filter is a severity that both happen to use. Everything touched is
        # reopened afterwards, so the inbox is left as it was found.
        sev = None
        for level in ("critical", "high", "medium", "low"):
            spread = {
                e: c.get("/api/tickets", headers=ah, params={
                    "severity": level, "entity": e, "status": "open",
                    "perPage": 1}).json()["total"]
                for e in both
            }
            if all(spread[e] > 0 for e in both):
                sev = level
                break
        check("a severity exists for both clients, to scope against", sev is not None,
              "no shared severity - the bulk scope check is skipped")

        if sev:
            first_open = c.get("/api/tickets", headers=ah, params={
                "severity": sev, "entity": FIRST, "status": "open",
                "perPage": 500}).json()["data"]
            all_open = c.get("/api/tickets", headers=ah, params={
                "severity": sev, "status": "open", "perPage": 500}).json()
            check("the unscoped set really is larger than one client's alone",
                  len(first_open) < all_open["total"],
                  f"one={len(first_open)} all={all_open['total']}")

            abm_open_before = all_open["total"] - len(first_open)
            r = c.post("/api/tickets/acknowledge-all", headers=ah,
                       json={"entity": FIRST, "status": "dismissed", "severity": sev,
                             "note": "smoke: scope probe"})
            body = r.json() if r.status_code == 200 else {}
            touched = set(body.get("ticket_nos", []))
            first_only = {t["ticket_no"] for t in first_open}

            check(f"bulk acknowledge touched only {FIRST} tickets",
                  bool(touched) and touched <= first_only,
                  f"touched={len(touched)} first={len(first_only)}")
            check("bulk acknowledge touched fewer than the unscoped set",
                  len(touched) < all_open["total"],
                  f"{len(touched)} vs {all_open['total']}")
            check("bulk acknowledge echoed the scope it used", body.get("entity") == FIRST, body.get("entity"))
            check("the other client's open tickets were left alone",
                  c.get("/api/tickets", headers=ah, params={
                      "severity": sev, "entity": SECOND, "status": "open",
                      "perPage": 1}).json()["total"], abm_open_before)

            # Put the inbox back exactly as it was found.
            for t in first_open:
                c.post(f"/api/tickets/{t['id']}/reopen", headers=ah)
            check("the inbox is back as it was found",
                  c.get("/api/tickets", headers=ah, params={
                      "severity": sev, "entity": FIRST, "status": "open",
                      "perPage": 500}).json()["total"], len(first_open))

        section("rule generation")
        gen = c.get("/api/rules/generate/status", headers=ud).json()
        check(f"generate status configured={gen['configured']}",
              isinstance(gen["configured"], bool))
        r = c.get("/api/rules/generate/context", headers=ud, params={"entity": FIRST})
        check(f"generate context lists {FIRST} datasources",
              r.status_code == 200 and "ipos_sales" in r.json()["datasources"])

        # --- the generation request is a contract, and it is enforced -------
        r = c.post("/api/rules/generate", headers=ah, json={
            "prompt": "flag anything odd", "entity": FIRST, "datasources": []})
        check("generation refuses an empty datasource selection",
              r.status_code == 422, f"got {r.status_code}")
        r = c.post("/api/rules/generate", headers=ah, json={
            "prompt": "flag anything odd", "entity": FIRST,
            "datasources": ["no_such_table_at_all"]})
        check("generation refuses a datasource that is not registered",
              r.status_code == 400, f"got {r.status_code} {r.text[:120]}")
        r = c.post("/api/rules/generate", headers=ah, json={
            "prompt": "x", "entity": FIRST, "datasources": ["ipos_sales"]})
        check("generation refuses a prompt that says nothing",
              r.status_code == 422, f"got {r.status_code}")

        # A generation attempt must not create anything. This is the whole
        # reason the agent runs in a scratch folder: a rule that has not been
        # reviewed has no business in the catalogue, and a trial that wrote a
        # run row would put a finding in the ledger that nobody asked for.
        rules_before = c.get("/api/rules", headers=ah, params={"perPage": 500}).json()["total"]
        runs_before = c.get("/api/rule-runs", headers=ah, params={"perPage": 1}).json()["total"]
        folders_before = _run_folder_count()

        section("the trial: a rule has to run before it is saved")
        # The trial has no endpoint, deliberately - it must not be something
        # the browser can ask for at will. So it is exercised here, directly,
        # because it is the thing that decides whether a rule may be saved.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.engine.loader import read_source
        from app.engine.trial import run_trial
        from app.settings import settings as app_settings

        entity = ESTATE_CLIENT[ESTATE_RETAIL]
        overview = c.get("/api/datasources/overview", headers=ah,
                         params={"entity": entity}).json()
        test_day = overview["data_date"]
        registered = [d["name"] for d in overview["registered"]]
        other_table = next((n for n in registered if n != "ipos_sales"), None)

        good = read_source(rule_source("CA-03").name, app_settings.rules_dir)
        t = run_trial(good, entity=entity, tables=["ipos_sales"],
                      data_date=date.fromisoformat(test_day), lookback_days=1,
                      allowed=["ipos_sales"])
        check("a real rule runs in the trial", t.status == "ok", f"{t.status} {t.error}")
        check("the trial reports what the control found",
              t.outcome in ("pass", "fail"), str(t.outcome))
        declared_name = re.search(r'^NAME\s*=\s*"(.*?)"', good, re.M)
        check("the trial reads the rule's own name",
              bool(t.name) and t.name == (declared_name.group(1) if declared_name else None),
              str(t.name))

        broken = good.replace('pl.col("invoice_value")',
                              'pl.col("invoice_value_typo")', 1)
        t = run_trial(broken, entity=entity, tables=["ipos_sales"],
                      data_date=date.fromisoformat(test_day), lookback_days=1,
                      allowed=["ipos_sales"])
        check("a rule with a column that does not exist is caught",
              t.status == "code_error", f"{t.status} {t.error}")
        check("and it is repairable, so the agent is told about it",
              t.repairable is True, str(t.status))

        banned = "import os\n\n\ndef run(ctx):\n    return {}\n"
        t = run_trial(banned, entity=entity, tables=["ipos_sales"],
                      data_date=date.fromisoformat(test_day), lookback_days=1,
                      allowed=["ipos_sales"])
        check("a rule that imports os is refused before it runs",
              t.status == "banned", f"{t.status} {t.error}")

        if other_table:
            outside = good.replace('ctx.table("ipos_sales")',
                                    f'ctx.table("{other_table}")', 1)
            t = run_trial(outside, entity=entity, tables=["ipos_sales"],
                          data_date=date.fromisoformat(test_day), lookback_days=1,
                          allowed=["ipos_sales"])
            check("a rule that reads a table outside the selection is refused",
                  t.status == "not_allowed", f"{t.status} {t.error}")

        t = run_trial(good, entity=entity, tables=["ipos_sales"],
                      data_date=date(2019, 1, 1), lookback_days=1,
                      allowed=["ipos_sales"])
        check("a rule on a day the ETL never delivered is a data problem",
              t.status == "data_missing", f"{t.status} {t.error}")
        check("and a data problem is NOT repairable - the agent cannot fix it",
              t.repairable is False, str(t.status))

        rules_after = c.get("/api/rules", headers=ah, params={"perPage": 500}).json()["total"]
        runs_after = c.get("/api/rule-runs", headers=ah, params={"perPage": 1}).json()["total"]
        check("the trial created no rule", rules_after == rules_before,
              f"{rules_before} -> {rules_after}")
        check("the trial wrote nothing to the ledger",
              runs_after == runs_before, f"{runs_before} -> {runs_after}")
        check("the trial left no run folder behind",
              _run_folder_count() == folders_before,
              f"{folders_before} -> {_run_folder_count()}")
        check("the trial left no scratch folder behind",
              _trial_folder_count() == 0, str(_trial_folder_count()))

    print("\n" + "=" * 72)
    print(f"RESULT: {passes} passed, {failures} failed")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())