# AGENTS.md

How to work in this repository. `README.md` documents **what the product is**; this
file documents **how to change it without breaking it**.

Written for whoever works here next, human or otherwise. Every rule below has a
reason attached — the reason is the part that matters when the rule looks
arbitrary.

---

## 1. There are no database migrations

**The models are the only schema. `create_all` builds the whole thing. If you
change a model, drop the database and rebuild it. Do not write an `ALTER TABLE`
script.**

This is deliberate while the product is in development, and it is verified rather
than assumed: creating the schema in an empty database produces all 16
`rule_runs` columns including `outcome` and `rule_version`, so there is nothing a
migration script would have to do.

A migration script was written once, for the `status`/`outcome` split, and
deleted. It was ~120 lines of `ALTER TABLE` and `UPDATE`, it needed a backfill
that derived new values from old ones, and it needed a `--check` mode to prove it
was re-runnable. All of that existed only to avoid `DROP DATABASE`. The models
already said what the schema should be.

### The reset recipe

```bash
# 1. change the model
# 2. drop and recreate the database
"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres \
  -c 'DROP DATABASE IF EXISTS "ABM_Audit_Portal"' -c 'CREATE DATABASE "ABM_Audit_Portal"'

# 3. start the API  -> create_all runs on startup and builds the schema
# 4. reseed
.\.venv\Scripts\python.exe scripts\make_sample_data.py
```

### Why, and when this stops being true

The schema is still moving. A migration script costs real complexity — and real
risk, because a backfill that guesses wrong writes wrong data silently — and buys
nothing while there is exactly one disposable database. The moment that stops
being true is named precisely:

