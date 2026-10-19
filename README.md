# ABM Audit Portal

**Intelligent Continuous Auditing — Laduree & Al Boom Marine**

A lightweight continuous-auditing platform. Power Automate extracts from client systems into a
CSV landing zone; this app runs audit rules against it on a schedule, and raises **tickets** that
auditors review and acknowledge.

Rules are **plain Python files**. There is no rule DSL, no new language to learn, and no engine
between you and the data.

---

## Contents

1. [What it does](#1-what-it-does)
2. [Architecture](#2-architecture)
3. [Data model](#3-data-model)
4. [The rule contract](#4-the-rule-contract)
5. [Datasources & cross-system joins](#5-datasources--cross-system-joins)
6. [Roles](#6-roles)
7. [Getting started](#7-getting-started)
8. [Project layout](#8-project-layout)
9. [Configuration](#9-configuration)
10. [API reference](#10-api-reference)
11. [Writing a rule](#11-writing-a-rule)
12. [Generating rules from a prompt](#12-generating-rules-from-a-prompt)
13. [Licensing](#13-licensing)
14. [Power Apps migration path](#14-power-apps-migration-path)
15. [Testing](#15-testing)
16. [Known gaps](#16-known-gaps)

---

## 1. What it does

Four entities, and that is the whole product:

| Entity | Meaning |
|---|---|
| **Rules** | An audit control, stored as a Python file + DB metadata |
| **Schedules** | A cron per rule, registered with APScheduler |
| **Runs** | One execution of one rule against one data date |
| **Tickets** | The alert an auditor acknowledges |

The execution model is deliberately trivial:

```
cron fires
  -> load rules/<CODE>_v<version>.py
  -> call run(ctx)
  -> validate the returned dict
  -> insert a `rule_runs` row
  -> if the rule returned rows, insert a `tickets` row
```

That is the entire engine. Everything of substance lives in the rule file.

### Rules are polars, not SQL

```
ctx.table("ipos_sales")   a polars DataFrame of the rows this run staged
ctx.date                  the date being audited
ctx.lookback_days         how far back this rule reads
ctx.log("...")            appends a note to the run summary
```

Three methods. There is no query engine and no query string.

This replaced an earlier `ctx.sql()` surface that needed DuckDB behind it. SQL
turned out to be the wrong shape for what these controls actually do. The
equivalent of "is the last hour more than twice the hourly average" was

```sql
sum(invoice_value) FILTER (WHERE hour < 23)
  / nullif(count(DISTINCT hour) FILTER (WHERE hour < 23), 0)
```

and as polars it is

```python
hourly = base.group_by("hour").agg(pl.col("invoice_value").sum())
baseline = hourly.filter(pl.col("hour") < LAST_HOUR)["value"].mean()
```

The second one is the audit step, stated as the audit step.

---

## 2. Architecture

```
LAD  Laduree          iPOS (SaaS)   |  Azure Synapse  |  HRMS
ABM  Al Boom Marine   iPOS          |  HRMS           |  Business Central

Power Automate ──▶ mount/SourceData/<ENTITY>/<YYYY-MM-DD>/<table>.csv
                        │
                        ▼
              APScheduler (per-rule cron)
                        │
                        ▼
       Python Audit Engine ── stage the inputs, then polars over them
                        │
                        ▼
      mount/Rules_Executions/<run_id>/   the exact rule + CSVs that run read
                        │
                        ▼
                  PostgreSQL  ABM_Audit_Portal
                 rules_master · rule_schedules · rule_runs · tickets
                 datasources · users
                        │
                        ▼
              React UI  (react-admin)   ──▶  later: Power Apps
```

**Every run freezes its inputs first.** Before the rule runs, the CSVs it declared
and the days its `lookback_days` covers are copied into
`mount/Rules_Executions/<run_id>/` alongside a byte-identical copy of the rule source
and a `metadata.json` naming every file with its SHA-256. The rule reads the
copy, never the mount.

That is what makes a failure debuggable: `python -m app.engine.replay 123`
re-runs that rule against those exact bytes, with no database and no network,
and reports whether it still reproduces the recorded outcome.

---

## 3. Data model

Six tables in `ABM_Audit_Portal`. Created on startup by `create_all`.

```
users          id, username, password_hash, role, is_active
datasources    id, entity, name, system, description, path_template, grain, columns
               -- no active flag: a table is registered or it is not

rules_master   ONE ROW PER VERSION OF A RULE
               id, code, version, is_version_active, status, source_file,
               name, entity, description, severity, lookback_days,
               validation_status, validation_messages, validated_at, validated_by,
               declared_datasources,   -- derived from the source, at write time
               change_note, created_by, origin, created_at, updated_at

               UNIQUE (code, version)
               UNIQUE (code) WHERE is_version_active

rule_schedules id, rule_code, label, kind, config, cron, timezone, data_lag_days,
               last_run_at, next_run_at, created_by          -- no FK, see below

runs           id, rule_id -> rules_master(id), rule_code, rule_version, schedule_id, data_date,
               triggered_by, started_at, finished_at, status, outcome, summary, metrics,
               rows, error_detail, duration_ms
               -- status  = did the rule EXECUTE?   running | success | failed
               -- outcome = what did the control FIND? pass | fail, NULL when it never ran

tickets        id, ticket_no, run_id, rule_id -> rules_master(id), rule_code,
               schedule_id, severity, rows, status, acknowledged_by,
               acknowledged_at, ack_note, created_at
```

`summary` is **not** on `tickets`. It is read from `runs` by join — one source of
truth, and always the text produced by the run that actually raised it.

### A rule is a chain of versions

`rules_master` holds **one row per version**, not one row per rule. `code`
identifies the logical rule and repeats across versions; exactly one row per code
is flagged `is_version_active` - the **head**.

```
LAD-CA-03  v1   head=f   LAD-CA-03_v1.py  initial version
LAD-CA-03  v2   head=f   LAD-CA-03_v2.py  smoke: bump
LAD-CA-03  v3   head=t   LAD-CA-03_v3.py  rollback to v1
```

Two database constraints make this safe rather than conventional:

| Constraint | Guarantees |
|---|---|
| `uq_rules_master_active_version` - `UNIQUE (code) WHERE is_version_active` | Two live versions of one rule are **impossible** |
| `uq_rules_master_code_version` - `UNIQUE (code, version)` | A version number can never be reused |

Both are verified directly against Postgres in the smoke test.

**Rules are addressed by `code`, never by row id** - a row id identifies one
*version*, the code identifies the *rule*. URLs read `/api/rules/LAD-CA-03/...`.

### Behaviour

| Action | Effect |
|---|---|
| Edit logic | New row at `version = max+1` + new file `<CODE>_v<n>.py`; the old row loses the head flag. Starts as `draft` with validation reset |
| `PUT .../status` | Lifecycle only - **no new version**. Pausing is not a logic change |
| Rollback | Copies an old version's source into a **new** head. Reads as *"v3 restored v1's logic"*; version numbers only move forward |
| Delete | Refused (409) if any run or ticket references it - findings must not vanish because a rule was deleted. Retire it instead |

### Schedules are keyed by `rule_code`, not a relationship

`rule_schedules` holds `rule_code` with **no foreign key**. `code` repeats across
versions so it cannot be unique, and a row-id FK would orphan every schedule the
moment a new version was published. The scheduler resolves
`rule_code -> head version` **at fire time**, so a version published between the
schedule firing and execution is the one that runs.

The cost is that `code` must be a stable permanent identifier. It is therefore:

- **write-once** - never edited after creation
- **validated** `^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$` and uppercased on create, so it
  can never produce a hostile filename or collide with an existing code

A schedule pointing at an unknown code is reported as `unresolved` at startup
rather than silently never firing.

`runs.rule_id` and `tickets.rule_id` **do** keep real foreign keys - to the exact
version row, so "what code produced this finding" is a single lookup instead of a
join. `rule_code` is denormalised onto both for list views, which also keeps the
future Power Apps connector to single flat calls.

### Key decisions

- **A rule is the aggregate root.** Schedules, code versions, runs and alerts all hang off it.

- **Every rule version is retained.** Rollback creates a *new* version copied from an old one, so the
  audit trail is never rewritten.

- **`rules.status` is the only run switch.** There is deliberately no `enabled` on `rule_schedules`.
  Two switches that could disagree were the original defect — the scheduler silently preferred
  `rules.status` with nothing telling the operator. Now: one truth.

- **`metrics` and `rows` are `JSONB`.** Rule output is free-form; the schema shouldn't constrain it.

- **`tickets.rows` holds the exact rows that caused the failure**, so an auditor sees the evidence
  without re-running anything.

- **Thresholds live in the rule source, as named constants.** The requirements are full of
  undefined limits — *"say 50%"*, *"exception threshold to be defined"* — so each rule declares
  them itself, e.g. `SINGLE_STAFF_CASH_SHARE_LIMIT = 50.0`. Changing one is a code change, which
  writes a new `rules_master` row: the audit trail records the change instead of losing it.

- **`rule_schedules.cron` is derived, never authored.** `kind` + `config` are what a user fills in.
  See §4a.

---

## 4. The rule contract

Every rule file must define exactly one function:

```python
def run(ctx) -> dict:
    ...
```

and return a dict matching `app.schemas.RuleResult`:

```python
{
    "status":  "success" | "failed",   # did the rule EXECUTE?
    "summary": str,     # one line an auditor reads on the ticket
    "metrics": dict,    # the numbers behind the verdict
    "rows":    list,    # the rows that caused the breach -> become the ticket
    "error_message": str | None,   # required when status == "failed"
}
```

A rule that ran and found nothing returns `{"status": "success", "rows": []}`. A rule that could
not run at all returns `{"status": "failed", "error_message": "...", "rows": []}`.

The runner maps that onto **two** run columns, because they answer different questions:

| `status` | `rows` | `runs.status` | `runs.outcome` | ticket raised? |
|---|---|---|---|---|
| `success` | `[]` | `success` | `pass` | no |
| `success` | non-empty | `success` | **`fail`** | **yes**, carrying `rows` |
| `failed` / raised exception | — | `failed` | `NULL` | no — the control never got an opinion |

The third row is the one that matters. A rule that never ran has no finding, so a day it failed
to cover is **unchecked, not clean** — and the UI says so. The mapping is expressed once in
`app/engine/ticketing.py` and shared with the replay tool, so the two can never disagree.

The return value is validated by Pydantic, so a malformed rule fails loudly instead of
corrupting the ledger. A rule that raises an exception is caught — it records `failed` with the
traceback and never takes down the process.

### What `ctx` gives you

| Member | Purpose |
|---|---|
| `ctx.table("ipos_sales")` | A polars DataFrame of the rows this run staged for that table |
| `ctx.date` | The anchor date being audited |
| `ctx.lookback_days` | How far back this rule reads (1 = that day, 5 = T-5 to T) |
| `ctx.log("...")` | Append a note to the run summary |
| `ctx.count_rows("name")` | Row count of a table |

There are no file paths and no query strings. A rule cannot reach anything that
was not staged for its run, so `mount/Rules_Executions/<run_id>/` is a true record of
what it read rather than a copy made afterwards.

Column types are inferred from the CSV: `invoice_value` arrives as a float,
`is_void` as a bool. Timestamps are strings until you parse them:

```python
pl.col("invoice_ts").str.to_datetime("%Y-%m-%d %H:%M:%S").dt.hour()
```

Referring to an unregistered datasource, or one that was not staged, raises
immediately with the list of what was actually available.

---

## 4a. Schedules — friendly cadence, never cron

A rule can have **many** schedules. A regulatory area may need "06:30 daily" *and* "month-end at
07:00" — requirement ABM #30 says regulation detail is updated "in the beginning of the year",
which is a monthly cadence on the same rule that also runs daily.

Users never see a cron expression. `app/engine/frequency.py` is the only module that knows cron.

| You pick | Shows as | Derived cron |
|---|---|---|
| Every N hours | "Every 6 hours" | `00 */6 * * *` |
| Daily + time | "Daily at 06:30" | `30 06 * * *` |
| Weekdays + time | "Weekdays at 06:30" | `30 06 * * 1-5` |
| Weekly + days + time | "Mon, Wed, Fri at 07:00" | `00 07 * * 1,3,5` |
| Monthly + day + time | "Monthly on day 28 at 07:00" | `00 07 28 * *` |
| Advanced | raw, behind a disclosure | as entered |

### Constraints that are enforced, with reasons shown in the UI

| Rule | Why |
|---|---|
| **Monthly capped at day 28** | Cron can't express "last day of month", and days 29–31 **silently skip months**. A month-end control that skips 11 months of the year is worse than useless — it looks like it ran. Verified: day 28 fires in all 12 months. |
| **Hourly intervals limited to 1,2,3,4,6,8,12** | Anything else drifts — "every 7 hours" gives some days three runs and others none, and stays permanently unaligned to the clock. |
| **Weekdays must be 0–6** | Range-checked *before* any modulo, so `9` is rejected rather than silently becoming Tuesday. |
| **`CUSTOM` is parsed, not trusted** | `croniter.is_valid` is lenient about malformed input, so we construct an iterator and demand a next value. |

### Preview before you save

`POST /api/rule-schedules/cadence-preview` returns the derived cron **and the real next fire
times**, so nobody discovers a bad schedule by waiting a week for it:

```
Daily at 06:30 (Asia/Dubai)
  next: Sun 18 Oct 06:30 · Mon 19 Oct 06:30 · Tue 20 Oct 06:30
```

---

## 4b. Validation gate

A rule must pass static validation before it can be activated, so a broken rule never reaches an
unattended 06:30 scheduled run.

```
draft ──validate──▶ valid ──activate──▶ active ──pause──▶ paused
  ▲                                                  │
  └──────────── edit (new version) ──────────────────┘
```

Editing the source resets `validation_status` to `unvalidated` — the old verdict no longer applies.
Activation returns **409** until validation passes.

Validation is static and cheap, so it runs on every save:

| Check | How |
|---|---|
| Module imports and compiles | `importlib` + `ast.parse` |
| Defines `run(ctx)` | module attribute |
| No banned imports / calls | **AST scan** — `os`, `sys`, `subprocess`, `socket`, `open()`, `eval` |
| Every table exists for this entity | union of declared `DATASOURCES` **and** every `ctx.table("x")` found in the source |
| Declared entity matches the DB | warn only |

That union is what the run folder stages. It matters because the declared list
is a claim and the call sites are the truth: a rule that calls
`ctx.table("bc_vendors")` from a LAD rule has to fail validation, and it does.

---

## 4c. How a run becomes a ticket

There is no delivery policy to configure. The whole rule is one sentence:

```
rule ran  +  rows returned  ->  one ticket holding those rows
rule ran  +  no rows        ->  no ticket
rule failed to run          ->  no ticket, error on the run
```

Delivery decisions live in the rule file, which is the thing a developer
actually edits. If a rule wants one ticket per store rather than one per run, it
groups its own rows and returns a `store_id` column. That keeps `ticketing.py`
free of any knowledge about rules — it reads a run and nothing else, so it can
be exercised without a scheduler, a cadence or a rule object.

**There is no dedupe.** A breach that is still happening produces another ticket
on the next run. That is deliberate: *"it is still breaching today"* is the fact
an auditor needs, and hiding it behind a closed ticket is how a real problem
stays unfixed. If the volume becomes a problem, the smallest fix is to keep
`occurrences` and bump it while the previous ticket for that code is still
`open` — one column, no modes.

To mute a rule without stopping it, set `status = paused`. That stops the runs
too, which is the honest behaviour.

### The date range

`lookback_days` is the only range knob, and it is one integer:

| Value | Reads |
|---|---|
| `1` | the anchor day only |
| `5` | T-5 to T |

The **anchor** comes from the schedule (`data_lag_days`, default 1 = yesterday).
The rule says how far back from that anchor to read. Each number has one job.

```python
NAME        = "LAD-CA-03 Last-hour of trading value spike"
ENTITY      = "LAD"
DATASOURCES = ["ipos_sales"]
```

### Binding a rule to its datasources

On the rule editor, a **Datasources** picker sits between the metadata and the
Python source. It lists the tables registered for that entity — from the
catalogue an admin maintains on the Data Sources screen — and writes the
selection into the `DATASOURCES` constant above.

It writes into the **file**, not a database column, and that is deliberate: the
runner reads the file, so a column beside it would be a second source of truth
that could quietly disagree with what actually runs.

Three things it tells you that a declaration alone would not:

| Message | Means |
|---|---|
| **not registered for this entity** | the table was removed, or belongs to another entity — validation will refuse the rule, and the rule would fail on its next run |
| **read but not declared** | `ctx.table("x")` names a table the constant omits — add it so validation covers it |

A rule whose declaration cannot be parsed is reported rather than rewritten: the
picker shows what the code actually reads and leaves the file alone.

Declared dependencies are surfaced in the UI, so *"this rule needs `lad.last_hour_ratio`, which does
not exist"* is a warning **before** a scheduled run silently defaults it.

---

## 5. Datasources & cross-system joins

The catalogue lives in the **`datasources` table**, and an admin owns it from the **Data Sources**
screen. It is not code.

The shared contract with the ETL team is deliberately tiny — a folder and a
filename. They push CSVs to `mount/SourceData/<ENTITY>/<date>/<name>.csv` and know nothing about this
app. The app reads that folder on demand, reports what it finds, and an admin decides what to register:

```
ETL → CSVs land → Data Sources screen → admin registers or removes → rules may read them
```

Discovery reads the CSV header, so **columns and types are read from the file the ETL actually
delivered** rather than typed by hand. Registering a table is the moment a file stops being somebody
else's problem and becomes something rules may reference as `ctx.table("name")`.

| Action | Effect |
|---|---|
| **Register** | the table becomes referenceable by new rules |
| **Remove** | deletes the registration. Refused while any active rule reads the table |

Removal is refused outright while an active rule reads the table, and the refusal names those rules.
There is no "retired" middle state: a table is either registered or it is not. That is deliberate —
removing a table breaks every rule that reads it on its very next run, not eventually, and a reversible
version that breaks rules the same way only added a state and a reassuring message that was untrue.

The CSV on disk is never touched. Removing a registration says *"we no longer consume this"*; whether
the ETL keeps producing it is a conversation with the other team.

A rule only ever sees its own entity's tables, so a Laduree rule cannot accidentally read ABM data.

| Entity | System | Tables |
|---|---|---|
| **LAD** | iPOS (SaaS) | `ipos_sales`, `ipos_items`, `ipos_voids` |
| **LAD** | Azure Synapse | `syn_inventory`, `syn_recipes`, `syn_wastage`, `syn_stock_transfers` |
| **LAD** | HRMS | `hr_employees`, `hr_attendance` |
| **ABM** | iPOS | `ipos_sales`, `ipos_price_overrides`, `ipos_discounts` |
| **ABM** | HRMS | `hr_employees`, `hr_attendance` |
| **ABM** | Business Central | `bc_purchase_orders`, `bc_goods_receipts`, `bc_vendors`, `bc_customer_credit` |

### The sample data is deliberately relatable

Shared keys, **not** remapped ones — that is what lets one rule join three systems:

| Key | Connects |
|---|---|
| `employee_id` | HRMS `hr_employees` ↔ iPOS `ipos_sales.staff_id` ↔ BC `approved_by` |
| `store_id` | consistent across every system |
| `sku` | iPOS ↔ Synapse inventory / recipes / wastage |
| `po_number` | BC `bc_purchase_orders` ↔ BC `bc_goods_receipts` |
| IBAN | HRMS `hr_employees.bank_iban` ↔ BC `bc_vendors.vendor_bank_iban` |

Two of the four seeded rules prove this works:

- **`ABM-CA-22`** joins HRMS employees to Business Central vendors on the IBAN — a payments-fraud
  indicator (requirements ABM #22/#23).
- **`LAD-CA-06`** joins iPOS activity to HRMS employment status to find exited staff still
  transacting (requirement Laduree #9).

---

## 6. Roles

Two roles, seeded once from `.env` and then bcrypt-hashed into PostgreSQL.

| Capability | `admin_user` | `audit_user` |
|---|:---:|:---:|
| View rules / schedules / runs / tickets | ✅ | ✅ |
| **Acknowledge / dismiss / reopen a ticket** | ✅ | ✅ |
| Trigger a manual run or re-run | ✅ | ✅ |
| Create / edit / pause a rule | ✅ | ❌ |
| Generate a rule from a prompt | ✅ | ❌ |
| Create / edit a schedule | ✅ | ❌ |

Clean split: **auditors consume and acknowledge; admins author and govern.** This also satisfies
requirement ABM #30 — *"regulation detail to be updated in the beginning of the year with
restricted access to modify."*

Enforced in the backend (`require_admin` → HTTP 403) and mirrored in the navigation.

---

## 7. Getting started

### Prerequisites

| Tool | Version used |
|---|---|
| Python | 3.14.7 |
| Node.js | 24.21.0 |
| PostgreSQL | 18.6 (running on `localhost:5432`) |

> **Note:** `psql` is not on `PATH`. It lives at
> `C:\Program Files\PostgreSQL\18\bin\psql.exe`.

### Step 1 — create the database

The database name contains underscores and mixed case, so it **must be quoted** or Postgres will
fold it to lowercase:

```sql
CREATE DATABASE "ABM_Audit_Portal";
```

### Step 2 — backend

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Step 3 — run

```bash
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Missing tables are created on startup, and the two users plus baseline parameters are seeded.

- API → http://127.0.0.1:8000
- Swagger → http://127.0.0.1:8000/docs
- Health → http://127.0.0.1:8000/health (reports the table list)

### Step 4 — sample data

```bash
.\.venv\Scripts\python.exe scripts\make_sample_data.py
```

Generates `mount/SourceData/` for both entities across 6 days, with **deliberately planted breaches**:

| Date | Laduree | Al Boom Marine |
|---|---|---|
| 2026-10-15 | clean | vendor/employee bank match |
| 2026-10-16 | **last-hour spike** (LAD-CA-03) | vendor/employee bank match |
| 2026-10-17 | **single-staff cash dominance** (LAD-CA-04) | vendor/employee bank match |
| 2026-10-18 | **both** + ex-staff POS activity (LAD-CA-06) | vendor/employee bank match |
| 2026-10-19 | clean | vendor/employee bank match |
| 2026-10-20 | clean | vendor/employee bank match |

Without planted breaches you cannot tell a working rule from a broken one.

Person and company names come from Faker (`en_GB`), seeded per key so the same
record gets the same name on every regeneration. That matters more than it looks:
an earlier version wrote `ABM Employee 0007` and `Laduree Staff 0042`, which
differ from one another by a single digit and therefore score **93%** under
character similarity. `ABM-CA-22` flagged **87 unrelated pairs** against a real
answer of 3, so the fixture could not tell a working control from a broken one.
Real names put the highest-scoring unrelated pair at **75%**, and the control now
returns exactly the 3 planted collisions.

Faker is pinned in `requirements.txt` for the same reason: an unpinned upgrade
would silently change every name, and with it every planted-breach assertion.

### Step 5 — frontend

```bash
cd frontend
npm install
npm run dev
```

→ http://127.0.0.1:5173

Vite proxies `/api` to the backend, so the browser sees a single origin in development.

### Step 6 — sign in

| Username | Password | Role |
|---|---|---|
| `admin_user` | `Admin@2026` | admin |
| `audit_user` | `Audit@2026` | auditor |

---

## 8. Project layout

```
app/
├── README.md                          the product: what it is and how it works
├── AGENTS.md                          how to change it without breaking it
├── mount/                           external data: read by the app, never owned
│   ├── SourceData/                  <ENTITY>/<date>/<table>.csv (generated; gitignored)
│   │   ├── LAD/2026-10-15/ipos_sales.csv ...
│   │   └── ABM/2026-10-15/bc_vendors.csv ...
│   └── Rules_Executions/            one folder per run (gitignored)
│       └── 000123/                  rule.py + metadata.json + the CSVs it read
├── backend/
│   ├── .env / .env.example
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                 app factory, health, dashboard, user bootstrap
│   │   ├── settings.py             env config
│   │   ├── db.py                   async engine + schema check
│   │   ├── models.py               9 tables
│   │   ├── schemas.py              API contracts + THE RULE CONTRACT
│   │   ├── security.py             bcrypt + JWT
│   │   ├── api/
│   │   │   ├── deps.py             auth + role gating
│   │   │   ├── auth.py  rules.py  schedules.py  runs.py  tickets.py
│   │   │   └── datasources.py      register / remove + overview + row preview
│   │   └── engine/
│   │       ├── catalog.py          THE CONTRACT: what an entity may read
│   │       ├── discovery.py        what the ETL landed, and its first rows. Filesystem only.
│   │       ├── datasources.py      client entities only (LAD/ABM and their systems)
│   │       ├── ctx.py              RuleContext: a table -> a polars DataFrame
│   │       ├── snapshot.py         stage one run's inputs. No ORM, no DB.
│   │       ├── loader.py           rule file load/save
│   │       ├── runner.py           THE ENGINE
│   │       ├── frequency.py        friendly cadence <-> cron (only place cron exists)
│   │       ├── validation.py       static validation gate
│   │       ├── ticketing.py        run -> one ticket. Imports no Rule.
│   │       ├── replay.py           re-run a captured folder. No DB, no network.
│   │       ├── scheduler.py        APScheduler wiring
│   │       └── rule_generator.py   prompt -> draft rule
│   ├── rules/
│   │   ├── _template.py
│   │   ├── rule_1.py   LAD-CA-03   last-hour trading spike
│   │   ├── rule_2.py   LAD-CA-04   single-staff cash dominance
│   │   ├── rule_3.py   ABM-CA-22   vendor/employee bank match
│   │   └── rule_4.py   LAD-CA-06   ex-staff still transacting
│   └── scripts/
│       ├── make_sample_data.py     generate SourceData with planted breaches
│       ├── smoke_test.py           end-to-end test suite
│       └── check_licenses.py       license gate
└── frontend/
    ├── vite.config.ts              dev proxy to :8000
    └── src/
        ├── App.tsx                 Admin + resources
        ├── dataProvider.ts         the ONLY module that talks to the API
        ├── authProvider.ts
        ├── theme.ts
        ├── components/ui.tsx       chips, JSON block, code block, rows table
        ├── pages/       Login.tsx  Dashboard.tsx
        └── resources/   Rule.tsx  Run.tsx  Ticket.tsx  Meta.tsx
```

---

## 9. Configuration

`backend/.env` (gitignored). `.env.example` is the committed template.

```ini
# --- Database ---
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=ABM_Audit_Portal

# --- Auth ---
JWT_SECRET=<random>          # auto-generated ephemeral secret if blank
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_HOURS=8

# --- Bootstrap users (seeded once) ---
ADMIN_USERNAME=admin_user
ADMIN_PASSWORD=Admin@2026
AUDITOR_USERNAME=audit_user
AUDITOR_PASSWORD=Audit@2026

# --- LLM for rule generation (any OpenAI-compatible endpoint) ---
LLM_BASE_URL=                 # blank disables prompt -> rule generation
LLM_API_KEY=
LLM_MODEL=

# --- Paths ---
SOURCEDATA_DIR=../mount/SourceData
RUNS_DIR=../mount/Rules_Executions
RULES_DIR=./rules

# --- Guardrails ---
MAX_ROWS_IN_TICKET=500

# --- CORS ---
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

---

## 10. API reference

All endpoints except `/health`, `/api/auth/login` and `/api/dashboard` require
`Authorization: Bearer <token>`.

### Auth
| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/auth/login` | — | `{username, password}` → JWT + user |
| GET | `/api/auth/me` | any | Current user |

### Rules
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/rules` | any | `?page&perPage&sort&order&status&entity&q` |
| GET | `/api/rules/{id}` | any | One rule |
| GET | `/api/rules/{id}/source` | any | Python source |
| GET | `/api/rules/{code}/versions` | any | Full version history |
| POST | `/api/rules` | admin | Create (bumps to v1) |
| PUT | `/api/rules/{id}` | admin | Update; new `source_code` → new version |
| POST | `/api/rules/{id}/rollback/{version}` | admin | Roll back = new version from old |
| DELETE | `/api/rules/{id}` | admin | Delete |
| POST | `/api/rules/{code}/test` | any | Run against a date (`{data_date}`) |
| POST | `/api/rules/{id}/validate` | any | Run the static gate, store the verdict |
| GET | `/api/rules/{id}/validate-report` | any | Last stored verdict without re-running |
| GET | `/api/rules/generate/status` | any | Is the LLM configured? |
| GET | `/api/rules/generate/context?entity=LAD` | any | What the LLM would be shown |
| POST | `/api/rules/generate` | admin | Prompt → **draft** rule |

### Schedules / Runs / Tickets / Params / Datasources
| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/rule-schedules/cadence-options` | any | Everything the frequency picker needs |
| POST | `/api/rule-schedules/cadence-preview` | any | Derived cron + real fire times, before saving |
| GET/POST | `/api/rule-schedules` | any / admin | List (`?rule_code=`) / create |
| GET/PUT/DELETE | `/api/rule-schedules/{id}` | any / admin | Read / update / delete |
| GET | `/api/rule-schedules/cron/jobs` | any | Live APScheduler jobs |
| GET | `/api/rule-runs` | any | `?status&outcome&rule_code&page&perPage&sort&order` — `status` filters execution, `outcome` filters findings; neither implies the other |
| GET | `/api/rule-runs/summary` | any | `{running, completed, failed, breaches, cleared}` |
| GET | `/api/rule-runs/{id}` | any | One run |
| POST | `/api/rule-runs/{id}/rerun` | any | Re-run same rule version + data date |
| GET | `/api/tickets/grouped` | any | `?group_by=rule_code\|severity\|status` — grouped across the **whole** filtered set, paginated by group, so counts are never fragmented |
| POST | `/api/tickets/acknowledge-all` | any | Scoped bulk acknowledge — one transaction, filters echoed back |
| GET | `/api/tickets` | any | `?status&severity&rule_code` |
| GET | `/api/tickets/summary` | any | Counts + open by severity |
| GET | `/api/tickets/{id}` | any | One ticket |
| POST | `/api/tickets/{id}/acknowledge` | **any** | `{status, note}` |
| POST | `/api/tickets/{id}/reopen` | any | Back to open |
| GET | `/api/datasources` | any | Registered tables (+ `?entity=`) |
| GET | `/api/datasources/entities` | any | The two entities and their systems |
| GET | `/api/datasources/overview?entity=&data_date=` | any | **One call** — registered tables, what the ETL delivered, and which rules read each |
| GET | `/api/datasources/discovered?entity=&data_date=` | any | Just what is on disk, read from the CSV headers |
| GET | `/api/datasources/preview?entity=&name=&data_date=&limit=10` | any | First rows of one landed file, plus the file's true row count |
| GET | `/api/datasources/available-dates?entity=LAD` | any | Day folders present, plus the latest |
| POST | `/api/datasources` | admin | Register a table. Columns default to the file's header |
| DELETE | `/api/datasources/{id}` | admin | Remove a registration. 409 naming the rules, while any active rule reads it |
| GET | `/api/dashboard` | — | Aggregated counters (unauthenticated, for the login screen) |

---

## 11. Writing a rule

Copy `backend/rules/_template.py` to `rules/<CODE>_v1.py`.

```python
import polars as pl

LAST_HOUR = 23
LAST_HOUR_RATIO_LIMIT = 2.0
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def run(ctx) -> dict:
    limit = LAST_HOUR_RATIO_LIMIT

    base = (
        ctx.table("ipos_sales")
        .with_columns(
            hour=pl.col("invoice_ts").str.to_datetime(TS_FORMAT, strict=False).dt.hour(),
        )
        .filter((~pl.col("is_void")) & pl.col("item_group").is_in(["RETAIL", "BAR"]))
    )

    if base.height == 0:
        return {"status": "success", "error_message": None,
                "summary": f"No retail/bar invoices on {ctx.date}",
                "metrics": {}, "rows": []}

    hourly = base.group_by("hour").agg(pl.col("invoice_value").sum())
    last_hour = float(hourly.filter(pl.col("hour") == LAST_HOUR)["value"].sum() or 0)
    hourly_avg = float(hourly.filter(pl.col("hour") < LAST_HOUR)["value"].mean() or 0)
    ratio = (last_hour / hourly_avg) if hourly_avg else 0.0

    if ratio <= limit:
        return {"status": "success", "error_message": None,
                "summary": f"{ratio:.2f}x the hourly average, within {limit}x",
                "metrics": {"ratio": round(ratio, 3), "limit": limit}, "rows": []}

    rows = (
        base.filter(pl.col("hour") == LAST_HOUR)
        .sort("invoice_value", descending=True)
        .select("invoice_id", "invoice_ts", "store_id", "staff_id", "invoice_value")
        .head(200)
        .to_dicts()
    )

    return {"status": "success", "error_message": None,
            "summary": f"Last hour {ratio:.2f}x the hourly average (limit {limit}x)",
            "metrics": {"ratio": round(ratio, 3), "limit": limit},
            "rows": rows}
```

Register it via `POST /api/rules` (the server writes the file and creates version 1), or use the
**Generate rule from prompt** button in the UI.

### Guidelines

- Declare thresholds as module constants in UPPER_CASE with a comment saying what they measure.
  Saving the file creates a new rule version, so a threshold change is recorded rather than lost.
- Derive your own helper columns with `with_columns(...)`. Nothing is pre-computed: the hour of a
  timestamp, whether a tender is cash, whether a person has exited are all written in the rule.
- An empty frame is a normal outcome. Say so in the summary and return no rows — do not raise.
- A source file that failed to land is handled by the platform, not the rule: the run records
  `error` and names the missing file, rather than reporting a clean day that was never audited.
- Only import from `polars`, `json`, `datetime`, `statistics`, `math`, `decimal`, `difflib`,
  `collections`, `itertools`, `functools`, `re`, `typing`.
- Never import `os` / `sys` / `subprocess` / `socket`, never call `open()`.

### Reproducing a run

Every execution folder is self-contained, so a failure can be re-run against the exact bytes
it failed on:

```
python -m app.engine.replay 123
python -m app.engine.replay 123 --save fixed.py    # edit the code that actually failed
```

No database, no scheduler, no network. The tool compares the replayed outcome against the
recorded `result.json` and says plainly whether it still reproduces.

### Reading the date range

`lookback_days` is the only range knob, and it is one integer. The **anchor** comes from the
schedule (`data_lag_days`, default 1 = yesterday); the rule says how far back from that anchor
to read. `1` is the anchor day, `5` is T-4 to T. Days that do not exist are skipped, not an
error — a lookback reaching back before the data starts still audits what there is.

---

## 12. Generating rules from a prompt

Admin only. Available from **Rules → Generate rule from prompt**.

1. Pick the client entity (the model only sees that entity's datasources).
2. Describe the control in plain language.
3. The model receives the datasource registry **with real column samples from the target date**
   plus the rule contract and a reference implementation, so it cannot invent columns.
4. Output is structurally validated (`def run(ctx)` present, both statuses used, no banned
   imports) and stored as a **draft**.
5. A human reads the source, runs a test against a real date, then promotes to `active`.

The generated rule is never auto-approved. For audit tooling, a human should read the logic
before it raises tickets.

Configure any OpenAI-compatible endpoint — Azure AI Foundry, Azure OpenAI, Ollama, MiniMax:

```ini
LLM_BASE_URL=https://your-endpoint/v1
LLM_API_KEY=sk-...
LLM_MODEL=your-model
```

---

## 13. Licensing

Every dependency is permissively licensed and the rule is **enforced, not asserted**:

```bash
.\.venv\Scripts\python.exe scripts\check_licenses.py
```

Current result: **52 permitted, 1 review, 0 unknown, 0 blocked.**

| Layer | Package | License |
|---|---|---|
| API | `fastapi` | MIT |
| | `uvicorn` | BSD-3-Clause |
| | `pydantic` | MIT |
| DB | `sqlalchemy` | MIT |
| | `asyncpg` | **Apache-2.0** |
| Sched | `apscheduler` | MIT |
| Auth | `pyjwt` | MIT |
| | `bcrypt` | Apache-2.0 |
| LLM | `openai` | Apache-2.0 |
| Misc | `orjson` | Apache-2.0 **or** MIT |
| | `croniter` | MIT |
| Rules | `polars` | MIT |
| Dev | `faker` | MIT (sample data only) |
| Frontend | `react`, `react-dom` | MIT |
| | `react-admin` | MIT |
| | `@mui/material` | MIT (core only) |
| | `vite` | MIT |
| | `typescript` | Apache-2.0 |

### The policy, honestly

A 100% "Apache or MIT only" tree is **not achievable**, and claiming otherwise would be
misleading. FastAPI transitively requires Starlette (BSD-3-Clause), uvicorn is BSD-3-Clause, and
`certifi` (via httpx) is MPL-2.0.

BSD-3 and ISC sit in the same permissive tier as MIT — attribution only. The one material
difference is that Apache-2.0 grants an explicit **patent** licence.

So the enforced policy is:

- **Allowed:** Apache-2.0, MIT, BSD-2/3, ISC, PSF, Zlib
- **Blocked:** GPL, AGPL, LGPL, SSPL, BUSL, Elastic, proprietary
- **Review (non-fatal):** MPL-2.0 and anything unclassifiable
- **Blocked regardless:** anything that relicenses later — the Soda Core / Grafana / n8n trap

This blocks every license that has actually bitten a project. Two real examples this avoids:

- **Soda Core** replaced its Apache-2.0 LICENSE with **Elastic License 2.0** in the v4 release
  (Jan 2026); v4.21.0 declares itself "Proprietary" on PyPI.
- **RestrictedPython** (the usual Python sandbox) is **ZPL 2.1**, not Apache/MIT, *and* carries a
  published RCE (CVE-2023-37271). It is excluded twice over.

`psycopg` is **LGPL-3.0** and was replaced by `asyncpg` (Apache-2.0) purely to satisfy this policy.

---

## 14. Power Apps migration path

The frontend was built with this in mind. Three things are already done:

**1. One API client.** `frontend/src/dataProvider.ts` is the only module that talks to the
backend. Swapping it later does not touch a single screen.

**2. Single-origin in dev.** Vite proxies `/api` to the backend, so there is one base URL.

**3. REST-only API.** No websocket, no server push, no file upload.

### Option A — Web View control (cheapest, works today)

Host the production build anywhere (Azure Static Web Apps / App Service) and add a Power Apps
**Web View** control pointing at it. No Power Platform tooling, no connector.

```bash
cd frontend && npm run build     # -> frontend/dist
```

### Option B — Custom connector (native galleries and tables)

Add a Power Automate **custom connector** over the same REST API when you need the data inside
native Power Apps controls. Three constraints must shape the API — they already largely do:

| Constraint | Consequence | Status |
|---|---|---|
| Connectors require **Swagger 2.0**, FastAPI emits OpenAPI 3.1 | Convert and commit `swagger.json` (< 1 MB) | ⬜ not done |
| Power Apps **cannot call your API directly** — every call routes through a Power Automate flow | Design **aggregate** endpoints only, never chatty per-row calls | ✅ `/api/dashboard`, `/api/tickets/summary`, `/api/rule-runs/summary` are aggregates |
| Connectors **do not support client-credentials** | Use API-key or OAuth 2.0 delegated, or front with API Management | ⬜ decide before prod |

### Option C — PCF React control

Compile components into a native Power Apps control with `pac pcf init`. Best UX, most work, and
it locks you into Power Platform tooling. Keep in reserve.

---

## 15. Testing

### End-to-end smoke test

With the API running:

```bash
cd backend
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

**161 assertions** covering: health, both logins, rejection of bad credentials and missing tokens,
RBAC for every admin-only endpoint, both entities and their systems, datasource listing,
**the admin's catalogue workflow** (discover what the ETL delivered, columns read from the CSV header,
register, remove, and the refusal for removing a table live rules read), rule registration,
**validate → activate gate**, source and version round-trip,
per-entity isolation, clean-day pass / breach-day fail for all four rules, both cross-system joins,
ticket numbering, JSON serialisation of timestamps, acknowledge → double-ack rejection → reopen,
**friendly cadence** (preview derivation, timezone correctness, and rejection of day 31,
out-of-range weekdays, non-dividing intervals and garbage cron), **multiple schedules per rule**
including duplicate detection and pause/reactivate deregistration, **the validation gate**
(banned imports, activation refusal, cross-entity table reference inside an SQL string), **alert
policy), **execution and outcome being independent** (a completed run always has an outcome, a
run that did not execute has none, and `?status=` and `?outcome=` filter separately), the run
ledger and the version each run was produced by, dashboard and generation status.

```
RESULT: 161 passed, 0 failed
```

The suite is **idempotent** — run it twice and it still reports 161/161. The deduplication
assertions work on ticket *identity* rather than absolute counts, so a previous run's tickets don't
make the next run fail.

### License gate

```bash
.\.venv\Scripts\python.exe scripts\check_licenses.py   # exit 1 on any blocked license
```

### Frontend

```bash
cd frontend
npm run typecheck
npm run build
```

---

## 16. Known gaps

Honest list of what is **not** done yet:

| Gap | Impact | Notes |
|---|---|---|
| **No schema versioning** | Tables come from `create_all` at startup | Deliberate while there is one disposable dev database — see **[AGENTS.md §1](AGENTS.md)**, which gives the reset recipe. Change a model, drop the database, reseed; do not write an `ALTER TABLE` script. Add Alembic the moment there is a **second environment** or data that cannot be regenerated. |
| **No process-level sandbox** | LLM-generated Python runs in-process | The AST gate blocks `os`/`subprocess`/`socket`/`open()` statically, but that is **not** a sandbox — a determined rule can still exhaust memory or spin a CPU. Acceptable while your devs write rules. **Mandatory hardening** if auditors type prompts and never read the diff: subprocess isolation, read-only mount, no network, rlimits. RestrictedPython is *not* a substitute — see §13. |
| **No pagination beyond `perPage`** | Large tables return capped pages | Fine for dev volumes |
| **No rule deletion from the UI** | API supports `DELETE` | Admin button not wired |
| **No Swagger 2.0 export** | Blocks Power Apps custom connector | §14 option B |
| **No requirements traceability** | The 129 requirement line items are not seeded | Worth doing — it makes the 78%/93%/95% coverage figures in the architecture docs computable |
| **`lookback_days` > 1 untested with data** | Every sample rule uses `1`, so the multi-day window is exercised only by unit-level checks on `days_for()` | Ageing controls (stock in transit, GRN pending) need real multi-day fixtures |
| **Single scheduler process** | Multiple API workers would duplicate jobs | Run **one** worker, or move to Celery/Redis |
| **No alert notification** | Tickets only appear in the UI | Email/Teams via Power Automate when needed |
| **Auth gaps** | See §16 note | No rate limiting, no MFA, token in `localStorage`, `403` incorrectly logs the user out |

### A note on the original "FSCS" label

The source requirements workbook has an `ABM` sheet and an `FSCS` sheet. The FSCS sheet covers
*bar items, recipe management, lab production, events and stock counts* — that is a food-service
audit universe, which matches **Laduree**. This build treats the two client entities as
`LAD` (Laduree) and `ABM` (Al Boom Marine). If FSCS is in fact a third client, it needs its own
entry in `ENTITIES` and `SYSTEMS_BY_ENTITY` in `app/engine/datasources.py`, plus its own
`SourceData/FSCS/` tree. Once that exists, registering its tables needs no code at all — the
admin does it from the Data Sources screen, exactly as for LAD and ABM.