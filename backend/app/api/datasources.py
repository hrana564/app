"""
Datasources - the boundary between "the ETL landed files" and "rules may read
these tables".

    GET  /entities            the two client entities
    GET  /                    registered tables, flat
    GET  /overview            registered + what landed today, in ONE call
    GET  /available-dates     days that have a folder
    POST /                    register a table the ETL delivered
    POST /{id}/retire         stop offering it to new rules
    POST /{id}/restore        undo a retirement

The overview endpoint exists so the screen never fans out into one request per
table. Everything it returns is read in the same pass.

RETIRE, NOT DELETE
------------------
A retired table keeps its row, its columns and its history; it simply stops
being offered. Three reasons, in order of weight:

  - a rule already reading it must keep running until someone deals with it,
    and a hard delete would leave that rule referencing a name that resolves to
    nothing
  - "we used to consume this" is the answer the ETL team needs when deciding
    what to switch off
  - a retirement is a mistake someone will make, and it must be undoable
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.engine.datasources import ENTITIES, SYSTEMS_BY_ENTITY
from app.engine.discovery import available_dates, discover, latest_date
from app.models import Datasource, Rule
from app.schemas import (
    DatasourceDiscovered,
    DatasourceOut,
    DatasourceOverview,
    DatasourceRegister,
    DatasourceRetire,
)
from app.settings import settings

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
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
    stmt = select(Datasource)
    if entity:
        stmt = stmt.where(Datasource.entity == entity.upper())
    rows = (await session.execute(stmt.order_by(Datasource.entity, Datasource.name))).scalars().all()
    return [DatasourceOut.model_validate(d) for d in rows]


@router.get("/available-dates")
async def available_dates_endpoint(entity: str = "LAD") -> dict:
    base = settings.sourcedata_dir / entity.upper()
    return {
        "dates": available_dates(entity),
        "root": str(base),
        "latest": latest_date(entity),
    }


# ---------------------------------------------------------------------------
# The one call the screen makes
# ---------------------------------------------------------------------------
async def _rules_using(session: SessionDep, entity: str) -> dict[str, list[str]]:
    """Active rules that read each table, keyed by table name.

    Read from `rules_master.declared_datasources`, which the validator already
    writes as the union of DATASOURCES and every ctx.table() in the source. So
    this is the recorded truth of what rules read, not a re-scan of the files.
    """
    rows = (
        await session.execute(
            select(Rule.code, Rule.declared_datasources).where(
                Rule.entity == entity.upper(),
                Rule.is_version_active.is_(True),
            )
        )
    ).all()

    usage: dict[str, list[str]] = {}
    for code, declared in rows:
        for name in declared or []:
            usage.setdefault(name, []).append(code)
    return {name: sorted(set(codes)) for name, codes in usage.items()}


@router.get("/overview", response_model=DatasourceOverview)
async def overview(
    session: SessionDep,
    _user: CurrentUser,
    entity: str = "LAD",
    data_date: str | None = None,
) -> DatasourceOverview:
    """Registered tables and what the ETL delivered, for one entity and day.

    `data_date` defaults to the newest day that has a folder, so the common case
    - "what landed today" - needs no arguments.
    """
    code = entity.upper()
    if code not in ENTITIES:
        raise HTTPException(404, f"Unknown entity '{entity}'")

    day = data_date or latest_date(code) or ""
    registered = (
        await session.execute(
            select(Datasource).where(Datasource.entity == code).order_by(Datasource.name)
        )
    ).scalars().all()

    usage = await _rules_using(session, code)
    landed = {f.name: f for f in discover(code, day)} if day else {}

    registered_out = []
    for row in registered:
        found = landed.get(row.name)
        registered_out.append(
            DatasourceOut(
                id=row.id,
                name=row.name,
                entity=row.entity,
                system=row.system,
                description=row.description,
                path_template=row.path_template,
                grain=row.grain,
                columns=dict(row.columns or {}),
                is_active=row.is_active,
                used_by=usage.get(row.name, []),
                landing="landed" if found else "absent",
                row_count=found.row_count if found else None,
                last_seen=found.data_date if found else None,
            )
        )

    discovered_out = []
    for name, found in landed.items():
        match = next((r for r in registered if r.name == name), None)
        if match is None:
            state = "new"
        elif match.is_active:
            state = "registered"
        else:
            state = "retired"
        discovered_out.append(
            DatasourceDiscovered(
                name=name,
                entity=code,
                data_date=found.data_date,
                columns=found.columns,
                column_count=found.column_count,
                row_count=found.row_count,
                size_bytes=found.size_bytes,
                modified_at=found.modified_at,
                state=state,
            )
        )

    return DatasourceOverview(
        entity=code,
        entity_name=ENTITIES[code],
        systems=SYSTEMS_BY_ENTITY.get(code, []),
        data_date=day,
        dates=available_dates(code),
        registered=registered_out,
        discovered=discovered_out,
    )


@router.get("/discovered")
async def discovered(entity: str = "LAD", data_date: str | None = None) -> list[Any]:
    """Only what is on disk, for the register picker."""
    code = entity.upper()
    day = data_date or latest_date(code)
    if not day:
        return []
    return discover(code, day)


# ---------------------------------------------------------------------------
# Writing - the admin's actual job
# ---------------------------------------------------------------------------
@router.post("", response_model=DatasourceOut, status_code=201)
async def register_datasource(
    payload: DatasourceRegister, session: SessionDep, admin: AdminUser
) -> DatasourceOut:
    """Register a table the ETL delivered.

    Columns come from the file's header when the admin does not override them,
    because the header is the truth about what the ETL actually sends and a
    hand-typed column list is a second place for it to be wrong.
    """
    code = payload.entity.upper()
    if code not in ENTITIES:
        raise HTTPException(404, f"Unknown entity '{payload.entity}'")

    name = payload.name.strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, f"'{name}' is not a usable table name")

    existing = (
        await session.execute(
            select(Datasource).where(
                Datasource.entity == code, Datasource.name == name
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Re-registering a retired table is a restore, not a duplicate.
        if existing.is_active:
            raise HTTPException(409, f"'{name}' is already registered for {code}")
        existing.is_active = True
        existing.columns = dict(payload.columns or existing.columns or {})
        await session.commit()
        await session.refresh(existing)
        return DatasourceOut.model_validate(existing)

    columns = dict(payload.columns or {})
    if not columns:
        found = discover(code, payload.data_date or latest_date(code) or "")
        match = next((f for f in found if f.name == name), None)
        if match is not None:
            columns = dict(match.columns)

    row = Datasource(
        name=name,
        entity=code,
        system=payload.system or "",
        description=payload.description or "",
        path_template=f"mount/SourceData/{code}/{{date}}/{name}.csv",
        grain=payload.grain or "",
        columns=columns,
        is_active=True,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DatasourceOut.model_validate(row)


@router.post("/{datasource_id}/retire", response_model=DatasourceOut)
async def retire_datasource(
    datasource_id: int, payload: DatasourceRetire, session: SessionDep, admin: AdminUser
) -> DatasourceOut:
    """Stop offering a table. Reversible, and refuses to hide a live dependency.

    A table an active rule still reads is refused unless the admin confirms,
    because retiring it will not stop that rule - the rule keeps running and
    will start failing when the ETL stops sending the file. The 409 exists so
    that consequence is a decision, not a surprise.
    """
    row = await session.get(Datasource, datasource_id)
    if row is None:
        raise HTTPException(404, "Datasource not found")
    if not row.is_active:
        return DatasourceOut.model_validate(row)

    usage = await _rules_using(session, row.entity)
    dependents = usage.get(row.name, [])
    if dependents and not payload.confirm_dependents:
        raise HTTPException(
            409,
            detail={
                "message": f"{len(dependents)} active rule(s) still read '{row.name}'",
                "dependents": dependents,
                "hint": "Retiring does not stop those rules. They will fail once the ETL stops sending this file.",
            },
        )

    row.is_active = False
    await session.commit()
    await session.refresh(row)
    return DatasourceOut.model_validate(row)


@router.post("/{datasource_id}/restore", response_model=DatasourceOut)
async def restore_datasource(
    datasource_id: int, session: SessionDep, admin: AdminUser
) -> DatasourceOut:
    """Undo a retirement. Nothing about the table changed, so nothing to rebuild."""
    row = await session.get(Datasource, datasource_id)
    if row is None:
        raise HTTPException(404, "Datasource not found")
    row.is_active = True
    await session.commit()
    await session.refresh(row)
    return DatasourceOut.model_validate(row)


@router.delete("/{datasource_id}", status_code=204)
async def delete_datasource(
    datasource_id: int, session: SessionDep, admin: AdminUser
) -> None:
    """Remove a registration for good. Only ever for a table no rule reads.

    Retirement is the reversible answer and the right one for anything in use.
    This is for the other case: a table that was registered by mistake, or one the
    ETL has stopped sending, which would otherwise sit in the list forever as a
    permanent red "absent".

    A hard delete is refused while any active rule declares the table, because
    the rule's own history records it as something it reads - and that record
    would outlive the row. Retirement preserves the story; deletion must not
    break it.
    """
    row = await session.get(Datasource, datasource_id)
    if row is None:
        raise HTTPException(404, "Datasource not found")

    dependents = (await _rules_using(session, row.entity)).get(row.name, [])
    if dependents:
        raise HTTPException(
            409,
            detail={
                "message": f"{row.name} is read by {len(dependents)} active rule(s)",
                "dependents": dependents,
                "hint": "Retire it instead - that stops new rules using it and keeps the history.",
            },
        )

    await session.delete(row)
    await session.commit()