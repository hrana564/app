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
from datetime import datetime
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


def section(title: str) -> None:
    print(f"\n[{title}]")


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

        # --- params must be gone entirely ---------------------------------
        section("params removed")
        check("GET /api/params returns 404",
              c.get("/api/params", headers=ud).status_code == 404)
        check("PUT /api/params returns 404",
              c.put("/api/params", headers=ah,
                    json={"key": "x", "value_numeric": 1}).status_code == 404)

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

        # --- validate + activate ------------------------------------------
        # A rule cannot be activated until it passes validation, and only active
        # rules get their schedules registered with APScheduler.
        section("validate and activate")
        for code, rid in rule_ids.items():
            rep = c.post(f"/api/rules/{rid}/validate", headers=ah).json()
            check(
                f"{code} passes validation",
                rep["status"] == "valid",
                f"{rep['status']}: {rep['errors']}"[:160],
            )
            a = c.put(f"/api/rules/{rid}", headers=ah, json={"status": "active"})
            check(
                f"{code} activated",
                a.status_code == 200 and a.json()["status"] == "active",
                f"{a.status_code} {a.text[:120]}",
            )

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

        # --- friendly cadence (no raw cron anywhere in the UI) ---------------
        section("friendly cadence")
        opts = c.get("/api/rule-schedules/cadence-options", headers=ud).json()
        check("cadence options exposed", len(opts["presets"]) > 0 and len(opts["kinds"]) == 6)
        check("monthly capped at day 28", opts["max_day_of_month"] == 28)
        check("hourly intervals limited", opts["hourly_intervals"] == [1, 2, 3, 4, 6, 8, 12])

        r = c.post(
            "/api/rule-schedules/cadence-preview", headers=ud,
            json={"kind": "DAILY", "config": {"time_of_day": "06:30"},
                  "timezone": "Asia/Dubai", "count": 3},
        ).json()
        check("preview derives cron from friendly config", r["ok"] and r["cron"] == "30 06 * * *", str(r))
        check("preview label is plain English", r["cadence_label"] == "Daily at 06:30", r["cadence_label"])
        check("preview returns real fire times", len(r["upcoming"]) == 3, str(r.get("upcoming"))[:120])
        first = datetime.fromisoformat(r["upcoming"][0])
        check("preview respects the timezone", str(first.utcoffset()) == "4:00:00", str(first))

        for bad_kind, bad_cfg, why in [
            ("MONTHLY", {"time_of_day": "07:00", "day_of_month": 31}, "day 31 must be rejected"),
            ("WEEKLY", {"time_of_day": "07:00", "days_of_week": [9]}, "out-of-range weekday rejected"),
            ("HOURLY", {"time_of_day": "00:00", "interval_hours": 7}, "non-dividing interval rejected"),
            ("CUSTOM", {"cron_override": "not a cron"}, "garbage cron rejected"),
        ]:
            r = c.post(
                "/api/rule-schedules/cadence-preview", headers=ud,
                json={"kind": bad_kind, "config": bad_cfg},
            ).json()
            check(why, r["ok"] is False and r["problems"], str(r)[:140])

        # --- multiple schedules per rule ---------------------------------------
        section("multiple schedules per rule")
        r03 = rule_ids["LAD-CA-03"]
        existing = c.get("/api/rule-schedules", headers=ud, params={"rule_id": r03}).json()["data"]
        for s in existing:
            c.delete(f"/api/rule-schedules/{s['id']}", headers=ah)

        created = []
        for label, kind, cfg in [
            ("Daily control", "DAILY", {"time_of_day": "06:30"}),
            ("Intraday sweep", "HOURLY", {"time_of_day": "00:00", "interval_hours": 6}),
            ("Month-end deep dive", "MONTHLY", {"time_of_day": "07:00", "day_of_month": 28}),
        ]:
            r = c.post(
                "/api/rule-schedules", headers=ah,
                json={"rule_id": r03, "label": label, "kind": kind, "config": cfg,
                      "timezone": "Asia/Dubai", "data_lag_days": 1},
            )
            if r.status_code == 201:
                created.append(r.json())
        check("three cadences on one rule", len(created) == 3, f"got {len(created)}")
        if len(created) == 3:
            check(
                "cadence labels are human readable",
                {s["cadence_label"] for s in created} == {
                    "Daily at 06:30", "Every 6 hours",
                    "Monthly on day 28 (last day of month) at 07:00",
                },
                str([s["cadence_label"] for s in created]),
            )
            check("each has its own next run", len({s["next_run_at"] for s in created}) == 3)

            jobs = c.get("/api/rule-schedules/cron/jobs", headers=ud).json()["jobs"]
            registered = {j["id"] for j in jobs}
            check(
                "every schedule registered with APScheduler",
                all(f"rule_schedule_{s['id']}" in registered for s in created),
                str(sorted(registered))[:200],
            )

            r = c.post(
                "/api/rule-schedules", headers=ah,
                json={"rule_id": r03, "kind": "DAILY", "config": {"time_of_day": "06:30"}},
            )
            check("duplicate cadence rejected (409)", r.status_code == 409, f"got {r.status_code}")

        r = c.post(
            "/api/rule-schedules", headers=ud,
            json={"rule_id": r03, "kind": "DAILY", "config": {"time_of_day": "06:30"}},
        )
        check("auditor CANNOT create schedules", r.status_code == 403, f"got {r.status_code}")

        r = c.put(f"/api/rules/{r03}", headers=ah, json={"status": "paused"})
        check("rule paused", r.status_code == 200 and r.json()["status"] == "paused")
        jobs = c.get("/api/rule-schedules/cron/jobs", headers=ud).json()["jobs"]
        check(
            "pausing the rule deregisters its schedules",
            not any(j["id"].startswith("rule_schedule_") for j in jobs),
            str([j["id"] for j in jobs])[:200],
        )
        c.put(f"/api/rules/{r03}", headers=ah, json={"status": "active"})
        jobs = c.get("/api/rule-schedules/cron/jobs", headers=ud).json()["jobs"]
        check(
            "reactivating re-registers them",
            sum(1 for j in jobs if j["id"].startswith("rule_schedule_")) >= 3,
            str([j["id"] for j in jobs])[:200],
        )

        # --- validation gate --------------------------------------------------
        section("validation gate")
        for code, rid in rule_ids.items():
            rep = c.get(f"/api/rules/{rid}/validate-report", headers=ud).json()
            check(f"{code} is validated", rep["status"] == "valid", str(rep)[:140])

        bad_rule = "import os\ndef run(ctx):\n    return {'status':'no_rows_returned'}\n"
        r = c.post(
            "/api/rules", headers=ah,
            json={"code": "SMOKE-BAD-1", "name": "banned import", "entity": "LAD",
                  "source_code": bad_rule, "status": "draft"},
        )
        bad_id = r.json()["id"]
        rep = c.post(f"/api/rules/{bad_id}/validate", headers=ud).json()
        check("banned import rejected", rep["status"] == "invalid" and rep["errors"], str(rep)[:160])
        r = c.put(f"/api/rules/{bad_id}", headers=ah, json={"status": "active"})
        check("invalid rule cannot be activated", r.status_code == 409, f"got {r.status_code}")

        cross = (
            'def run(ctx):\n'
            '    rows = ctx.sql(\'SELECT * FROM ctx.table("bc_vendors")\').all()\n'
            '    return {"status": "no_rows_returned", "rows": []}\n'
        )
        r = c.put(f"/api/rules/{bad_id}", headers=ah,
                  json={"source_code": cross, "change_note": "cross-entity table"})
        rep = c.post(f"/api/rules/{bad_id}/validate", headers=ud).json()
        check(
            "cross-entity table reference caught inside an SQL string",
            rep["status"] == "invalid"
            and any("bc_vendors" in e for e in rep["errors"]),
            str(rep)[:200],
        )
        c.delete(f"/api/rules/{bad_id}", headers=ah)

        # --- alert policy + dedup ---------------------------------------------
        section("alert policy and deduplication")
        c.put(f"/api/rules/{r03}", headers=ah,
              json={"ticket_mode": "ONE_PER_RUN", "dedupe_mode": "PER_DATA_DATE",
                    "alerting_enabled": True})

        def ticket_count() -> int:
            return c.get("/api/tickets", headers=ud,
                         params={"rule_id": r03, "perPage": 200}).json()["total"]

        # Asserted on ticket IDENTITY, not on absolute counts, so the suite is
        # re-runnable: a previous run may already have opened a ticket for this
        # data date, in which case this run bumps it instead of creating one.
        DEDUPE_DAY = "2026-10-18"

        def ticket_ids() -> set:
            return {
                t["id"] for t in c.get("/api/tickets", headers=ud,
                                       params={"rule_id": r03, "perPage": 200}).json()["data"]
            }

        ids_before = ticket_ids()
        c.post(f"/api/rules/{r03}/test", headers=ud, json={"data_date": DEDUPE_DAY})
        ids_after_first = ticket_ids()

        new_first = ids_after_first - ids_before
        if new_first:
            check("first failing run raises a new ticket", True)
        else:
            # Already-open ticket for this date from an earlier suite run: the
            # run must have bumped it instead. Verify the bump happened.
            bumped_id = max(ids_after_first) if ids_after_first else None
            t = next((x for x in c.get("/api/tickets", headers=ud,
                                       params={"rule_id": r03, "perPage": 200}).json()["data"]
                      if x["id"] == bumped_id), None)
            check("run bumps the existing ticket rather than duplicating it",
                  bool(t) and t["occurrences"] >= 2,
                  str(t)[:160] if t else "no ticket")

        ids_before_repeat = ticket_ids()
        c.post(f"/api/rules/{r03}/test", headers=ud, json={"data_date": DEDUPE_DAY})
        check("repeat on same data date does NOT add a ticket",
              ticket_ids() == ids_before_repeat,
              f"new ids: {sorted(ticket_ids() - ids_before_repeat)}")

        rows = c.get("/api/tickets", headers=ud,
                     params={"rule_id": r03, "perPage": 10}).json()["data"]
        bumped = next((t for t in rows if t["occurrences"] >= 2), None)
        check("repeat bumps the occurrence count", bumped is not None,
              str([(t['ticket_no'], t['occurrences']) for t in rows])[:160])

        if bumped:
            c.post(f"/api/tickets/{bumped['id']}/acknowledge", headers=ud,
                   json={"status": "acknowledged", "note": "smoke"})
            ids_before_new = ticket_ids()
            c.post(f"/api/rules/{r03}/test", headers=ud, json={"data_date": DEDUPE_DAY})
            check(
                "a breach that persists after acknowledgement opens a NEW ticket",
                bool(ticket_ids() - ids_before_new),
                "no new ticket after acknowledging",
            )

        c.put(f"/api/rules/{r03}", headers=ah, json={"dedupe_mode": "NONE"})
        before_none = ticket_count()
        c.post(f"/api/rules/{r03}/test", headers=ud, json={"data_date": DEDUPE_DAY})
        c.post(f"/api/rules/{r03}/test", headers=ud, json={"data_date": DEDUPE_DAY})
        check("dedupe NONE always adds a ticket",
              ticket_count() == before_none + 2, f"{before_none} -> {ticket_count()}")

        c.put(f"/api/rules/{r03}", headers=ah,
              json={"dedupe_mode": "PER_DATA_DATE", "alerting_enabled": False})
        before_mute = ticket_count()
        run = c.post(f"/api/rules/{r03}/test", headers=ud,
                     json={"data_date": DEDUPE_DAY}).json()
        check("alerting muted: run still executes", run["status"] == "fail", run.get("status"))
        check("alerting muted: no ticket raised",
              ticket_count() == before_mute, f"{before_mute} -> {ticket_count()}")
        c.put(f"/api/rules/{r03}", headers=ah, json={"alerting_enabled": True})

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