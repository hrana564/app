"""Async SQLAlchemy engine and session wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.settings import settings

# Deterministic constraint names. Without this, Alembic autogenerate emits
# drop_constraint() for unnamed FKs and those migrations fail at runtime.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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


async def check_schema() -> tuple[bool, str]:
    """Verify migrations have been applied. Does NOT create tables.

    The schema is owned by Alembic. Auto-creating tables at startup was the
    original shortcut; it let the models and the database drift apart silently
    and left nobody able to say what the schema actually was. Now a missing
    migration is a loud failure with the fix in the message.
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            exists = await conn.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            )
            if not exists.scalar():
                return False, (
                    "schema is not initialised. Run from backend/: "
                    "  python -m alembic upgrade head"
                )
            tables = await conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
            count = int(tables.scalar() or 0)
            if count <= 1:  # only alembic_version
                return False, (
                    "schema looks empty. Run:  python -m alembic upgrade head"
                )
            return True, f"{count} tables present"
    except Exception as exc:  # noqa: BLE001
        return False, f"cannot reach the database: {exc}"