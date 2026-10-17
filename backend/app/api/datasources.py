"""Datasource registry - what the rules can read."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.engine.datasources import ENTITIES, SYSTEMS_BY_ENTITY, all_sources
from app.models import Datasource
from app.schemas import DatasourceOut
from app.settings import settings

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


@router.get("/entities")
async def list_entities(_user: CurrentUser) -> dict:
    """The two client entities and the systems each one draws from."""
    return {
        "entities": [
            {
                "code": code,
                "name": name,
                "systems": SYSTEMS_BY_ENTITY.get(code, []),
            }
            for code, name in sorted(ENTITIES.items())
        ]
    }


@router.get("", response_model=list[DatasourceOut])
async def list_datasources(
    session: SessionDep, _user: CurrentUser, entity: str | None = None
) -> list[DatasourceOut]:
    """Mirror the in-code registry into the DB on first read, then serve the DB.

    The Python registry stays the source of truth; the table exists so the UI
    and a future Power Apps connector can enumerate the same list.
    """
    await _sync_registry(session)
    stmt = select(Datasource)
    if entity:
        stmt = stmt.where(Datasource.entity == entity.upper())
    rows = (await session.execute(stmt.order_by(Datasource.entity, Datasource.name))).scalars().all()
    return [DatasourceOut.model_validate(d) for d in rows]


@router.get("/available-dates")
async def available_dates(_user: CurrentUser, entity: str = "LAD") -> dict:
    base = settings.sourcedata_dir / entity.upper()
    if not base.exists():
        return {"dates": [], "root": str(base)}
    dates: list[str] = []
    for folder in sorted(base.iterdir()):
        if not folder.is_dir():
            continue
        try:
            date.fromisoformat(folder.name)
        except ValueError:
            continue
        dates.append(folder.name)
    return {"dates": dates, "root": str(base)}


async def _sync_registry(session) -> None:
    existing = {
        (entity, name)
        for entity, name in (
            await session.execute(select(Datasource.entity, Datasource.name))
        ).all()
    }
    added = False
    for src in all_sources():
        if (src.entity, src.name) in existing:
            continue
        session.add(
            Datasource(
                name=src.name,
                entity=src.entity,
                system=src.system,
                description=src.description,
                path_template=src.path_template,
                grain=src.grain,
                columns=dict(src.columns),
                derived=dict(src.derived),
                is_active=True,
            )
        )
        added = True
    if added:
        await session.commit()