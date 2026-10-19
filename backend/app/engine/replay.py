"""
Replay a captured run.

    python -m app.engine.replay 123
    python -m app.engine.replay mount/Rules_Executions/000123
    python -m app.engine.replay 123 --save fixed.py

Runs the rule copy and the CSV copies frozen in the run folder. No database, no
network, no scheduler - which is the point: a failure you can reproduce is a
failure you can fix, and this runs while you fix it.

REPRODUCIBILITY IS THE POINT
----------------------------
If the folder has a `result.json` from the original run, this compares the
replayed outcome against it and says plainly whether they agree. A mismatch is
not noise - it means either the rule was edited after that run (in which case
the folder is doing its job and you are looking at a deliberate difference) or
something non-deterministic crept into the rule.

`--save` copies the folder's rule.py out to a working file, so the fix can be
made on the exact code that failed and then published as a new version.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

from app.engine.ctx import RuleContext
from app.engine.datasources import registry_for
from app.engine.loader import load_rule_at
from app.engine.snapshot import RULE_COPY_NAME, Snapshot
from app.engine.ticketing import run_outcome_for, run_status_for

_RULE_MARKER = '"""'


def _resolve_folder(argument: str) -> Path:
    from app.settings import settings

    candidate = Path(argument)
    if candidate.is_dir():
        return candidate
    if candidate.is_file() and candidate.suffix == ".json":
        return candidate.parent

    # A bare run id, or a zero-padded one.
    stem = argument.strip().rstrip("/\\").split("\\")[-1].split("/")[-1]
    if stem.isdigit():
        return Path(settings.runs_dir) / f"{int(stem):06d}"
    return candidate


def _load_result(folder: Path) -> dict | None:
    path = folder / "result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _compare(recorded: dict | None, fresh: dict) -> str | None:
    """Describe how the replay differs from the recorded run, or None."""
    if recorded is None:
        return None

    # The rule's own status, the run's execution status and the run's control
    # outcome are three different things. Derive them the same way the runner
    # does, or a faithful replay would report a difference that is not one.
    fresh_status = fresh.get("status", "success")
    new_rows = len(fresh.get("rows") or [])
    new_status = run_status_for(fresh_status)
    new_outcome = run_outcome_for(fresh_status, new_rows)

    rec_status = recorded.get("status")
    rec_outcome = recorded.get("outcome")
    rec_rows = len(recorded.get("rows") or [])

    if rec_status == new_status and rec_outcome == new_outcome and rec_rows == new_rows:
        return f"reproduces the recorded run ({new_status}, {new_rows} rows)"

    return (
        f"DIFFERS from the recorded run: "
        f"status {rec_status} -> {new_status}, "
        f"outcome {rec_outcome} -> {new_outcome}, "
        f"rows {rec_rows} -> {new_rows}"
    )


def replay(folder: Path, *, save_to: str | None = None) -> int:
    folder = folder.resolve()
    if not folder.is_dir():
        print(f"not a run folder: {folder}", file=sys.stderr)
        return 2

    try:
        snap = Snapshot(
            run_id=0, root=folder, entity="", data_date=date.today(),
            lookback_days=1, rule_code="", rule_version=0, rule_source_file="",
        ).reload()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"cannot read {folder}: {exc}", file=sys.stderr)
        return 2

    print(f"run {snap.run_id}   {snap.rule_code} v{snap.rule_version}   "
          f"{snap.entity} {snap.data_date}   lookback {snap.lookback_days}")
    for name, files in sorted(snap.tables.items()):
        rows = sum(f.rows for f in files)
        days = ", ".join(f.day for f in files)
        print(f"  {name:<16} {rows:>7,} rows   {days}")
    if snap.missing_days:
        print(f"  (no data folder for: {', '.join(snap.missing_days)})")
    if snap.missing_files:
        print(f"  (files that never landed: {', '.join(snap.missing_files)})")

    rule_path = folder / RULE_COPY_NAME
    if save_to:
        shutil.copy2(rule_path, save_to)
        print(f"\nworking copy of the rule written to {save_to}")

    recorded = _load_result(folder)
    try:
        module = load_rule_at(rule_path)
        ctx = RuleContext(
            data_date=snap.data_date,
            lookback_days=snap.lookback_days,
            rule_name=snap.rule_code,
            registered=list(registry_for(snap.entity).keys()),
            snapshot=snap,
        )
        result = module.run(ctx)
    except Exception as exc:  # noqa: BLE001 - this is a debugging tool
        import traceback

        print(f"\nrule raised {type(exc).__name__}: {exc}\n", file=sys.stderr)
        traceback.print_exc()
        return 1

    rows = result.get("rows") or []
    print(f"\nstatus  {result.get('status')}")
    print(f"summary {result.get('summary')}")
    print(f"rows    {len(rows)}")
    if result.get("metrics"):
        print(f"metrics {json.dumps(result['metrics'], indent=2, default=str)}")
    for note in (getattr(ctx, "logs", []) or []):
        print(f"  log: {note}")

    verdict = _compare(recorded, result)
    if verdict:
        print(f"\n{verdict}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.engine.replay",
        description="Re-run a captured rule execution against its own inputs.",
    )
    parser.add_argument(
        "run", help="run id (123) or the folder path (mount/Rules_Executions/000123)"
    )
    parser.add_argument(
        "--save", metavar="PATH",
        help="copy the folder's rule.py to PATH so you can edit the failing version",
    )
    args = parser.parse_args(argv)
    return replay(_resolve_folder(args.run), save_to=args.save)


if __name__ == "__main__":
    raise SystemExit(main())