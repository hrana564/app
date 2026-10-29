"""
Start again from nothing.

The reset recipe the README points at. A change to a model means the database
no longer matches the code, and this project has no migrations on purpose:
there is one disposable development database, so the answer is to drop it and
rebuild it rather than to write an ALTER TABLE script that only ever runs
once.

What it does, in order:

    1. drop the database and create it empty
    2. create the tables the models describe
    3. seed the accounts from .env
    4. seed the datasource catalogue
    5. register the demo rules that live in backend/rules/
    6. empty the run folders and the scratch space

What it deliberately does NOT do: keep anything. A run, a ticket, a
description and a version that a test made are all disposable, and a database
that carries them is a database whose answers depend on when it was last
reset. The rule SOURCE files are the exception - they are code, not data, and
they are what step 5 reads.

    python scripts/reset_database.py            # reset, then say what happened
    python scripts/reset_database.py --keep-data  # leave the CSVs alone

STOP THE API FIRST. It holds connections to the database being dropped, and a
drop that has to fight a live connection is a drop that fails.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.settings import settings  # noqa: E402


# The demo rules, and how serious each one is. The FILES are the source of
# truth for which rules exist - this is read from backend/rules/ - so this
# only has to say the two things a file cannot: how serious the control is,
# and whether a person or the agent wrote it.
SEVERITIES: dict[str, tuple[str, str]] = {
    "ET-1": ("critical", "manual"),
    "SQT-1": ("high", "manual"),
    "SQT-2": ("critical", "manual"),
    "SQT-3": ("high", "manual"),
    "SQT-4": ("medium", "llm"),
    "SQT-5": ("medium", "llm"),
    "ET-2": ("medium", "llm"),
    "ET-3": ("medium", "llm"),
}


async def _drop_and_create() -> None:
    """An empty database with the right name.

    Connected to `postgres` rather than to the application database, because a
    database cannot be dropped while something is using it - and the thing
    using it is the database being dropped. `WITH (FORCE)` ends the sessions
    that are holding it, which is what makes this safe to run while the API
    is up.

    asyncpg is used directly rather than through an engine: the pool's
    pre-ping races with the drop, and the connection that issued the drop
    comes back to find its socket gone.
    """
    import asyncpg

    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database="postgres",
    )
    try:
        await conn.execute(
            f'DROP DATABASE IF EXISTS "{settings.postgres_db}" WITH (FORCE)'
        )
        await conn.execute(f'CREATE DATABASE "{settings.postgres_db}"')
    finally:
        await conn.close()
    print(f"  database '{settings.postgres_db}': dropped and created")


async def _create_tables() -> None:
    from app.db import engine, init_models

    # The pool was opened against the database that has just been dropped, so
    # its connections are for a database that no longer exists. Throwing them
    # away is what makes the next one open against the new one.
    await engine.dispose()
    await init_models()
    print("  tables: created from the models")


async def _seed_accounts() -> None:
    from app.main import _bootstrap

    await _bootstrap()
    print("  accounts: seeded from .env")


async def _seed_catalogue() -> None:
    from make_sample_data import seed_catalog_async

    await seed_catalog_async(verbose=False)
    print("  datasources: seeded from the demo estate")


async def _register_rules() -> int:
    """Register every demo rule file as a live version row.

    The files are the source of truth for WHICH rules exist. Each one becomes
    version 1 of its code, active, with the name and description it declares
    for itself - because those constants are the rule's own account of what
    it does, and they are already in the file.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.engine.loader import read_source
    from app.engine.rules import scoped_code, validate_code
    from app.engine.validation import declared_tables
    from app.models import Rule

    rules_dir = settings.rules_dir
    added = 0

    async with SessionLocal() as session:
        for path in sorted(rules_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            code = validate_code(path.name.split("_v")[0])
            entity = code.split("-", 1)[0].upper()
            code = scoped_code(code, entity)
            if (
                await session.execute(
                    select(Rule).where(Rule.code == code, Rule.version == 1)
                )
            ).scalar_one_or_none():
                continue

            source = read_source(path.name, rules_dir)
            name, description = _declared(source)
            severity, origin = SEVERITIES.get(code, ("medium", "manual"))
            session.add(
                Rule(
                    code=code,
                    version=1,
                    is_version_active=True,
                    status="active",
                    name=name or code,
                    entity=entity,
                    description=description,
                    severity=severity,
                    lookback_days=1,
                    validation_status="valid",
                    validation_messages=[],
                    validated_by=None,
                    declared_datasources=declared_tables(source),
                    source_file=path.name,
                    change_note="initial version",
                    created_by=settings.admin_username,
                    origin=origin,
                )
            )
            added += 1
        await session.commit()
    print(f"  rules: {added} registered from {rules_dir}")
    return added


def _declared(source: str) -> tuple[str, str]:
    """The NAME and DESCRIPTION a rule declares for itself."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "", ""
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = node.value.value
    return found.get("NAME", ""), found.get("DESCRIPTION", "")


def _empty_folders() -> None:
    """The run folders and the scratch space.

    A run folder is a copy of the bytes one run read, kept so a run can be
    replayed. With the runs gone they are copies of nothing, and they are the
    biggest thing in the tree by far.

    The folders themselves and their `.gitkeep` stay: the shape of the tree is
    part of the repository, so a reset that deleted it would show up in every
    diff as a file that vanished.
    """
    for folder in (settings.runs_dir, settings.trials_dir):
        folder.mkdir(parents=True, exist_ok=True)
        for child in folder.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    keep = settings.runs_dir / ".gitkeep"
    if not keep.exists():
        keep.touch()
    print(f"  {settings.runs_dir}: emptied")
    print(f"  {settings.trials_dir}: emptied")


async def _reset() -> None:
    """The whole reset, in one event loop.

    One loop, because the engine's pool belongs to the loop that opened it -
    and a pool carried from one loop to another is a pool that behaves in
    ways nobody can predict.
    """
    await _drop_and_create()
    await _create_tables()
    await _seed_accounts()
    await _seed_catalogue()
    await _register_rules()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="leave the generated CSVs alone (they are not touched anyway)",
    )
    parser.parse_args()

    print("Resetting the database to the shape the models describe.\n")
    asyncio.run(_reset())
    _empty_folders()
    print("\nDone. Start the API and it will use the new database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
