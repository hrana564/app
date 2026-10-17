"""
End-to-end smoke test against a running API.

Exercises the full auditor journey and asserts the behaviour that matters:
  login -> both roles -> RBAC -> register 4 rules across 2 entities
  -> run each against clean and breach days -> tickets raised
  -> acknowledge -> schedule created -> APScheduler job registered

Usage:  python scripts/smoke_test.py [base_url]
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

ADMIN = ("admin_user", "Admin@2026")
AUDITOR = ("audit_user", "Audit@2026")

# code, entity, severity, filename, description
RULE_SPECS = [
    (
        "LAD-CA-03",
        "LAD",
        "high",
        "rule_1.py",
        "Value settled in the last hour of trading exceeds a multiple of the "
        "hourly average (retail and bar items).",
    ),
    (
        "LAD-CA-04",
        "LAD",
        "critical",
        "rule_2.py",
        "One staff member settles more than the permitted share of the day's "
        "cash-tendered invoices.",
    ),
    (
        "ABM-CA-22",
        "ABM",
        "critical",
        "rule_3.py",
        "Vendor bank details in Business Central match employee bank details "
        "in HRMS (cross-system join).",
    ),
    (
        "LAD-CA-06",
        "LAD",
        "high",
        "rule_4.py",
        "Exited employees still settling POS transactions - access not revoked "
        "(cross-system join).",
    ),
]

# code -> (clean_day or None, breach_day)
# ABM-CA-22 is a standing master-data control: the vendor/employee bank collision
# exists in every day folder until the master data is remediated, so there is
# deliberately no clean day for it.
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


def main() -> int:
    print("=" * 72)
    print(f"ABM AUDIT PORTAL - smoke test against {BASE}")
    print("=" * 72)

    with httpx.Client(base_url=BASE, timeout=180.0) as c:
        # --- health -------------------------------------------------------
        print("\n[health]")
        h = c.get("/health").json()
        check("api healthy", h["status"] == "ok", str(h))
        check("database reachable", h["database"] is True)

        # --- auth ---------------------------------------------------------
        print("\n[auth]")
        r = c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
        check("admin login", r.status_code == 200, r.text[:160])
        admin = r.json()
        r = c.post("/api/auth/login", json={"username": AUDITOR[0], "password": AUDITOR[1]})
        check("auditor login", r.status_code == 200, r.text[:160])
        auditor = r.json()

        check("admin role", admin["user"]["role"] == "admin")
        check("auditor role", auditor["user"]["role"] == "auditor")
        check(
            "wrong password rejected",
            c.post("/api/auth/login", json={"username": ADMIN[0], "password": "nope"}).status_code == 401,
        )
        check("unauthenticated rejected", c.get("/api/rules").status_code == 401)

        ah = {"Authorization": f"Bearer {admin['access_token']}"}
        ud = {"Authorization": f"Bearer {auditor['access_token']}"}

        # --- RBAC ---------------------------------------------------------
        print("\n[role based access control]")
        check("auditor can read rules", c.get("/api/rules", headers=ud).status_code == 200)
        r = c.post(
            "/api/rules",
            headers=ud,
            json={
                "code": "RBAC-PROBE",
                "name": "should be rejected",
                "source_code": "def run(ctx):\n    return {'status':'no_rows_returned'}\n",
            },
        )
        check("auditor CANNOT create rules", r.status_code == 403, f"got {r.status_code}")
        r = c.put(
            "/api/params", headers=ud,
            json={"key": "rbac.probe", "label": "probe", "value_numeric": 1},
        )
        check("auditor CANNOT edit params", r.status_code == 403, f"got {r.status_code}")
        r = c.post(
            "/api/rules/generate", headers=ud, json={"prompt": "flag anything odd"}
        )
        check("auditor CANNOT generate rules", r.status_code == 403, f"got {r.status_code}")

        # --- entities + datasources ---------------------------------------
        print("\n[entities and datasources]")
        ents = c.get("/api/datasources/entities", headers=ud).json()
        codes = sorted(e["code"] for e in ents["entities"])
        check("two client entities LAD + ABM", codes == ["ABM", "LAD"], str(codes))
        lad = next(e for e in ents["entities"] if e["code"] == "LAD")
        abm = next(e for e in ents["entities"] if e["code"] == "ABM")
        check("Laduree systems", sorted(lad["systems"]) == sorted(["iPOS (SaaS)", "Azure Synapse", "HRMS"]), str(lad["systems"]))
        check("ABM systems", sorted(abm["systems"]) == sorted(["Business Central", "HRMS", "iPOS"]), str(abm["systems"]))

        ds = c.get("/api/datasources", headers=ud).json()
        lad_ds = {d["name"] for d in ds if d["entity"] == "LAD"}
        abm_ds = {d["name"] for d in ds if d["entity"] == "ABM"}
        check("LAD has iPOS/Synapse/HRMS tables", {"ipos_sales", "syn_inventory", "hr_employees"} <= lad_ds, str(sorted(lad_ds)))
        check("ABM has Business Central tables", {"bc_vendors", "bc_purchase_orders"} <= abm_ds, str(sorted(abm_ds)))

        for ent in ("LAD", "ABM"):
            d = c.get("/api/datasources/available-dates", headers=ud, params={"entity": ent}).json()
            check(f"{ent} day folders present", len(d["dates"]) == 6, str(d["dates"]))

        # --- params -------------------------------------------------------
        print("\n[parameters]")
        params = c.get("/api/params/as-dict", headers=ud).json()
        for key in ("lad.last_hour_ratio", "lad.single_staff_cash_share", "abm.bank_match_min_similarity"):
            check(f"{key} present", key in params)

        # --- register rules -------------------------------------------------
        print("\n[rules]")
        existing = c.get("/api/rules", headers=ah).json()
        rule_ids: dict[str, int] = {}

        for code, entity, severity, filename, desc in RULE_SPECS:
            found = next((x for x in existing["data"] if x["code"] == code), None)
            if found:
                rule_ids[code] = found["id"]
                check(f"{code} already registered ({entity}, id={found['id']})", True)
                continue
            src = (RULES_DIR / filename).read_text(encoding="utf-8")
            r = c.post(
                "/api/rules",
                headers=ah,
                json={
                    "code": code,
                    "name": code,
                    "entity": entity,
                    "description": desc,
                    "severity": severity,
                    "query_mode": "SNAPSHOT",
                    "source_code": src,
                    "status": "active",
                    "datasource_hint": "",
                },
            )
            if r.status_code != 201:
                check(f"{code} created", False, f"{r.status_code} {r.text[:220]}")
                continue
            body = r.json()
            rule_ids[code] = body["id"]
            check(f"{code} created [{entity}] -> {body['source_file']} v{body['current_version']}", True)

        if len(rule_ids) < 4:
            print("\nAborting: could not register all rules.")
            return 1

        r = c.get(f"/api/rules/{rule_ids['LAD-CA-03']}/source", headers=ud)
        check("source readable", r.status_code == 200 and "def run(ctx)" in r.json()["source_code"])
        r = c.get(f"/api/rules/{rule_ids['LAD-CA-03']}/versions", headers=ud)
        check("version history present", r.status_code == 200 and len(r.json()) >= 1)

        # --- entity isolation ------------------------------------------------
        print("\n[entity isolation]")
        r = c.post(
            f"/api/rules/{rule_ids['LAD-CA-03']}/test",
            headers=ud, json={"data_date": "2026-10-19"},
        )
        check("LAD rule runs against LAD data", r.status_code == 200, r.text[:160])

        # --- execution: clean vs breach --------------------------------------
        print("\n[execution - clean and breach days]")
        for code, (clean_day, breach_day) in EXEC_EXPECTATIONS.items():
            rid = rule_ids[code]
            if clean_day:
                r = c.post(f"/api/rules/{rid}/test", headers=ud, json={"data_date": clean_day})
                body = r.json()
                check(
                    f"{code} passes on {clean_day} (got {body.get('status')})",
                    body.get("status") == "pass",
                    body.get("summary", "")[:110],
                )
            r = c.post(f"/api/rules/{rid}/test", headers=ud, json={"data_date": breach_day})
            body = r.json()
            check(
                f"{code} fails on {breach_day} with rows "
                f"(got {body.get('status')}, {len(body.get('rows', []))} rows)",
                body.get("status") == "fail" and len(body.get("rows", [])) > 0,
                body.get("summary", "")[:110],
            )

        # --- cross-system join proof ------------------------------------------
        print("\n[cross-system join]")
        r = c.post(
            f"/api/rules/{rule_ids['ABM-CA-22']}/test",
            headers=ud, json={"data_date": "2026-10-15"},
        )
        rows = r.json().get("rows", [])
        check(
            "ABM-CA-22 joined HRMS employees to Business Central vendors",
            bool(rows) and "employee_id" in rows[0] and "vendor_id" in rows[0],
            str(rows[:1])[:200],
        )
        r = c.post(
            f"/api/rules/{rule_ids['LAD-CA-06']}/test",
            headers=ud, json={"data_date": "2026-10-15"},
        )
        rows = r.json().get("rows", [])
        check(
            "LAD-CA-06 joined iPOS activity to HRMS employment status",
            bool(rows) and "exit_date" in rows[0],
            str(rows[:1])[:200],
        )

        # --- tickets -----------------------------------------------------------
        print("\n[tickets]")
        tickets = c.get("/api/tickets", headers=ud, params={"perPage": 200}).json()
        check("tickets raised", tickets["total"] >= 4, f"total={tickets['total']}")
        check(
            "ticket numbers formatted",
            all(t["ticket_no"].startswith("ABM-TKT-") for t in tickets["data"]),
        )
        check(
            "open tickets carry rows",
            all(len(t["rows"]) > 0 for t in tickets["data"] if t["status"] == "open"),
        )
        # JSON serialisation of timestamp columns
        check(
            "timestamp values serialised as strings",
            any(
                isinstance(v, str) and "T" in v
                for t in tickets["data"]
                for row in t["rows"][:3]
                for v in row.values()
            ),
        )

        summary = c.get("/api/tickets/summary", headers=ud).json()
        check("ticket summary aggregates", summary["total"] > 0, str(summary))

        open_ticket = next((t for t in tickets["data"] if t["status"] == "open"), None)
        if open_ticket:
            print("\n[acknowledgement]")
            r = c.post(
                f"/api/tickets/{open_ticket['id']}/acknowledge",
                headers=ud,
                json={"status": "acknowledged", "note": "Reviewed, referred to store manager."},
            )
            check(
                f"auditor acknowledged {open_ticket['ticket_no']}",
                r.status_code == 200 and r.json()["status"] == "acknowledged",
                r.text[:160],
            )
            check("acknowledged_by recorded", r.json()["acknowledged_by"] == AUDITOR[0])
            r = c.post(
                f"/api/tickets/{open_ticket['id']}/acknowledge",
                headers=ud, json={"status": "acknowledged", "note": "again"},
            )
            check("double-acknowledge rejected", r.status_code == 409, f"got {r.status_code}")
            r = c.post(f"/api/tickets/{open_ticket['id']}/reopen", headers=ud)
            check("reopen works", r.status_code == 200 and r.json()["status"] == "open")

        # --- schedules ----------------------------------------------------------
        print("\n[schedules]")
        r = c.post(
            "/api/schedules", headers=ud,
            json={"rule_id": rule_ids["LAD-CA-03"], "cron": "30 6 * * *", "enabled": True},
        )
        check("auditor CANNOT create schedules", r.status_code == 403, f"got {r.status_code}")

        existing_sched = c.get(
            "/api/schedules", headers=ud, params={"rule_id": rule_ids["LAD-CA-03"]}
        ).json()["data"]
        r = c.post(
            "/api/schedules", headers=ah,
            json={"rule_id": rule_ids["LAD-CA-03"], "cron": "30 6 * * *", "enabled": True},
        )
        if r.status_code == 201:
            sched = r.json()
            check(
                f"schedule created cron='{sched['cron']}' next={sched['next_run_at']}",
                sched["enabled"],
            )
            jobs = c.get("/api/schedules/cron/jobs", headers=ud).json()
            check(
                "job registered with APScheduler",
                any(j["id"] == f"schedule_{sched['id']}" for j in jobs["jobs"]),
                str(jobs)[:200],
            )
            r = c.post(
                "/api/schedules", headers=ah,
                json={"rule_id": rule_ids["LAD-CA-04"], "cron": "not a cron"},
            )
            check("invalid cron rejected", r.status_code == 400, f"got {r.status_code}")
            r = c.put(f"/api/schedules/{sched['id']}", headers=ah, json={"enabled": False})
            check("schedule disabled", r.status_code == 200 and not r.json()["enabled"])
        elif existing_sched and r.status_code == 409:
            check("schedule already exists (409 duplicate guard)", True)
        else:
            check("schedule created", False, f"{r.status_code} {r.text[:200]}")

        # --- runs + dashboard ----------------------------------------------------
        print("\n[runs ledger and dashboard]")
        runs = c.get("/api/runs", headers=ud, params={"perPage": 200}).json()
        check("runs recorded", runs["total"] >= 8, f"total={runs['total']}")
        rsum = c.get("/api/runs/summary", headers=ud).json()
        check("run summary has pass+fail", rsum["pass"] > 0 and rsum["fail"] > 0, str(rsum))

        dash = c.get("/api/dashboard").json()
        check("dashboard totals", dash["rules"]["total"] >= 4, str(dash))

        print("\n[rule generation]")
        gen = c.get("/api/rules/generate/status", headers=ud).json()
        check(f"generate status configured={gen['configured']}", isinstance(gen["configured"], bool))
        r = c.get("/api/rules/generate/context", headers=ud, params={"entity": "LAD"})
        check("generate context lists LAD datasources", r.status_code == 200 and "ipos_sales" in r.json()["datasources"])

    print("\n" + "=" * 72)
    print(f"RESULT: {passes} passed, {failures} failed")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())