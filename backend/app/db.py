"""Async SQLAlchemy engine and session wiring."""

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
    """Columns present in the database that the models no longer declare.

    Read-only diagnosis. A leftover column cannot break the app - nothing selects
    it - but it is the visible symptom of a database that predates a model
    change, which is the condition AGENTS.md warns about.
    """
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
    """FastAPI dependency."""
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    """Create any missing tables. Safe to call on every start."""
    from app import models  # noqa: F401  (registers the mappers)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    """Tables currently present, for /health and diagnostics."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        )
        return [r[0] for r in rows]