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

# 3. empty the run folders - they are numbered from the same counter
Get-ChildItem "mount\Rules_Executions" -Directory | Remove-Item -Recurse -Force

# 4. start the API  -> create_all runs on startup and builds the schema
# 5. reseed
.\.venv\Scripts\python.exe scripts\make_sample_data.py
```

Step 3 is not optional. `rule_runs` restarts its ids at 1 while
`mount/Rules_Executions/` still holds folders `000001`..`000365`, and the
snapshot guard refuses to overwrite one — so every run fails with
`FileExistsError` and the smoke test blames the code. See §3a.

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

### "What does this rule read" also lives in exactly one place

`app/engine/validation.py`:

```python
declared_names(source)     -> the DATASOURCES literal only
referenced_tables(source)  -> every ctx.table("x") / ctx.count_rows("x")
declared_tables(source)    -> the union of both, sorted
```

All three are **pure text**: no file, no database, no catalogue. That is what
lets the same answer come out of two callers with different access — validating a
rule holds the catalogue, while creating or editing one holds only the source
text it was just handed.

`create_rule` and `new_version` both call `declared_tables` on the source they
have just written. They used not to, and `new_version` copied the *previous*
version's list instead. That was wrong twice over: a brand new rule reported no
datasources until somebody pressed Validate, and an edited rule reported the
datasources of a file nobody was running. The rule page then said *"reads no data
sources"* about a rule whose file declared two, and the Data Sources screen's
"used by" answered for the wrong version.

Derive this twice and the two drift invisibly: the rule still runs correctly
while the catalogue and the rule's own page disagree about what it reads.

### The client scope also lives in exactly one place

```
app/api/scope.py:

scoped(stmt, model, entity)   -> the same statement, narrowed to one client
```

Only `rules_master` knows which client a thing belongs to. A run and a ticket
carry no entity of their own — they point at a rule and inherit it — so three of
the five queries that have to filter by client have to **join** to get there.
Writing that join per endpoint is how the dashboard ends up counting one client
while the inbox beside it shows another: the two disagree silently and every number still
looks plausible.

Callers pass the result to **both** the page query and its count query. Narrowing
one and not the other is how `total` starts lying about what the page holds.

The bulk acknowledge takes `entity` too, and that is the one that matters. Its
own docstring already said the action must reach "exactly what is on screen" —
and the client the app bar is scoped to is part of what is on screen. The smoke
test proves it by filtering on a severity both clients use, so an unscoped call
would visibly touch more rows.

### The landing page derives "did the control report?" in exactly one place

```
app/api/assurance.py:

state_of(passed, breached, errored)  -> one control's state on one data date
summarise_day(assured, seen)          -> the four counts for that date
rank_controls(rows)                   -> worst first
```

These are pure functions, no database, and both the endpoint and the smoke test
lean on them. `summarise_day` is the important one: it is why

```
passed + breached + errored + unchecked == assured controls
```

holds on every row. `unchecked` is computed as the **shortfall** rather than
counted separately, so it cannot drift out of step with the total.

Derive this a second time somewhere and the two disagree invisibly. The grid
still draws, every number still looks plausible, and the dashboard starts
reporting incomplete assurance as complete — which is the one thing a head of
audit cannot catch, because a clean-looking audit is exactly what they are
checking for.

Two distinctions the payload depends on, both of which cost real argument to
settle:

- **Assurance is active AND validated.** A control still in draft owes no
  opinion, so counting it as a gap would blame the controls for a control nobody
  switched on. It is reported separately, under `not_assured`.
- **A missed day is overdue only if the control had a cadence.** An on-demand
  control that did not run is a coverage note, not a missed deadline. Without
  this, `CA-22` — which is manual-only — reads as a fault on every day it
  has not run, forever.

**Nothing here is stored.** The payload is derived on read precisely so there is
no summary table to fall out of step with `rule_runs` and `tickets`.

### A partial answer must say it is partial

The grouped inbox caps how many rows it reads, so that a pathological filter
cannot pull an unbounded result set into memory. That cap is on **work**, not on
the **truth**:

```
app/api/tickets.py:

