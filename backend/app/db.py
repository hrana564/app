from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.settings import settings

log = logging.getLogger("abm.db")

# Deterministic constraint names, so DDL is stable and diffable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


async def _stale_columns(conn) -> dict[str, list[str]]:
    from app import models  # noqa: F401  (registers the mappers)

    declared = {
        table.name: {c.name for c in table.columns} for table in Base.metadata.sorted_tables
    }
    rows = await conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
    )
    live: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        live.setdefault(table_name, set()).add(column_name)

    stale: dict[str, list[str]] = {}
    for table, columns in live.items():
        if table not in declared:
            continue  # a table the app does not own - not ours to judge
        extra = sorted(columns - declared[table])
        if extra:
            stale[table] = extra
    return stale


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    from app import models  # noqa: F401  (registers the mappers)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # `create_all` never ALTERs, so a database created before the ticket
        # number was widened keeps VARCHAR(32) forever. This one is widened in
        # place rather than left to a reset: a rule code may be 64 characters
        # (see app/engine/rules.py) and a ticket number is `<code>-TKT-<5
        # digits>`, so the old width broke the first finding a long-coded rule
        # raised. Widening a varchar needs no table rewrite, and running it
        # every boot is a no-op - cheaper than dropping a database with data.
        await conn.execute(
            text("ALTER TABLE tickets ALTER COLUMN ticket_no TYPE varchar(80)")
        )
        # create_all never ALTERs an existing table, so a database created before
        # a column was removed keeps the old shape forever. `datasources.is_active`
        # is exactly that case: the model no longer declares it, and a stale
        # database would still carry it. A leftover column is harmless - nothing
        # reads it - so this is reported, not fixed. A missing column is the
        # dangerous direction, and that is what makes the reset necessary.
        stale = await _stale_columns(conn)
        for table, columns in stale.items():
            log.warning(
                "table %s still carries columns the model no longer declares: %s. "
                "Drop and recreate the database (see AGENTS.md).",
                table,
                ", ".join(columns),
            )


async def table_names() -> list[str]:
    from sqlalchemy import text

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        )
        return [r[0] for r in rows]