"""Schedules: one cron per rule."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.engine import scheduler
from app.models import Schedule
from app.schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("", response_model=dict)
async def list_schedules(
    session: SessionDep,
    _user: CurrentUser,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=500),
    rule_id: int | None = None,
    enabled: bool | None = None,
) -> dict:
    stmt = select(Schedule)
    if rule_id is not None:
        stmt = stmt.where(Schedule.rule_id == rule_id)
    if enabled is not None:
        stmt = stmt.where(Schedule.enabled.is_(enabled))
    stmt = stmt.order_by(Schedule.id).offset((page - 1) * perPage).limit(perPage)

    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [ScheduleOut.model_validate(r).model_dump(mode="json") for r in rows]}


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(
    schedule_id: int, session: SessionDep, _user: CurrentUser
) -> ScheduleOut:
    sched = await session.get(Schedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return ScheduleOut.model_validate(sched)


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    payload: ScheduleCreate, session: SessionDep, admin: AdminUser
) -> ScheduleOut:
    try:
        scheduler.validate_cron(payload.cron)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid cron '{payload.cron}': {exc}") from exc

    dup = (
        await session.execute(
            select(Schedule).where(
                Schedule.rule_id == payload.rule_id, Schedule.cron == payload.cron
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(
            status_code=409, detail="This rule already has a schedule with that cron"
        )

    sched = Schedule(
        rule_id=payload.rule_id,
        cron=payload.cron,
        timezone=payload.timezone,
        enabled=payload.enabled,
        created_by=admin.username,
    )
    session.add(sched)
    await session.flush()

    await scheduler.sync_schedule(sched)
    await session.commit()
    await session.refresh(sched)
    return ScheduleOut.model_validate(sched)


@router.put("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: int, payload: ScheduleUpdate, session: SessionDep, admin: AdminUser
) -> ScheduleOut:
    sched = await session.get(Schedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if payload.cron is not None:
        try:
            scheduler.validate_cron(payload.cron)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid cron '{payload.cron}': {exc}"
            ) from exc
        sched.cron = payload.cron
    if payload.timezone is not None:
        sched.timezone = payload.timezone
    if payload.enabled is not None:
        sched.enabled = payload.enabled

    await scheduler.sync_schedule(sched)
    await session.commit()
    await session.refresh(sched)
    return ScheduleOut.model_validate(sched)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: int, session: SessionDep, admin: AdminUser
) -> None:
    sched = await session.get(Schedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await scheduler.remove_schedule(schedule_id)
    await session.delete(sched)
    await session.commit()


@router.get("/cron/jobs")
async def live_jobs(_user: CurrentUser) -> dict:
    """What APScheduler currently has loaded - useful for debugging schedules."""
    return {"jobs": scheduler.status(), "now": datetime.now(timezone.utc).isoformat()}