"""
Datasources - the boundary between "the ETL landed files" and "rules may read
these tables".

    GET    /entities          the two client entities
    GET    /                  registered tables, flat
    GET    /overview          registered + what landed today, in ONE call
    GET    /discovered        just what is on disk, read from the CSV headers
    GET    /preview           the first rows of one landed file
    GET    /available-dates   days that have a folder
    POST   /                  register a table the ETL delivered
    DELETE /{id}              remove a registration

The overview endpoint exists so the screen never fans out into one request per
table. Everything it returns is read in the same pass.

REMOVE, AND NOTHING ELSE
------------------------
A table is either registered or it is not. There is no third state.

An earlier version had `retire` - reversible, and refused while live rules read
the table. It was removed because it did not mean what its dialog claimed. The
dialog said the rules "keep running" and would fail only once the ETL stopped
sending the file. They do not: `ctx.table()` consults the catalogue, so a
retired table is refused on the very next run, while the file is still sitting
on disk. Retire and delete broke the same rules in the same way, so retire only
added a second state and a reassuring message that was not true.

Removal is refused outright while an active rule declares the table. That guard
is the whole of the safety, and it is honest: nothing is silently half-removed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.engine.datasources import ENTITIES, SYSTEMS_BY_ENTITY
from app.engine.discovery import (
    available_dates,
    discover,
    entity_root,
    latest_date,
    preview_rows,
)
from app.models import Datasource, Rule
from app.schemas import (
    DatasourceDiscovered,
    DatasourceOut,
    DatasourceOverview,
    DatasourcePreview,
    DatasourceRegister,
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


@router.get("/preview", response_model=DatasourcePreview)
async def preview(
    _user: CurrentUser,
    entity: str = "LAD",
    name: str = "",
    data_date: str | None = None,
    limit: int = Query(10, ge=1, le=200),
) -> DatasourcePreview:
    """The first rows of one landed file, for a human to look at.

    Reading, not querying. Nothing here touches the catalogue: an admin deciding
    whether to trust a table needs to see what the ETL sent, and that is true
    whether or not anyone has registered it yet.

    The name arrives from the client and is joined onto a filesystem path, so it
    is held to the same rule as registration - a table name is an identifier,
    never a path fragment.
    """
    code = entity.upper()
    if code not in ENTITIES:
        raise HTTPException(404, f"Unknown entity '{entity}'")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, f"'{name}' is not a usable table name")

    day = data_date or latest_date(code) or ""
    path = entity_root(code) / day / f"{name}.csv"
    if not path.is_file():
        raise HTTPException(
            404, f"{name}.csv did not land for {code} on {day or '(no day)'}"
        )

    return DatasourcePreview(
        entity=code,
        name=name,
        data_date=day,
        total_rows=_row_count(path),
        rows=preview_rows(path, limit),
        limit=limit,
    )


def _row_count(path: Path) -> int | None:
    """Total rows in the file, or None if the file will not parse."""
    try:
        return int(pl.scan_csv(path).select(pl.len().alias("n")).collect()["n"][0])
    except Exception:  # noqa: BLE001 - the preview is best-effort
        return None


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
                used_by=usage.get(row.name, []),
                landing="landed" if found else "absent",
                row_count=found.row_count if found else None,
                last_seen=found.data_date if found else None,
            )
        )

    discovered_out = []
    for name, found in landed.items():
        match = next((r for r in registered if r.name == name), None)
        state = "registered" if match is not None else "new"
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
        raise HTTPException(409, f"'{name}' is already registered for {code}")

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
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DatasourceOut.model_validate(row)


@router.delete("/{datasource_id}", status_code=204)
async def delete_datasource(
    datasource_id: int, session: SessionDep, admin: AdminUser
) -> None:
    """Remove a registration. Only ever for a table no rule reads.

    Refused outright while an active rule declares the table, and the 409 names
    the rules. That refusal is the whole safety story: a rule's own history
    records it as something it reads, and `ctx.table()` reads the catalogue, so
    removing the row would break that rule on its very next run - immediately,
    not eventually, and with the file still sitting on disk.
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
                "hint": "Change or remove those rules first — a rule that reads a table which is no longer registered fails on its next run.",
            },
        )

    await session.delete(row)
    await session.commit()