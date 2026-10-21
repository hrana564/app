# Scope Investments — Audit Portal

**Intelligent Continuous Auditing — multiple portfolio companies**

A lightweight continuous-auditing platform. Power Automate extracts from client systems into a
CSV landing zone; this app runs audit rules against it on a schedule, and raises **tickets** that
auditors review and acknowledge.

Rules are **plain Python files**. There is no rule DSL, no new language to learn, and no engine
between you and the data.

The portal audits two unrelated clients — **Client A** and **Client B**. Every
screen is scoped to one of them; the switch is in the app bar, and the scope is applied by the
server rather than by the browser filtering what it already downloaded. See
[the client scope](#the-client-scope).

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
12. [Creating a rule with the agent](#12-creating-a-rule-with-the-agent)
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
CLIENT-A   Client A   POS   |  Warehouse  |  HRMS
CLIENT-B   Client B   POS   |  HRMS       |  ERP

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

### The code names the client

A rule's code is its client's code, then the control's own: `LAD-CA-03`. The
prefix follows the rule's **row**, so it is applied when the rule is created
and never written into the file - which is what keeps one file registrable for
whichever client a deployment wants it for.

A ticket's number is derived from the rule's code, so it carries the same
prefix: `LAD-CA-03-TKT-00042`. A reference an auditor reads out of an email
then says whose control it was, without opening the record.

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
07:00" — requirement #30 says regulation detail is updated "in the beginning of the year",
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
`ctx.table("bc_vendors")` from a CLIENT-A rule has to fail validation, and it does.

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
NAME        = "Last-hour of trading value spike"
DESCRIPTION = "Value settled in the last hour of trading exceeds a multiple of the hourly average."
DATASOURCES = ["ipos_sales"]
```

A rule file does **not** say which client it belongs to. That is a property of
the rule's row, chosen when the rule is registered — so one file can be
registered for whichever client the deployment wants it for, and the source
carries no client's name. The catalogue is where a table is bound to a client;
the rule is bound the same way.

The **code** does carry the client's code, because a code is an identifier and
an identifier that says whose control it is is worth having. It is applied
when the rule is created, from the row's client — never written into the file.

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

A rule only ever sees its own entity's tables, so one client's rule cannot accidentally read another's data.

| Entity | System | Tables |
|---|---|---|
| **CLIENT-A** | POS | `ipos_sales`, `ipos_items`, `ipos_voids`, `ipos_discounts` |
| **CLIENT-A** | Warehouse | `syn_inventory`, `syn_recipes`, `syn_wastage`, `syn_stock_transfers` |
| **CLIENT-A** | HRMS | `hr_employees`, `hr_attendance` |
| **CLIENT-B** | POS | `ipos_sales`, `ipos_price_overrides`, `ipos_discounts` |
| **CLIENT-B** | HRMS | `hr_employees`, `hr_attendance` |
| **CLIENT-B** | ERP | `bc_purchase_orders`, `bc_goods_receipts`, `bc_vendors`, `bc_customer_credit` |

### The sample data is deliberately relatable

Shared keys, **not** remapped ones — that is what lets one rule join three systems:

| Key | Connects |
|---|---|
| `employee_id` | HRMS `hr_employees` ↔ POS `ipos_sales.staff_id` ↔ BC `approved_by` |
| `store_id` | consistent across every system |
| `sku` | POS ↔ Warehouse inventory / recipes / wastage |
| `po_number` | BC `bc_purchase_orders` ↔ BC `bc_goods_receipts` |
| IBAN | HRMS `hr_employees.bank_iban` ↔ BC `bc_vendors.vendor_bank_iban` |

Two of the four seeded rules prove this works:

- **`ABM-CA-22`** joins HRMS employees to ERP vendors on the IBAN — a payments-fraud
  indicator (requirements #22/#23).
- **`LAD-CA-06`** joins POS activity to HRMS employment status to find exited staff still
  transacting (requirement #9).

---

## 6. Roles

Four roles. A role is a **named bundle of capabilities**, and the matrix that
says which role holds which capability lives in exactly one place —
`backend/app/permissions.py`. The backend enforces it, and the server sends
the resolved list to the browser so the screens offer exactly what the server
will allow.

| Capability | `admin_user` | `manager_user` | `audit_user` | `viewer_user` |
|---|:---:|:---:|:---:|:---:|
| View rules / schedules / runs / tickets | ✅ | ✅ | ✅ | ✅ |
| Trigger a manual run | ✅ | ✅ | ✅ | ✅ |
| **Acknowledge / dismiss / reopen a ticket** | ✅ | ✅ | ✅ | ❌ |
| Re-run a run | ✅ | ✅ | ✅ | ❌ |
| Create / edit / pause / delete a rule | ✅ | ✅ | ❌ | ❌ |
| Validate a rule | ✅ | ✅ | ❌ | ❌ |
| Generate a rule from a prompt | ✅ | ✅ | ❌ | ❌ |
| Create / edit a schedule | ✅ | ✅ | ❌ | ❌ |
| Register / remove a datasource | ✅ | ✅ | ❌ | ❌ |
| Manage accounts | ✅ | ❌ | ❌ | ❌ |

Clean split: **auditors consume and acknowledge; managers author and maintain;
admins govern the people.** This also satisfies requirement #30 — *"regulation
detail to be updated in the beginning of the year with restricted access to
modify."*

The `manager` role is the split worth having when the estate is maintained by
somebody who is not the portal administrator: everything about the rules,
schedules and catalogue, and nothing about the people.

Enforced in the backend (`require(*capabilities)` → HTTP 403) and mirrored in
the navigation and on every button, so the screen never offers an action the
server would refuse.

The four accounts are seeded once from `.env` and then bcrypt-hashed into
PostgreSQL. The sign-in screen offers them — with their passwords — so a
reviewer can try each role without being given accounts by hand; set
`SHOW_DEMO_ACCOUNTS=false` and the panel disappears, which is what a live
deployment should do. **Further accounts are created from the app** — the
Settings page's Accounts tab, which only an administrator can reach. A new
account cannot change its own role, delete itself, or be the last administrator
removed.

The Settings page's other tab is **Roles**: a read-only tick-and-cross table of
what each role may do, grouped by subject. It is the screen that answers the
question a greyed-out button only implies — *what can the other role do that I
cannot?* — and it is built from `GET /api/roles`, so it is the server's matrix
rather than a picture of one. Everyone can read it, whoever they are.

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
.\.venv\Scripts\python.exe scripts\run_api.py --reload
```

Missing tables are created on startup, and the two users plus baseline parameters are seeded.

- API → http://127.0.0.1:3000
- Swagger → http://127.0.0.1:3000/docs
- Health → http://127.0.0.1:3000/health (reports the table list)

The port is `API_PORT` in `backend/.env`. The server, the smoke test and the
Vite dev proxy all read that one value, so they cannot disagree about where
the API is.

### Step 4 — sample data

```bash
.\.venv\Scripts\python.exe scripts\make_sample_data.py
```

Generates `mount/SourceData/` for both entities across 6 days, with **deliberately planted breaches**:

| Date | Client A | Client B |
|---|---|---|
| 2026-10-15 | clean | vendor/employee bank match |
| 2026-10-16 | **last-hour spike** (LAD-CA-03) | vendor/employee bank match |
| 2026-10-17 | **single-staff cash dominance** (LAD-CA-04) | vendor/employee bank match |
| 2026-10-18 | **both** + ex-staff POS activity (LAD-CA-06) | vendor/employee bank match |
| 2026-10-19 | clean | vendor/employee bank match |
| 2026-10-20 | clean | vendor/employee bank match |

And on **18 and 20 October**, in both estates, two more:

| Planted | What it looks like | Read by |
|---|---|---|
| **early close** | one store's staff clock out at 21:00 and the tills keep selling until the day's end | `LAD-GEN-02` |
| **outsized discounts** | one employee at one store gives four discounts of 400-900, against an estate range of 5-120 | `LAD-GEN-01` |

Without planted breaches you cannot tell a working rule from a broken one.

Person and company names come from Faker (`en_GB`), seeded per key so the same
record gets the same name on every regeneration. That matters more than it looks:
an earlier version wrote `Second Employee 0007` and `First Staff 0042`, which
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
| `manager_user` | `Manager@2026` | manager |
| `audit_user` | `Audit@2026` | auditor |
| `viewer_user` | `Viewer@2026` | viewer |

All four are seeded from `.env` and offered on the sign-in screen, so you can
try each role without being given accounts by hand. Set
`SHOW_DEMO_ACCOUNTS=false` to hide that panel — the accounts still exist.

---

## 8. Project layout

```
app/
├── README.md                          the product: what it is and how it works
├── AGENTS.md                          how to change it without breaking it
├── mount/                           external data: read by the app, never owned
│   ├── SourceData/                  <ENTITY>/<date>/<table>.csv (generated; gitignored)
│   │   ├── CLIENT-A/2026-10-15/ipos_sales.csv ...
│   │   └── CLIENT-B/2026-10-15/bc_vendors.csv ...
│   └── Rules_Executions/            one folder per run (gitignored)
│       └── 000123/                  rule.py + metadata.json + the CSVs it read
├── backend/
│   ├── .env / .env.example
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                 app factory, health, user bootstrap
│   │   ├── settings.py             env config
│   │   ├── db.py                   async engine + schema check
│   │   ├── models.py               9 tables
│   │   ├── schemas.py              API contracts + THE RULE CONTRACT
│   │   ├── security.py             bcrypt + JWT
│   │   ├── permissions.py          THE MATRIX: what each role may do
│   │   ├── api/
│   │   │   ├── deps.py             auth + capability gating (require(*caps))
│   │   │   ├── auth.py  rules.py  schedules.py  runs.py  tickets.py
│   │   │   ├── users.py            the accounts, and the roles they hold
│   │   │   ├── roles.py            GET /api/roles: the whole permission model
│   │   │   ├── assurance.py         the landing page's read model
│   │   │   ├── scope.py            THE ONLY place a query is filtered by client
│   │   │   └── datasources.py      register / remove + overview + row preview
│   │   └── engine/
│   │       ├── catalog.py          THE CONTRACT: what an entity may read
│   │       ├── discovery.py        what the ETL landed, and its first rows. Filesystem only.
│   │       ├── datasources.py      client entities only (CLIENT-A/CLIENT-B and their systems)
│   │       ├── ctx.py              RuleContext: a table -> a polars DataFrame
│   │       ├── snapshot.py         stage one run's inputs. No ORM, no DB.
│   │       ├── loader.py           rule file load/save
│   │       ├── runner.py           THE ENGINE
│   │       ├── frequency.py        friendly cadence <-> cron (only place cron exists)
│   │       ├── validation.py       static validation gate
│   │       ├── ticketing.py        run -> one ticket. Imports no Rule.
│   │       ├── replay.py           re-run a captured folder. No DB, no network.
│   │       ├── scheduler.py        APScheduler wiring
│   │       ├── rule_generator.py   the prompts: the connector contract + the data
│   │       ├── rule_agent.py       the graph: write -> run -> repair
│   │       └── trial.py            run a candidate rule without touching the ledger
│   ├── rules/
│   │   ├── _template.py
│   │   ├── LAD-CA-03_v1.py   last-hour trading spike
│   │   ├── LAD-CA-04_v1.py   single-staff cash dominance
│   │   ├── ABM-CA-22_v1.py   vendor/employee bank match
│   │   ├── LAD-CA-06_v1.py   ex-staff still transacting
│   │   ├── LAD-GEN-01_v1.py  discounts beyond the limit
│   │   └── LAD-GEN-02_v1.py  sales posted after the store closed
│   └── scripts/
│       ├── make_sample_data.py     generate SourceData with planted breaches
│       ├── smoke_test.py           end-to-end test suite
│       ├── run_api.py              start the API on the port .env names
│       ├── check_licenses.py       license gate
│       └── write_third_party_notice.py  writes THIRD_PARTY_NOTICES.txt
├── docs/
│   └── CREATING_A_RULE.md          a guide for the people who write the controls
└── frontend/
    ├── vite.config.ts              dev proxy to the API's port
    └── src/
        ├── App.tsx                 Admin + resources + the Settings route
        ├── Layout.tsx              app bar: name, client switch, ONE profile button
        ├── permissions.ts          the capability vocabulary, and the gates
        ├── entityScope.tsx         THE ONLY place the client is chosen
        ├── dataProvider.ts         the ONLY module that talks to the API
        ├── authProvider.ts
        ├── theme.ts
        ├── components/ui.tsx       chips, JSON block, code block, rows table
        ├── components/RoleMatrix.tsx  the read-only roles table
        ├── pages/       Login.tsx  Dashboard.tsx  Settings.tsx
        └── resources/   Rule.tsx  Run.tsx  Ticket.tsx  Meta.tsx  User.tsx
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

# --- LLM for the rule agent (any OpenAI-compatible endpoint) ---
LLM_BASE_URL=                 # blank disables rule generation
LLM_API_KEY=
LLM_MODEL=
LLM_EXTRA_PARAMS=             # e.g. {"reasoning_effort": "low"} - optional
LLM_MAX_ATTEMPTS=3            # 1 write + 2 repairs
LLM_TIMEOUT_SECONDS=300
TRIAL_TIMEOUT_SECONDS=30

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

All endpoints except `/health` and `/api/auth/login` require
`Authorization: Bearer <token>`. `/api/dashboard` used to be the third exception,
on the grounds that the login screen wanted it. It does not, and an unauthenticated
read of a client's rule counts and open findings is a leak — so it now requires a
token like everything else.

### The client scope

The portal audits two clients, **Client A** and **Client B**.
Every screen is scoped to one of them, chosen once in the app bar and applied by
the server — never by the browser filtering what it already downloaded.

`?entity=CLIENT-A|CLIENT-B` is accepted on rules, runs, tickets (including `/grouped`,
`/summary` and `acknowledge-all`), both summaries, `/api/dashboard` and the
datasource overview. Omit it and the endpoint answers for every client.

Only `rules_master` carries the client. A run and a ticket inherit it from the
rule that produced them, so those endpoints join to reach it — that join lives in
`app/api/scope.py` and nowhere else. See AGENTS.md §2.

### Auth
| Method | Path | Capability | Description |
|---|---|---|---|
| POST | `/api/auth/login` | — | `{username, password}` → JWT + user (the user carries the resolved capability list) |
| GET | `/api/auth/me` | any signed-in user | Current user, with the resolved capability list |

### Rules
| Method | Path | Capability | Description |
|---|---|---|---|
| GET | `/api/rules` | `rules.read` | `?page&perPage&sort&order&status&entity&q` |
| GET | `/api/rules/{id}` | `rules.read` | One rule |
| GET | `/api/rules/{id}/source` | `rules.read` | Python source |
| GET | `/api/rules/{code}/versions` | `rules.read` | Full version history |
| POST | `/api/rules` | `rules.write` | Create (bumps to v1) |
| PUT | `/api/rules/{id}` | `rules.write` | Update; new `source_code` → new version |
| POST | `/api/rules/{id}/rollback/{version}` | `rules.activate` | Roll back = new version from old |
| DELETE | `/api/rules/{id}` | `rules.delete` | Delete |
| POST | `/api/rules/{code}/test` | `rules.test` | Run against a date (`{data_date}`) |
| POST | `/api/rules/{id}/validate` | `rules.write` | Run the static gate, store the verdict |
| GET | `/api/rules/{id}/validate-report` | `rules.read` | Last stored verdict without re-running |
| GET | `/api/rules/generate/status` | `rules.read` | Is the LLM configured? |
| GET | `/api/rules/generate/context?entity=CLIENT-A` | `rules.read` | What the LLM would be shown |
| POST | `/api/rules/generate` | `rules.generate` | Prompt + selection + lookback → a rule **that has been run**. Creates nothing |

### Schedules / Runs / Tickets / Params / Datasources
| Method | Path | Capability | Description |
|---|---|---|---|
| GET | `/api/rule-schedules/cadence-options` | `schedules.read` | Everything the frequency picker needs |
| POST | `/api/rule-schedules/cadence-preview` | `schedules.read` | Derived cron + real fire times, before saving |
| GET/POST | `/api/rule-schedules` | `schedules.read` / `schedules.write` | List (`?rule_code=`) / create |
| GET/PUT/DELETE | `/api/rule-schedules/{id}` | `schedules.read` / `schedules.write` | Read / update / delete |
| GET | `/api/rule-schedules/cron/jobs` | `schedules.read` | Live APScheduler jobs |
| GET | `/api/rule-runs` | `runs.read` | `?status&outcome&rule_code&entity&page&perPage&sort&order` — `status` filters execution, `outcome` filters findings; neither implies the other |
| GET | `/api/rule-runs/summary` | `runs.read` | `?entity` — `{running, completed, failed, breaches, cleared}` |
| GET | `/api/rule-runs/{id}` | `runs.read` | One run |
| POST | `/api/rule-runs/{id}/rerun` | `runs.rerun` | Re-run same rule version + data date |
| GET | `/api/tickets/grouped` | `tickets.read` | `?group_by=rule_code\|severity\|status&entity` — grouped across the **whole** filtered set, paginated by group, so counts are never fragmented |
| POST | `/api/tickets/acknowledge-all` | `tickets.acknowledge` | Scoped bulk acknowledge — one transaction, filters echoed back. Takes `entity`, so it reaches exactly the tickets on screen and no others |
| GET | `/api/tickets` | `tickets.read` | `?status&severity&rule_code&q&entity` |
| GET | `/api/tickets/summary` | `tickets.read` | `?entity` — counts + open by severity |
| GET | `/api/tickets/{id}` | `tickets.read` | One ticket |
| POST | `/api/tickets/{id}/acknowledge` | `tickets.acknowledge` | `{status, note}` |
| POST | `/api/tickets/{id}/reopen` | `tickets.acknowledge` | Back to open |
| GET | `/api/datasources` | `datasources.read` | Registered tables (+ `?entity=`) |
| GET | `/api/datasources/entities` | `datasources.read` | The two entities and their systems |
| GET | `/api/datasources/overview?entity=&data_date=` | `datasources.read` | **One call** — registered tables, what the ETL delivered, and which rules read each |
| GET | `/api/datasources/preview?entity=&name=&data_date=&limit=10` | `datasources.read` | First rows of one landed file, plus the file's true row count |
| GET | `/api/datasources/available-dates?entity=CLIENT-A` | `datasources.read` | Day folders present, plus the latest |
| POST | `/api/datasources` | `datasources.write` | Register a table. Columns default to the file's header |
| DELETE | `/api/datasources/{id}` | `datasources.write` | Remove a registration. 409 naming the rules, while any active rule reads it |
| GET | `/api/dashboard?entity=&window=14` | `assurance.read` | The **assurance read model** — see below |

### Accounts
| Method | Path | Capability | Description |
|---|---|---|---|
| GET | `/api/users` | `users.read` | Every account, with the resolved capability list |
| POST | `/api/users` | `users.write` | Create one — `{username, password, full_name, role}` |
| PATCH | `/api/users/{id}` | `users.write` | Change the role, the name, the active flag, or reset the password |
| DELETE | `/api/users/{id}` | `users.write` | Remove one. 409 for yourself, and for the last active administrator |
| PATCH | `/api/users/me/password` | any signed-in user | Change your own password. Needs the current one |

### The permission model
| Method | Path | Capability | Description |
|---|---|---|---|
| GET | `/api/roles` | any signed-in user | The whole matrix — capabilities with labels and groups, roles with labels, descriptions and their capability lists. The Settings page and the role picker both build from this |

A read of the permission *model* is not a read of the estate, so it asks for a
session rather than a capability — and the people who ask it most are the ones
with least permission to spare.

### The assurance read model

`GET /api/dashboard` is not a set of counters. It answers the three questions a
head of audit asks, from the ledger, in one round trip. Nothing is stored: the
whole payload is derived on read, so it cannot drift from `rule_runs` and
`tickets`.

| Key | What it answers |
|---|---|
| `as_at`, `as_at_age_days` | The date the newest control opinion is about, and how stale it is |
| `controls.assured` / `.total` / `.not_assured` | Live validated controls, all controls, and those never switched on |
| `days[]` | One line per data date: `passed`, `breach`, `errored`, `unchecked`, `exceptions` |
| `offenders[]` | Controls ranked by **days breached**, with exceptions, open findings and missed days |
| `overdue[]` / `gaps[]` | Scheduled controls that missed a day / any control that did not cover every day |
| `open` | Live exposure: exception rows, finding count, oldest age, severity split |
| `longest_open[]` | Open problems grouped by what they say, oldest first, with a repeat count |
| `movement` | Findings and exceptions raised in the window, and how many were acknowledged |

`window` counts **data dates**, not calendar days, so a day the ETL never
delivered cannot appear as a day the controls missed. Every figure inside it is
filtered the same way: runs by `rule_runs.data_date`, and tickets by the
`data_date` of the run that raised them.

**The four states partition the controls.** On every data date,

```
passed + breached + errored + unchecked == assured controls
```

`unchecked` is the shortfall — a control that owed an opinion and did not give
one. It is never folded into a pass, and never dropped from the total, because
that is precisely how a dashboard reports incomplete assurance as complete. The
smoke test asserts the identity on every row, for both clients.

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

## 12. Creating a rule with the agent

Admin only. Available from **Rules → Create a rule**.

The agent does not hand you a file and wish you luck. It writes a rule, **runs
it against the bytes your estate actually holds**, and - when it fails - hands
the error back and writes it again, up to three times. What comes back is a
rule that has been executed, together with what it found.

```
   write the rule
         |
         v
   check it is allowed to run  ---->  not allowed? --> tell the model why
         |
         v
   RUN IT against real data     ---->  it failed?  --> tell the model why
         |                                      |
         |                                      +--> write it again
         v
   it ran, and here is what it found
```

**You choose the scope.** The datasource selection binds: the model is shown
only the tables you tick, and a rule that reads any other is refused before it
runs. You also set the lookback (how far back it reads) and the date it is
tested against.

**Start from a control you already have.** **Start from a template** offers the
programme's own controls - *Sales posted / settled after the store closing
hours* and *Discounts beyond a specified limit by store and by employee* -
written out in the programme's words, with the catalogue's knowledge of each
table filled in and the judgement left in angle brackets. Choosing one brings
its datasources with it, because a control reads the data it is about. A
control whose feed the client has not registered is not offered, and says
which feed is missing.

**Nothing is created until you press Save.** The trial writes no `rule_runs`
row, raises no ticket, and takes no run folder - so a rule that has not been
reviewed by a human never reaches the catalogue, and the trial does not put a
finding in the ledger that nobody asked for. If the agent never gets it
working, no rule is created and the screen says what it tried and why each
attempt failed.

The generated rule is saved as a **draft** and validated. It does not run on a
schedule until you activate it.

Configure any OpenAI-compatible endpoint — Azure AI Foundry, Azure OpenAI, Ollama, MiniMax:

```ini
LLM_BASE_URL=https://your-endpoint/v1
LLM_API_KEY=sk-...
LLM_MODEL=your-model
```

A reasoning model can be slow, because it thinks before it answers. Most
providers let you turn that down, and the portal passes the knob through:

```ini
LLM_EXTRA_PARAMS={"reasoning_effort": "low"}
```

It is optional. An endpoint that does not understand it answers 400, and the
call is retried once without it.

**A guide for the people who write the controls** - how to word the prompt,
how to choose the datasources, what the messages mean, and when to write the
rule yourself - is in **[docs/CREATING_A_RULE.md](docs/CREATING_A_RULE.md)**.

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

**226 assertions** covering: health, both logins, rejection of bad credentials and missing tokens,
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
ledger and the version each run was produced by, **the assurance grid** (the four
states partition the assured controls on every data date for both clients, the grid
ends on the date it reports to, exposure matches the inbox, the severity split adds
back to the headline, grouped problems never exceed the open findings, and nothing
without a cadence is ever called overdue), the dashboard being unreachable without a
token, generation status, and **the client
scope** (CLIENT-A + CLIENT-B must equal the whole on every list and summary, a scoped list carries only that
client's rules, the scoped dashboard agrees with the scoped lists, and the bulk acknowledge reaches
only the client it was given — then puts every ticket back).

```
RESULT: 226 passed, 0 failed
```

The suite is **idempotent** — run it twice and it still reports 226/226. The deduplication
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

### Adding a client

Client identity is configuration. To audit a third client, add it to
`CLIENT_ENTITIES` in the deployment's `.env`:

```
CLIENT_ENTITIES=[{"code":"CLIENT-C","name":"Client C","systems":["ERP","HRMS"]}]
```

Then create its `SourceData/CLIENT-C/` tree and register its tables from the
Data Sources screen. No code change, and the app bar picks it up on the next
load — it reads the list from `GET /api/datasources/entities` rather than
carrying its own copy.

Note the demo estate's two clients have deliberately different upstream systems,
which is what lets one rule join a POS to an ERP and another join a POS to a
warehouse. A new client's systems are descriptive only — what actually matters is
that its tables share keys with the ones a rule already reads.