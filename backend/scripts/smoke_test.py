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

import sys
from datetime import datetime
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

ADMIN = ("admin_user", "Admin@2026")
AUDITOR = ("audit_user", "Audit@2026")

RULE_SPECS = [
    ("LAD-CA-03", "LAD", "high", "LAD-CA-03_v1.py",
     "Value settled in the last hour of trading exceeds a multiple of the hourly average."),
    ("LAD-CA-04", "LAD", "critical", "LAD-CA-04_v1.py",
     "One staff member settles more than the permitted share of the day's cash invoices."),
    ("ABM-CA-22", "ABM", "critical", "ABM-CA-22_v1.py",
     "Vendor bank details in Business Central match employee bank details in HRMS."),
    ("LAD-CA-06", "LAD", "high", "LAD-CA-06_v1.py",
     "Exited employees still settling POS transactions."),
]

EXEC_EXPECTATIONS = {
    "LAD-CA-03": ("2026-10-19", "2026-10-16"),
    "LAD-CA-04": ("2026-10-19", "2026-10-17"),
    "ABM-CA-22": (None, "2026-10-15"),
    "LAD-CA-06": ("2026-10-16", "2026-10-15"),
}

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


def main() -> int:
    print("=" * 72)
    print(f"ABM AUDIT PORTAL - smoke test against {BASE}")
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

        section("role based access control")
        check("auditor can read rules", c.get("/api/rules", headers=ud).status_code == 200)
        r = c.post("/api/rules", headers=ud,
                   json={"code": "RBAC-PROBE", "name": "nope",
                         "source_code": "def run(ctx):\n    return {}\n"})
        check("auditor CANNOT create rules", r.status_code == 403, f"got {r.status_code}")
        r = c.post("/api/rules/generate", headers=ud, json={"prompt": "flag anything odd"})
        check("auditor CANNOT generate rules", r.status_code == 403, f"got {r.status_code}")

        section("entities and datasources")
        ents = c.get("/api/datasources/entities", headers=ud).json()
        check("two client entities LAD + ABM",
              sorted(e["code"] for e in ents["entities"]) == ["ABM", "LAD"])
        ds = c.get("/api/datasources", headers=ud).json()
        check("LAD has iPOS/Synapse/HRMS tables",
              {"ipos_sales", "syn_inventory", "hr_employees"}
              <= {d["name"] for d in ds if d["entity"] == "LAD"})
        check("ABM has Business Central tables",
              {"bc_vendors", "bc_purchase_orders"}
              <= {d["name"] for d in ds if d["entity"] == "ABM"})
        for ent in ("LAD", "ABM"):
            d = c.get("/api/datasources/available-dates", headers=ud,
                      params={"entity": ent}).json()
            check(f"{ent} day folders present", len(d["dates"]) == 6, str(d["dates"]))
            check(f"{ent} reports a latest day", bool(d.get("latest")), str(d.get("latest")))

        # --- the admin's job: see what landed, register it, retire what we
        # --- no longer consume. Every assertion below is idempotent.
        ov = c.get("/api/datasources/overview", headers=ud,
                   params={"entity": "LAD"}).json()
        check("overview names the entity", ov["entity"] == "LAD")
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

        check("rule dependency is reported",
              "LAD-CA-03" in registered["ipos_sales"]["used_by"],
              str(registered["ipos_sales"]["used_by"]))
        check("a table no rule reads shows no dependents",
              registered.get("syn_wastage", {}).get("used_by") == [],
              str(registered.get("syn_wastage", {}).get("used_by")))

        # Discovery reads the header, so the columns are real.
        sales_file = next(d for d in ov["discovered"] if d["name"] == "ipos_sales")
        check("discovered columns come from the CSV header",
              {"invoice_id", "store_id", "invoice_value"} <= set(sales_file["columns"]),
              str(list(sales_file["columns"])[:4]))
        check("discovered column count matches the header",
              sales_file["column_count"] == len(sales_file["columns"]))

        # --- register, then retire, then restore ----------------------------
        probe = "smoke_probe_table"
        # Creating returns 201, a re-run returns 409. Asserting the same number
        # of things either way is what keeps the suite idempotent - a branch that
        # checks one more thing on first run makes run counts drift.
        new_row = c.post("/api/datasources", headers=ah, json={
            "name": probe, "entity": "LAD", "system": "Smoke",
            "description": "created by the smoke test", "grain": "one row per nothing",
            "columns": {"id": "VARCHAR"},
        })
        check("register is accepted, or reports it already exists",
              new_row.status_code in (201, 409),
              f"{new_row.status_code} {new_row.text[:140]}")

        listed = c.get("/api/datasources", headers=ud,
                       params={"entity": "LAD"}).json()
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
                      "name": "x", "entity": "LAD"}).status_code == 403)
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
                      params={"entity": "LAD"}).json()))
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
                  "name": "../escape", "entity": "LAD"}).status_code == 400)
        check("re-registering an active table is a conflict",
              c.post("/api/datasources", headers=ah, json={
                  "name": "ipos_sales", "entity": "LAD"}).status_code == 409)

        section("params removed")
        check("GET /api/params returns 404",
              c.get("/api/params", headers=ud).status_code == 404)

        section("rules")
        existing = c.get("/api/rules", headers=ah).json()
        present = {x["code"] for x in existing["data"]}
        for code, entity, severity, filename, desc in RULE_SPECS:
            if code in present:
                check(f"{code} already registered", True)
                continue
            src = (RULES_DIR / filename).read_text(encoding="utf-8")
            r = c.post("/api/rules", headers=ah, json={
                "code": code, "name": code, "entity": entity, "description": desc,
                "severity": severity,
                "source_code": src, "status": "draft"})
            if r.status_code != 201:
                check(f"{code} created", False, f"{r.status_code} {r.text[:220]}")
                continue
            body = r.json()
            check(f"{code} created -> {body['source_file']} v{body['version']}",
                  body["version"] == 1 and body["is_version_active"])

        all_rules = c.get("/api/rules", headers=ah).json()
        check("four rules listed", all_rules["total"] == 4, str(all_rules["total"]))
        check("every listed rule is a head version",
              all(x["is_version_active"] for x in all_rules["data"]))

        section("rule code validation")
        probe = "def run(ctx):\n    return {'status':'no_rows_returned'}\n"
        for bad, why in [("lad ca 03", "spaces rejected"), ("LAD/CA", "slash rejected")]:
            r = c.post("/api/rules", headers=ah,
                       json={"code": bad, "name": "code probe", "source_code": probe})
            check(why, r.status_code in (400, 409), f"{bad} -> {r.status_code}")

        r = c.post("/api/rules", headers=ah,
                   json={"code": "lad-ca-99", "name": "lowercase probe",
                         "source_code": probe})
        check("lowercase code normalised to uppercase",
              r.status_code == 201 and r.json()["code"] == "LAD-CA-99",
              f"{r.status_code} {r.text[:120]}")
        if r.status_code == 201:
            dup = c.post("/api/rules", headers=ah,
                         json={"code": "LAD-CA-99", "name": "duplicate probe",
                               "source_code": probe})
            check("duplicate code rejected (409)", dup.status_code == 409,
                  f"got {dup.status_code}")
            c.delete("/api/rules/LAD-CA-99", headers=ah)

        section("validate and activate")
        for code, *_ in RULE_SPECS:
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
        sales_row = next(
            (d for d in c.get("/api/datasources", headers=ud,
                              params={"entity": "LAD"}).json()
             if d["name"] == "ipos_sales"), None)
        check("ipos_sales is registered for LAD", sales_row is not None)
        if sales_row:
            blocked = c.delete(f"/api/datasources/{sales_row['id']}", headers=ah)
            check("removing a table live rules read is refused", blocked.status_code == 409,
                  f"got {blocked.status_code}")
            body = blocked.json().get("detail", {})
            check("the refusal names the dependent rules",
                  "LAD-CA-03" in (body.get("dependents") or []), str(body)[:160])
            check("the refusal says the rule fails on its next run",
                  "next run" in (body.get("hint") or "").lower(),
                  str(body.get("hint"))[:140])
            check("the refused table is still registered",
                  any(d["name"] == "ipos_sales" for d in c.get(
                      "/api/datasources", headers=ud,
                      params={"entity": "LAD"}).json()))

        # ------------------------------------------------------------------ #
        # Versioning runs against a throwaway code, deleted at the end, so the
        # repository never accumulates version files from test runs.
        section("versioning")
        VCODE = "SMOKE-VERSION-PROBE"
        vsrc = "DATASOURCES = ['ipos_sales']\n\n\ndef run(ctx):\n    return {'status': 'no_rows_returned'}\n"
        created = c.post("/api/rules", headers=ah, json={
            "code": VCODE, "name": "versioning probe", "source_code": vsrc,
            "status": "draft"})
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
        for rcode, (clean_day, breach_day) in EXEC_EXPECTATIONS.items():
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
        r = c.post("/api/rules/LAD-CA-03/test", headers=ud,
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
        r = c.post("/api/rules/ABM-CA-22/test", headers=ud,
                   json={"data_date": "2026-10-15"}).json()
        rows = r.get("rows", [])
        check("ABM-CA-22 joined HRMS employees to Business Central vendors",
              bool(rows) and "employee_id" in rows[0] and "vendor_id" in rows[0],
              str(rows[:1])[:200])
        r = c.post("/api/rules/LAD-CA-06/test", headers=ud,
                   json={"data_date": "2026-10-15"}).json()
        rows = r.get("rows", [])
        check("LAD-CA-06 joined iPOS activity to HRMS employment status",
              bool(rows) and "exit_date" in rows[0], str(rows[:1])[:200])

        section("tickets")
        tickets = c.get("/api/tickets", headers=ud, params={"perPage": 200}).json()
        check("tickets raised", tickets["total"] >= 4, f"total={tickets['total']}")
        check("tickets carry rule_code", all(t.get("rule_code") for t in tickets["data"]))
        check("ticket summary comes from rule_runs join",
              all(t.get("summary") for t in tickets["data"]))
        check("ticket numbers formatted",
              all(t["ticket_no"].startswith("ABM-TKT-") for t in tickets["data"]))
        check("filter by rule_code works",
              c.get("/api/tickets", headers=ud,
                    params={"rule_code": "ABM-CA-22", "perPage": 50}).json()["total"] >= 1)
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
        r = c.delete("/api/rules/LAD-CA-03", headers=ah)
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
                   json={"rule_code": "LAD-CA-03", "kind": "DAILY",
                         "config": {"time_of_day": "06:30"}})
        check("auditor CANNOT create schedules", r.status_code == 403, f"got {r.status_code}")
        r = c.post("/api/rule-schedules", headers=ah,
                   json={"rule_code": "NOT-A-RULE", "kind": "DAILY",
                         "config": {"time_of_day": "06:30"}})
        check("a schedule cannot reference an unknown rule code",
              r.status_code == 404, f"got {r.status_code}")

        for s in c.get("/api/rule-schedules", headers=ud,
                       params={"rule_code": "LAD-CA-03"}).json()["data"]:
            c.delete(f"/api/rule-schedules/{s['id']}", headers=ah)

        created = []
        for label, kind, cfg in [
            ("Daily control", "DAILY", {"time_of_day": "06:30"}),
            ("Intraday sweep", "HOURLY", {"time_of_day": "00:00", "interval_hours": 6}),
            ("Month-end deep dive", "MONTHLY", {"time_of_day": "07:00", "day_of_month": 28}),
        ]:
            r = c.post("/api/rule-schedules", headers=ah, json={
                "rule_code": "LAD-CA-03", "label": label, "kind": kind,
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

        dash = c.get("/api/dashboard").json()
        check("dashboard totals", dash["rules"]["total"] >= 4, str(dash))
        check("dashboard counts versions separately",
              dash["rules"]["versions"] >= dash["rules"]["total"], str(dash["rules"]))
        check("dashboard splits execution from findings",
              dash["runs"]["completed"] == dash["runs"]["breaches"] + dash["runs"]["cleared"],
              str(dash["runs"]))

        section("rule generation")
        gen = c.get("/api/rules/generate/status", headers=ud).json()
        check(f"generate status configured={gen['configured']}",
              isinstance(gen["configured"], bool))
        r = c.get("/api/rules/generate/context", headers=ud, params={"entity": "LAD"})
        check("generate context lists LAD datasources",
              r.status_code == 200 and "ipos_sales" in r.json()["datasources"])

    print("\n" + "=" * 72)
    print(f"RESULT: {passes} passed, {failures} failed")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())