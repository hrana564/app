"""Bring the run ledger up to the current shape.

Two changes, both about the same table, both derived from what is already there
so the script is safe to run on a database of any age or twice in a row.

1. SPLIT THE STATUS COLUMN IN TWO

       BEFORE   status = running | pass | fail | error
       AFTER    status  = running | success | failed    did the rule EXECUTE?
                outcome = pass | fail | NULL           what did the control FIND?

   The old column answered two questions at once and only ever used one of its
   values for good news. `fail` meant "the audit ran perfectly and found a
   breach", and `error` covered everything from a crash to a missing datasource.
   On a runs list they were the same red.

       pass   -> status=success  outcome=pass
       fail   -> status=success  outcome=fail
       error  -> status=failed   outcome=NULL
       running -> untouched

2. DENORMALISE rule_version ONTO THE RUN

   `rule_code` was already denormalised so list views never need a join back to
   the version history. `rule_version` belongs the same way: a runs screen has
   to be able to say which version produced a run. rule_id stays authoritative.

   Backfilled by joining rule_id to the version row it already points at, so it
   is correct by construction rather than guessed.

    .venv\\Scripts\\python.exe scripts\\migrate_run_ledger.py
    .venv\\Scripts\\python.exe scripts\\migrate_run_ledger.py --check
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

# Running a file inside scripts/ puts scripts/ on the path, not the project
# root, so `app` would not resolve without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import engine  # noqa: E402

# Each statement is independent and keyed on values that only exist BEFORE the
# migration, which is what makes re-running a no-op rather than a corruption.
STEPS = [
    ("add rule_runs.outcome",
     "ALTER TABLE rule_runs ADD COLUMN IF NOT EXISTS outcome VARCHAR(16)"),
    ("index rule_runs.outcome",
     "CREATE INDEX IF NOT EXISTS ix_rule_runs_outcome ON rule_runs (outcome)"),
    ("old 'pass' meant: ran clean",
     "UPDATE rule_runs SET outcome = 'pass', status = 'success' WHERE status = 'pass'"),
    ("old 'fail' meant: ran and found a breach",
     "UPDATE rule_runs SET outcome = 'fail', status = 'success' WHERE status = 'fail'"),
    ("old 'error' meant: never got an answer",
     "UPDATE rule_runs SET outcome = NULL, status = 'failed' WHERE status = 'error'"),
    # Safety net: a run that reports it executed but carries no outcome is a row
    # the backfill missed, and would read as a crash in the UI.
    ("an executed run with no outcome is not trustworthy",
     "UPDATE rule_runs SET outcome = NULL, status = 'failed' "
     "WHERE status = 'success' AND outcome IS NULL"),

    ("add rule_runs.rule_version",
     "ALTER TABLE rule_runs ADD COLUMN IF NOT EXISTS rule_version INTEGER"),
    # rule_id already points at the exact version row that ran, so this reads the
    # answer off the row itself rather than inferring it.
    ("backfill rule_version from the version row rule_id points at",
     "UPDATE rule_runs r SET rule_version = m.version FROM rules_master m "
     "WHERE r.rule_id = m.id AND r.rule_version IS NULL"),
]

CHECK = """
SELECT
    count(*)                                            AS total,
    count(*) FILTER (WHERE status =  'running')         AS running,
    count(*) FILTER (WHERE status =  'success')         AS success,
    count(*) FILTER (WHERE status =  'failed')          AS failed,
    count(*) FILTER (WHERE outcome = 'pass')           AS outcome_pass,
    count(*) FILTER (WHERE outcome = 'fail')           AS outcome_fail,
    count(*) FILTER (WHERE rule_version IS NOT NULL)    AS versioned,
    -- These must all be zero, or the UI would tell a story that isn't true.
    count(*) FILTER (WHERE status = 'success' AND outcome IS NULL)  AS ok_no_outcome,
    count(*) FILTER (WHERE status = 'failed'  AND outcome IS NOT NULL) AS bad_outcome,
    count(*) FILTER (WHERE status NOT IN ('running','success','failed')) AS stale_status,
    count(*) FILTER (WHERE rule_version IS NULL)       AS missing_version
FROM rule_runs
"""


async def main(check_only: bool) -> int:
    async with engine.begin() as conn:
        print("BEFORE")
        for status, n in (await conn.execute(text(
            "SELECT status, count(*) FROM rule_runs GROUP BY status ORDER BY status"
        ))).all():
            print(f"    status={status:<10} {n}")

        if check_only:
            print("\n--check: nothing was written.")
        else:
            print("\nAPPLYING")
            for label, sql in STEPS:
                await conn.execute(text(sql))
                print(f"    ok  {label}")

    async with engine.connect() as conn:
        row = (await conn.execute(text(CHECK))).mappings().one()

    print("\nAFTER")
    for key, value in row.items():
        print(f"    {key:<26} {value}")

    problems = []
    if row["stale_status"]:
        problems.append(f"{row['stale_status']} row(s) still hold a pre-split status value")
    if row["ok_no_outcome"]:
        problems.append(
            f"{row['ok_no_outcome']} executed run(s) carry no outcome - "
            "the UI would read them as a crash")
    if row["bad_outcome"]:
        problems.append(
            f"{row['bad_outcome']} run(s) that failed to execute still carry an "
            "outcome - they never had one")
    if row["missing_version"]:
        problems.append(
            f"{row['missing_version']} run(s) have no rule_version - the detail "
            "page would show 'vundefined'")

    print()
    if problems:
        for p in problems:
            print(f"    FAIL  {p}")
        return 1

    print("    PASS  every row is internally consistent")
    print(f"          execution: {row['success']} completed, "
          f"{row['failed']} failed, {row['running']} running")
    print(f"          control:   {row['outcome_fail']} breaches, "
          f"{row['outcome_pass']} cleared")
    print(f"          version:   {row['versioned']}/{row['total']} runs record "
          "which rule version produced them")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(check_only="--check" in sys.argv)))