total_tickets  ->  its own COUNT over the same filter  (always the real number)
scanned        ->  how many rows the scan actually read
truncated      ->  total_tickets > scanned
```

It used to report the scanned count as the total and expose `scanned` as a
duplicate of it. Nothing on screen could tell the difference, so an estate with
more than `MAX_GROUP_SCAN` open tickets would show a header claiming 5,000 while
the filter actually matched 12,000 — and the bulk acknowledge behind that
header would touch all 12,000. The numbers looked plausible, which is the only
property a wrong number needs to have.

Two consequences worth keeping in mind:

- **The count and the rows come from different queries on purpose.** If they
  shared one, the truncation would be invisible by construction.
- **`truncated` is a UI obligation, not just a field.** `Ticket.tsx` must show
  it. A truth the endpoint knows and the screen does not say is still a lie the
  user is told.

### A rule is only created after it has run

The agent writes a rule, runs it, and repairs it until it runs. The rule
reaches the catalogue only when a human has read it and pressed Save.

```
app/engine/rule_agent.py:   the graph - write -> run -> repair
app/engine/trial.py:         runs a candidate, touches nothing
app/engine/rule_generator.py: the prompts - the connector contract, and the data
```

Three rules hold this together, and each one exists because breaking it would
be invisible:

| Rule | Why |
|---|---|
| The trial writes nothing to the ledger | No `rule_runs` row, no ticket, no run folder. A trial that raised a ticket would put a finding in the estate that nobody asked for |
| The datasource selection binds | The model is shown only the selected tables, and a rule reading any other is refused **before it executes**. A picker the model may wander outside of is a picker that selects nothing |
| Generation creates no rule row | The agent's output is source and a verdict. The rule row and the file are written by `POST /api/rules`, after a human has read the code |

The trial stages with `capture()` - the production staging path - and runs the
copy that `capture()` froze into the folder. A separate "test mode" would be a
different execution path, and a rule that passes a different path has not been
tested. It stages into `mount/.trials/<token>/` rather than
`mount/Rules_Executions/`, because run folders are numbered from the same
counter as `rule_runs` and a trial that took one would make every later run
fail with `FileExistsError`.

**The failures are classified, because only one of them can be repaired.**

| Classification | Meaning | Repairable? |
|---|---|---|
| `code_error` | raised, or returned `failed` while the data exists | yes |
| `not_allowed` | reads a table outside the selection | yes |
| `banned` | the static gate refused it | yes |
| `data_missing` | the ETL did not land a file | **no** |

An agent that believes it can fix missing data will rewrite working code
until it has nothing left to rewrite. The classification is the difference
between "try again" and "stop and tell the user".

**A template brings its own datasources.** The programme templates in
`frontend/src/components/rulePrompts.ts` name the feeds their control reads,
and choosing one selects them — because a control reads the data it is about,
and a prompt that names a table the user did not pick is a prompt about the
wrong data. A control whose feed the client has not registered is **not
offered**: the menu item says which feed is missing, because a control that
cannot read its data cannot be written, and a trial that fails on missing data
is `data_missing` — the one failure the agent may not repair.

Two implementations of "is this file allowed to run" would be two answers to
the same question, so `scan_source()` in `validation.py` is the only one, and
the trial and the validator both call it. The same is true of the call itself:
`invoke_rule()` in `ctx.py` builds the context and calls `run(ctx)` for both
the runner and the trial, because a trial that tests a different path from
production is not a trial at all.

### The client scope lives in exactly one place in the frontend too

```
src/entityScope.tsx:

useEntityScope()   -> { entity, setEntity }
```

One provider above `<Admin>`, one dropdown in the app bar, and no screen carries
its own copy. Rules and Runs take it as a react-admin **permanent filter**
(`<List filter={{ entity }}>`) rather than a toolbar dropdown, so a screen's
"Clear" button cannot quietly drop the client and leave the list showing both.
The choice is remembered in `localStorage`, because the scheduler and the ETL
work per client and re-picking it on every reload is noise.

---

## 3. Invariants — break these and something will be quietly wrong

| Invariant | Where | Why it exists |
|---|---|---|
| `dataProvider.ts` is the only module that talks to the API | `frontend/src/` | The frontend must stay portable to Power Apps. Swapping the client must not touch a screen. No bare `fetch()` in a component — use the `api` object. |
| `ticketing.py` imports no `Rule` | `backend/app/engine/` | It reads a run and nothing else, so it can be tested without a scheduler, a cadence or a rule object. |
| `frequency.py` is the only module that knows cron | `backend/app/engine/` | Users never see a cron expression. Friendly cadence in, derived cron out. |
| `snapshot.py` does no ORM and no DB work | `backend/app/engine/` | A run's inputs are frozen before the rule sees them. That is what makes `replay` work with no database and no network. |
| The runner and replay share the mapping | see §2 | Otherwise replay reports differences that aren't real. |
| One `declared_tables` for every caller | `app/engine/validation.py` | Validating a rule and writing one must answer "what does it read" identically, or the rule page and the catalogue disagree about it. See §2. |
| One `scoped()` for every query that filters by client | `backend/app/api/scope.py` | Runs and tickets have no entity column; they inherit the rule's. Five endpoints joining for it themselves is how the dashboard counts a different client than the list beside it. See §2. |
| One `summarise_day()` for the assurance grid | `backend/app/api/assurance.py` | `passed + breached + errored + unchecked == assured` has to close, or the landing page reports incomplete assurance as complete. See §2. |
| One `useEntityScope()` for every screen | `frontend/src/entityScope.tsx` | The app bar owns the client. A screen with its own dropdown can disagree with the header above it, and a "Clear" button can drop the scope silently. See §2. |
| One `scan_source()` for every gate | `backend/app/engine/validation.py` | The static gate that decides a rule may run. The validator and the trial both call it, so a candidate cannot be stricter when it is tested than when it is activated. |
| One `invoke_rule()` for every execution | `backend/app/engine/ctx.py` | Builds the context and calls `run(ctx)`. The runner and the trial both call it, because a trial that tests a different path from production is not a trial. |
| The trial touches nothing | `backend/app/engine/trial.py` | No `rule_runs` row, no ticket, no run folder. A trial that raised a ticket would put a finding in the estate that nobody asked for. See §2. |
| One licence policy for gate and notice | `backend/scripts/check_licenses.py` | `write_third_party_notice.py` imports `BLOCKING`/`PERMISSIVE`/`classify` rather than restating them. It once kept its own lists and they drifted within a single commit - `epl`, `cpal` and `commercial` were blocked by the gate and unknown to the notice, which is the one place a copyleft dependency could be declared into compliance. |
| One `SEVERITIES` list for the frontend | `frontend/src/theme.ts` | Three screens sorted and filtered by severity with a copy each, and the ticket inbox held two. Derive from `SEVERITIES` / `SEVERITIES_ASC`; the backend equivalent is `SEVERITY_ORDER` in `app/api/assurance.py`, which the ticket group sort imports. |
| Rules may only `import` from the allow-list | `app/engine/validation.py` | Enforced by AST scan, not convention. |
| The catalogue lives in the `datasources` table, not in `app/` | see §3a | A module under `app/` that also answered "what tables exist" would be a second source of truth, and the two would drift the first time an admin registered something. |
| Consumers take a `Catalog`, never a storage mechanism | `app/engine/catalog.py` | Keeps the ETL, the catalogue, the rules and the runner decoupled. See §3a. |

### 3a. The pipeline, and why each stage is decoupled

```
ETL team ──writes CSVs──> mount/SourceData/<ENTITY>/<date>/*.csv
                                    │
                            engine/discovery.py      filesystem only
                                    │  admin registers what we consume
                                    ▼
                          the datasources table       ← one catalogue
                                    │
                                    ▼
                            engine/catalog.py         the only contract
                                    │
     ┌──────────────┬───────────────┼──────────────┬─────────────────┐
   rules          runs           replay      rule_generator        UI
(validate)    (ctx.table)   (own snapshot)    (LLM prompt)     (admin)
                                    │
                                    ▼
                          engine/rule_agent.py    write → run → repair
                                    │
                                    ▼
                            engine/trial.py         runs a candidate, in a
                                    │               scratch folder, touching
                                    │               nothing that is recorded
                                    ▼
                            the admin reads it, then Save
```

Each stage knows only the stage before it, and the only thing crossing the
boundary is a **table name**.

**The ETL knows nothing about us.** The entire shared contract is a folder
convention: `<ENTITY>/<date>/<name>.csv`. Discovery reads that folder and reports
it; there is no callback, no handshake, and no shared schema. Another team can
rewrite their ETL in another language and this app does not notice.

**The catalogue is data, so an admin owns it.** It used to be eighteen
hardcoded `Source(...)` definitions in `app/engine/datasources.py`, and that was
a trap: the module was the enforcement point for `ctx.table()`, while the
database held a mirror no enforcement read. An admin could retire a table in the
UI and every rule would carry on reading it. The registry now lives in the
`datasources` table, and `app/engine/datasources.py` holds only client identity
(`ENTITIES`, `SYSTEMS_BY_ENTITY`). `scripts/seed_datasources.py` seeds a fresh
database and is never imported by the running app.

**Consumers take a `Catalog`, never a storage mechanism.** `engine/catalog.py`
exposes exactly two methods — `names_for(entity)` and `entries_for(entity)`.
Two implementations satisfy it:

| Implementation | Answers | Used by |
|---|---|---|
| `DatabaseCatalog` | what an admin has registered and not retired | validation, runner, generator |
| `SnapshotCatalog` | what one frozen run was given | replay |

Replay uses `SnapshotCatalog` **on purpose**. Consulting the live catalogue would
mean a run from last week could not be reproduced after a table was retired, and
it would break the "replay works with no database" property. The manifest is the
record of what that run read, so it is the only honest answer.

### Remove, and nothing else

A table is either registered or it is not. There is no third state, so
`Datasource` has no `is_active` column.

There was a `retire` action once — reversible, and refused while live rules read
the table. It was removed because it did not mean what its dialog claimed. The
dialog said the rules "keep running" and would fail only once the ETL stopped
sending the file. They do not: `ctx.table()` consults the catalogue, so the very
next run is refused while the file is still sitting on disk. Retire and remove
broke the same rules in the same way, so retire only added a state and a
reassurance that was untrue.

`DELETE /api/datasources/{id}` is refused with a 409 naming the dependent rules.
That refusal is the whole of the safety, and it is honest: nothing is left
half-removed.

### The reset clears the run folders too

`mount/Rules_Executions/` is numbered from the same counter as `rule_runs`, and
the snapshot guard refuses to overwrite an existing folder. Dropping the database
without emptying that folder leaves new run ids colliding with old folders, and
every run fails with:

```
snapshot failed: FileExistsError: run folder ... already exists
```

So the reset recipe in §1 needs one more line:

```bash
Get-ChildItem "mount\Rules_Executions" -Directory | Remove-Item -Recurse -Force
```

`-Directory`, not a `*` wildcard — the wildcard also takes `.gitkeep`.

Without it the smoke test reports dozens of failures that look like a code bug
and are not one.

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

### `DATASOURCES` is in the file, so the picker edits the file

The Data Sources picker on the rule editor writes `DATASOURCES = [...]` **into the
Python source**. It does not save to a database column, and it must not be changed
to do so.

The runner reads the file. A column beside it would be a second source of truth
that could disagree, and the runner would believe the file while the screen
believed the row. The picker is a convenience over the declaration.

`frontend/src/components/ruleSource.ts` holds that rewriting. It strips docstrings
and comments first, because `_template.py` documents itself with
`ctx.count_rows("name")` — reading raw text offered to bind a table called `name`
that does not exist. A commented-out `DATASOURCES` is not a live declaration
either.

When the constant cannot be parsed, the picker refuses to write rather than
guessing, and says what the code actually reads.

---

## 5. Running it

The API's port is `API_PORT` in `backend/.env` — **one** value. The server that
binds it (`scripts/run_api.py`), the smoke test that calls it and the Vite dev
proxy that fronts it all read it, so none of them can end up pointing
somewhere else.

```bash
# API - 127.0.0.1:3000
cd backend
.\.venv\Scripts\python.exe scripts\run_api.py

# Frontend - 127.0.0.1:5173 (proxies /api to the API's port)
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
npx tsx src/components/ruleSource.check.ts           # must report 0 failures
```

`ruleSource.check.ts` is the exception to the pattern: a plain script, not a
test suite, because this project has no frontend test runner and adding one for
a single file is not a trade worth making. It exists because that file rewrites a
Python rule an auditor wrote — "it looks right in the dropdown" is not evidence,
so the rewrite is asserted against the real `backend/rules/*.py`. It is excluded
in `tsconfig.json` so it never reaches the bundle.

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
| **`check()` in `smoke_test.py` is truthiness, not equality** | `check(label, ok, detail)` prints `PASS` whenever `ok` is merely truthy. So `check("x", offending, [])` **fails when the list is empty** — the one case it was written to catch. Write the comparison yourself: `check("x", not offending, str(offending))`. And `check("x", got, want)` passes on any non-empty `got`, so a mismatch reads green. |
| **The smoke test writes to the ledger** | It POSTs `/api/rules/{code}/test`, and every call is a real execution: a `rule_runs` row, a `mount/Rules_Executions/<id>/` folder, and a ticket if the control breached. Ten passes in a row leave the same control recorded 28 times against the same data date, and the landing page's exception counts inflate by the same factor — 285 open exceptions reads as 4,851. Fine for correctness, misleading for anything you want to *look* at. |
| **`captureBeyondViewport` screenshots clip the right edge** | The image comes out wider than the layout and text at the boundary looks truncated. It isn't: measure `scrollWidth <= clientWidth` on `.RaLayout-content` and on the app bar before believing a screenshot. |
| **Changing `node_modules` leaves Vite serving the old dep** | `npm install` changes the optimised bundle in `node_modules/.vite`, and the dev server keeps serving what it built before. Stop the server, delete `node_modules/.vite`, start it again — and then *look* at the served module, because a stale dep bundle fails in ways that read like the app is broken. |
| **`npm audit`'s decode-uri-component advisory cannot be fixed here** | Seven moderate advisories, all one problem: `decode-uri-component` ≤ 0.4.2, reached through `query-string` → `react-admin`. Both overrides are dead ends. Forcing `decode-uri-component` to 0.5.0 breaks at RUNTIME — it is ESM-only and `query-string@7` `require()`s it, so `decodeComponent is not a function` and every URL with a query string dies. Forcing `query-string` to 9.x breaks at BUILD time — v9 dropped the named `parse`/`stringify` exports react-admin imports. The fix is upstream, in react-admin's own `query-string` range. Do not spend the afternoon again. |
| **`GET /v1/models 404` in the API log is not the app misbehaving** | The OpenCode CLI probes the local ports it knows to be OpenAI-compatible model servers, every thirty seconds: it asks `/health`, and when that answers 200 it assumes a model server and asks for `/v1/models`. The Audit Portal answers `/health` because it is a health endpoint, and has no `/v1` routes at all, so the probe 404s. The User-Agent says `opencode/latest/.../cli`. That is why the API is on **3000** and not 8000: 8000 is on OpenCode's probe list and 3000 is not. Verified by listening on nine candidate ports at once — only 1234 (LM Studio) and 11434 (Ollama) were knocked on. Do not move the port back to 8000 to "tidy" the log. |
| **The venv is not what `requirements.txt` resolves to** | A long-lived `.venv` drifts: it kept three stale transitives that a clean install would not produce, and four orphan packages a clean install would not have. Neither shows up in `pip check`, which only looks at declared requirements. The witness is a clean venv: build one from `requirements.txt` and diff `pip list --format=freeze` against the working one. They should match exactly. |

---

## 8. Dependencies

**Apache-2.0 / MIT family only**, and the rule is **enforced, not asserted** —
`scripts\check_licenses.py` exits 1 on a blocked license. Both obligations are
separate and both are real: the checker proves nothing in the tree is copyleft,
and `scripts\write_third_party_notice.py` writes the `THIRD_PARTY_NOTICES.txt`
you owe on redistribution. A clean checker does not produce that file.

**The majors are not taken, and `npm outdated` is not a to-do list.** It offers
`react-router` 8, which react-admin does not accept — it depends on
`react-router@^6.28.1 || ^7.1.1` — so installing it breaks the framework. It
offers TypeScript 7 (the native port) and Vite 8, which change no shipped
behaviour. A patch inside the range your manifest already allows is a different
thing entirely — that one you take.

**MUI v9 removed the system props from `Box`, `Typography`, `Stack`, `Grid` and
`Link`.** `alignItems`, `color`, `display` and the rest are no longer props of
the component; they are style, and style goes in `sx`. So `<Stack alignItems="center">`
is now `<Stack sx={{ alignItems: "center" }}>`, and `<Typography color="text.secondary">`
is `<Typography sx={{ color: "text.secondary" }}>`. A `Typography` that has an
`sx` of its own gains the new properties inside it.

That is not a cosmetic change and the compiler will not catch it going the
wrong way: a dropped `alignItems` still compiles, and the row quietly stops
being centred. So when the props move, the screen has to be measured, not
looked at — §6's ruler, at both widths.

Client identity is **configuration, not code** — `CLIENT_ENTITIES` in the
deployment's `.env`, read once in `app/settings.py`. Nothing under `app/` or
`frontend/src/` may name a client, and the frontend gets the list from
`GET /api/datasources/entities` rather than keeping its own copy. A client
name in source is a sale blocker, not a style question.

The demo estate's **rule files** are the one place a client code appears, and
only because a rule's file is named for its code (`LAD-CA-03_v1.py`) and the
code names the client — see below. The file's *contents* name nobody: the
control's logic, its thresholds and the tables it reads. A deployment that
names its clients differently re-files the demo rules under the new code, and
the file follows.

**Which client a rule belongs to is on the rule's ROW, not in its file.** The
file holds the logic and the data binding; the row holds the client, chosen
when the rule is registered. A file that named a client would carry a client's
identity into the source, and would be wrong the moment the same file was
registered for a different client — which is the whole reason the file is
portable. The `ENTITY` constant is still *read*, and a file that declares one
that disagrees with its row is warned about, but nothing asks for one.

**The rule's CODE names the client, and it is derived from the row.**
`LAD-CA-03` says whose control it is, and the ticket number derived from it
(`LAD-CA-03-TKT-00042`) says the same — a reference an auditor reads out of
an email should say where it came from without opening the record. The prefix
is applied in `scoped_code()` when the rule is created, from the row's
client, so it can never be another client's; a code that already carries a
client's prefix has it **replaced** rather than stacked. It is never written
into the file, for the reason above.

Two consequences worth holding onto: the rule's file is named for its code
(`LAD-CA-03_v1.py`), so a suite that reads a demo rule finds it by its base
code rather than by a name that changes with the deployment; and the demo
suite derives the codes it asserts on, so it names no client either.

The same applies to the demo estate's own layout. `ipos_*`, `syn_*`, `bc_*` and
`hr_*` are PRODUCT names — iPOS, Synapse, Business Central — and they are the
demo ETL contract, not a client's identity. The two estates are handed to
whichever clients the deployment names, **in the order `CLIENT_ENTITIES`
declares them**. That order is the mapping, and it is why the seeder uses
declaration order rather than sorting: sorted, a client called ABM sorted
ahead of LAD and was handed the other company's estate, with the other
company's tables under its own name.

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