- a **second environment** exists (staging, a client's own server, CI), or
- there is **data that cannot be regenerated**.

At that point add Alembic, and treat this section as history.

### The trap this already caused once

`create_all` **never alters an existing table**. It only creates missing ones. So
after a model change, a database that already exists will silently keep the old
shape — missing column, or a renamed column left orphaned beside its successor.
The app then fails at runtime with `UndefinedColumn` or reads `undefined` in the
UI, and the cause is a schema that was never updated.

**That is the whole reason this section exists.** If something suddenly cannot
find a column you just added, the database is stale: drop it, don't debug it.

---

## 2. Run ledger semantics — the rule that is easiest to break

```
status  = running | success | failed     did the rule EXECUTE?
outcome = pass | fail | NULL            what did the control FIND?
```

These answer different questions and must never be merged into one column again.

| Situation | `status` | `outcome` |
|---|---|---|
| job in flight | `running` | `NULL` |
| snapshot could not be staged | `failed` | `NULL` |
| a declared datasource had no data | `failed` | `NULL` |
| rule `.py` raised | `failed` | `NULL` |
| rule returned `status:"failed"` | `failed` | `NULL` |
| ran, returned rows | `success` | **`fail`** |
| ran, no rows | `success` | `pass` |

A rule that never ran has **no opinion about the control**, so it has no outcome.
That `NULL` is the entire point.

They used to share one column, and it cost the product its credibility: `fail`
meant *"the audit ran perfectly and found a breach"* while `error` covered
everything from a crash to a missing datasource. On the runs list they were the
same red, so a screen full of working audits read as a screen full of faults.

### The mapping lives in exactly one place

`app/engine/ticketing.py`:

```python
run_status_for(rule_status)             -> success | failed
run_outcome_for(rule_status, row_count) -> pass | fail | None
```

Both are pure, and **both are called by `runner.py` and `replay.py`**. Do not
inline the derivation at a second call site. The replay tool's own docstring says
why: *"if the two derived them differently, a replay would report a difference
that is not one."*

---

## 3. Invariants — break these and something will be quietly wrong

| Invariant | Where | Why it exists |
|---|---|---|
| `dataProvider.ts` is the only module that talks to the API | `frontend/src/` | The frontend must stay portable to Power Apps. Swapping the client must not touch a screen. No bare `fetch()` in a component — use the `api` object. |
| `ticketing.py` imports no `Rule` | `backend/app/engine/` | It reads a run and nothing else, so it can be tested without a scheduler, a cadence or a rule object. |
| `frequency.py` is the only module that knows cron | `backend/app/engine/` | Users never see a cron expression. Friendly cadence in, derived cron out. |
| `snapshot.py` does no ORM and no DB work | `backend/app/engine/` | A run's inputs are frozen before the rule sees them. That is what makes `replay` work with no database and no network. |
| The runner and replay share the mapping | see §2 | Otherwise replay reports differences that aren't real. |
| Rules may only `import` from the allow-list | `app/engine/validation.py` | Enforced by AST scan, not convention. |

### Normalise — join, don't duplicate

One source of truth. `tickets.summary` is **not** stored; it is read from the run
that raised it.

The exceptions are deliberate and both are marked in the models:

- `rule_code` is denormalised onto `rule_runs` and `tickets`
- `rule_version` is denormalised onto `rule_runs`

Both exist so a list view never needs a join back to the version history, which
also keeps the future Power Apps connector to single flat calls. `rule_id` stays
the authoritative pointer in both cases.

---

## 4. Rules are plain Python files

No DSL, no new language, no engine between you and the data. The user keeps
complete control of their own logic.

A rule is `backend/rules/<CODE>_v<n>.py` defining `run(ctx)`, returning a dict
matching `app.schemas.RuleResult`:

```python
{"status": "success" | "failed",   # did the rule EXECUTE?
 "error_message": str | None,
 "summary": str, "metrics": dict, "rows": list}
```

Non-empty `rows` means the control found something, and that raises a ticket.
The rule decides its own thresholds, as module constants, because changing one is
a code change — which publishes a new version and records the change instead of
losing it.

Do not add a second way to express a rule.

---

## 5. Running it

```bash
# API - 127.0.0.1:8000
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend - 127.0.0.1:5173 (proxies /api to 8000)
cd frontend && npm run dev

# Sample data with deliberately planted breaches
.\.venv\Scripts\python.exe scripts\make_sample_data.py
```

`admin_user` / `Admin@2026` (admin) · `audit_user` / `Audit@2026` (auditor)

> Without planted breaches you cannot tell a working rule from a broken one.
> `make_sample_data.py` is not fixture noise — it is the only reason the test
> suite means anything.

---

## 6. Verification bar

A change is not done until all of these pass:

```bash
cd backend
.\.venv\Scripts\python.exe scripts\smoke_test.py     # must be 0 failed
.\.venv\Scripts\python.exe scripts\check_licenses.py # must exit 0

cd ../frontend
npx tsc --noEmit
npm run build
```

The smoke test is **idempotent** — run it twice. Assertions work on ticket
*identity*, not absolute counts, so a previous run's tickets don't fail the next.

### Measure, don't eyeball

Layout bugs are invisible to the eye and obvious to a ruler. Before claiming a
table is aligned or a row is one line, measure it in the browser via CDP:

```js
[...document.querySelectorAll('tbody tr')].map(r => Math.round(r.getBoundingClientRect().height))
// -> [41, 41, 41]  one line each.  [41, 62]  something wrapped.
```

Header/cell alignment is the same: compare `getBoundingClientRect().left` of each
`th` against each `td` in the same index. That check found a real bug where a
7-column header sat above full-width group rows.

Chrome recipe — Node 24 has a global `WebSocket`, so **no Puppeteer needed**:

```bash
chrome.exe --headless=new --remote-debugging-port=9222 \
  --user-data-dir=%TEMP%\opencode\chrome-profile --window-size=1920,1150 about:blank
```

Verify at **1920 and 1366**. A table that only works on a wide monitor is broken
for anyone on a laptop.

---

## 7. Tooling traps in this repo

Each of these cost real time. They will cost it again.

| Trap | What to do |
|---|---|
| **PowerShell eats quotes in native commands** | `psql -c 'CREATE DATABASE "ABM_Audit_Portal"'` reaches psql as `CREATE DATABASE ABM_Audit_Portal` and Postgres folds it to lowercase, so the DB is then "not found". Use a name that needs no quoting, or write the SQL to a file. |
| **PowerShell here-strings corrupt Python** | `@"..."@` mangles nested f-string and quote usage. Use the Write tool for any whole file, and `git commit -F <file>` for multi-line messages containing quotes. |
| **MUI `Tooltip` clones its child** | The `aria-label` lands on a wrapping `<span>`, not on the button. Target `button[aria-label=...]`, never the bare attribute. |
| **MUI selects need `mousedown`** | Dispatching `click` on `.MuiSelect-select` does nothing. Dispatch `MouseEvent('mousedown', {bubbles:true})`. |
| **`.RaDatagrid-row` also matches the header row** | react-admin puts `RaDatagrid-row` on `<thead>`'s `<tr>` too. Count `tbody tr`. Getting this wrong silently reports the header text as row one. |
| **Vite can wedge and serve stale modules** | A Babel error leaves the dev server serving old code, so browser checks pass against code you already changed. If a result contradicts an edit, `Invoke-WebRequest http://127.0.0.1:5173/src/...` and confirm the dev server is serving your edit before believing anything. Restart when in doubt. |
| **A bare `<tr>` under `<table>`** | React rejects it and logs a hydration error. Every extra row needs its own `<TableBody>`. |

---

## 8. Dependencies

**Apache-2.0 / MIT family only**, and the rule is **enforced, not asserted** —
`scripts\check_licenses.py` exits 1 on a blocked license.

- **Allowed:** Apache-2.0, MIT, BSD-2/3, ISC, PSF, Zlib
- **Blocked:** GPL, AGPL, LGPL, SSPL, BUSL, Elastic, proprietary
- **Review (non-fatal):** MPL-2.0 and anything unclassifiable
- **Blocked regardless:** anything that relicenses later

A 100% Apache/MIT tree is not achievable and the README says so honestly
(FastAPI pulls BSD-3 Starlette; `certifi` is MPL-2.0). BSD-3 and ISC are the same
permissive tier as MIT — attribution only; the material difference is Apache-2.0's
explicit patent grant.

Pin versions where a silent upgrade would change behaviour. Faker is pinned for
exactly this: an unpinned upgrade would change every generated name, and with it
every planted-breach assertion.

---

## 9. House style

- **No tombstone comments.** No `# removed`, `# no longer used`, `# changed from
  X to Y`. They rot and mislead the next reader. If the old approach is worth
  remembering, put it in the commit message; if it was wrong, let it go.
- Comment the **reason**, not the mechanism. `if x: return y` needs no comment.
  `if x: return y  # a rule that never ran has no opinion, so it has no outcome`
  does.
- No stock phrases. "Let me explain", "It's worth noting", "delve into".
- Match the file you are in. The codebase already has a voice — plain sentences,
  British spelling in prose, tables where a table is clearer.
- Keep the backend thin. Logic that belongs to a rule belongs in the rule.