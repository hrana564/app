"""
Prune duplicate runs and tickets from smoke-test runs.

Keeps the newest run per (rule_code, data_date). Deletes the rest.
Tickets cascade via ON DELETE CASCADE on tickets.run_id.
Also removes the corresponding mount/Rules_Executions/<run_id:06d> folders.

Safety:
- --dry-run (default): prints plan, makes no changes
- --apply: performs deletions
- Idempotent - running again is a no-op
- Touches nothing else (rules, schedules, catalogue, source CSVs, users)
"""

from __future__ import annotations

import asyncio
import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, init_models
from app.models import Run, Ticket
from app.settings import settings


async def prune(apply: bool, verbose: bool = True) -> int:
    await init_models()
    
    runs_dir = Path(settings.runs_dir)
    
    async with SessionLocal() as session:
        # Get all runs with metadata
        result = await session.execute(
            select(
                Run.id,
                Run.rule_code,
                Run.data_date,
                Run.started_at,
                Run.status,
                Run.outcome,
                func.jsonb_array_length(Run.rows).label("row_count"),
            ).order_by(Run.started_at.desc(), Run.id.desc())
        )
        runs = result.all()
        
        if not runs:
            if verbose:
                print("No runs found.")
            return 0
        
        # Group by (rule_code, data_date)
        groups = defaultdict(list)
        for run in runs:
            key = (run.rule_code, run.data_date)
            groups[key].append(run)
        
        to_delete = []
        to_keep = []
        
        for key, group_runs in groups.items():
            # Keep newest (first in desc order)
            keep = group_runs[0]
            to_keep.append(keep)
            for r in group_runs[1:]:
                to_delete.append(r)
        
        # Count tickets that will be deleted (cascade)
        if to_delete:
            delete_ids = [r.id for r in to_delete]
            ticket_result = await session.execute(
                select(func.count())
                .select_from(Ticket)
                .where(Ticket.run_id.in_(delete_ids))
            )
            tickets_to_delete = ticket_result.scalar_one()
        else:
            tickets_to_delete = 0
        
        # Count folders
        folders_to_delete = []
        for r in to_delete:
            folder = runs_dir / f"{r.id:06d}"
            if folder.exists():
                folders_to_delete.append(folder)
        
        if verbose:
            print("=" * 72)
            print(f"PRUNE DUPLICATE RUNS {'(DRY RUN)' if not apply else '(APPLYING)'}")
            print("=" * 72)
            print(f"Total runs: {len(runs)}")
            print(f"Groups (rule_code, data_date): {len(groups)}")
            print(f"Runs to keep: {len(to_keep)}")
            print(f"Runs to delete: {len(to_delete)}")
            print(f"Tickets to delete (cascade): {tickets_to_delete}")
            print(f"Run folders to delete: {len(folders_to_delete)}")
            print()
            
            if to_delete:
                print("Runs to delete:")
                for r in to_delete[:20]:
                    print(f"  {r.rule_code} {r.data_date} run_id={r.id} started={r.started_at} outcome={r.outcome} rows={r.row_count or 0}")
                if len(to_delete) > 20:
                    print(f"  ... and {len(to_delete) - 20} more")
                print()
            
            # Calculate exposure
            kept_exposure = sum((r.row_count or 0) for r in to_keep if r.outcome == 'fail')
            print(f"Exposure (exceptions) after prune: {kept_exposure}")
            print()
        
        if not apply:
            if verbose:
                print("DRY RUN - no changes made. Use --apply to delete.")
            return 0
        
        # Apply deletions
        if to_delete:
            delete_ids = [r.id for r in to_delete]
            await session.execute(delete(Run).where(Run.id.in_(delete_ids)))
            await session.commit()
            if verbose:
                print(f"Deleted {len(to_delete)} runs ({tickets_to_delete} tickets cascaded)")
        
        # Delete folders
        deleted_folders = 0
        for folder in folders_to_delete:
            try:
                shutil.rmtree(folder)
                deleted_folders += 1
            except Exception as e:
                if verbose:
                    print(f"Warning: could not delete {folder}: {e}")
        
        if verbose and deleted_folders:
            print(f"Deleted {deleted_folders} run folders")
        
        if verbose:
            print("\nDone.")
        
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune duplicate runs and tickets")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()
    
    try:
        return asyncio.run(prune(apply=args.apply, verbose=not args.json))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
