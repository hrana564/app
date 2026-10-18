"""
Rule schedules - friendly cadence only.

Nothing here accepts or returns a user-typed cron as the primary interface.
`cron` appears in the response for engineers debugging the scheduler, but the
UI works entirely from `kind` + `config` + `cadence_label` + `upcoming_runs`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.engine import scheduler
from app.engine.frequency import (
    ALL_KINDS,
    Cadence,
    CadenceError,
    MAX_DAY_OF_MONTH,
    PRESET_CHOICES,
    VALID_HOURLY_INTERVALS,
    WEEKDAY_NAMES,
    describe,
    next_runs,
    relative_time,
    to_cron,
)
from app.engine.scheduler import cadence_of, cron_for
from app.models import Rule, RuleSchedule
from app.schemas import (
    CadenceOption,
    CadencePreviewRequest,
    CadencePreviewResponse,
    RuleStatus,
    ScheduleCreate,
    ScheduleOut,
    ScheduleUpdate,
)

log = logging.getLogger("abm.api.schedules")

router = APIRouter(prefix="/api/rule-schedules", tags=["rule schedules"])


def _to_out(schedule: RuleSchedule) -> ScheduleOut:
    """Shape a row for the API, adding the human labels the UI needs."""
    cadence = Cadence.from_dict(schedule.config)
    upcoming: list[datetime] = []
    try:
        upcoming = next_runs(cadence, schedule.timezone or "UTC", count=3)
    except CadenceError:
        upcoming = []

    data = ScheduleOut.model_validate(schedule)
    data.cadence_label = describe(cadence)
    data.upcoming_runs = upcoming
    data.next_run_relative = (
        relative_time(upcoming[0]) if upcoming else None
    )
    return data


# ---------------------------------------------------------------------------
# The vocabulary the UI builds its picker from
# ---------------------------------------------------------------------------
@router.get("/cadence-options", response_model=dict)
async def cadence_options(_user: CurrentUser) -> dict:
    """Everything the frequency picker needs, in one call."""
    return {
        "presets": [CadenceOption(**p).model_dump(mode="json") for p in PRESET_CHOICES],
        "kinds": ALL_KINDS,
        "weekday_names": WEEKDAY_NAMES,
        "hourly_intervals": VALID_HOURLY_INTERVALS,
        "max_day_of_month": MAX_DAY_OF_MONTH,
        "rule_statuses": ["draft", "active", "paused", "retired"],
    }


@router.post("/cadence-preview", response_model=CadencePreviewResponse)
async def cadence_preview(
    payload: CadencePreviewRequest, _user: CurrentUser
) -> CadencePreviewResponse:
    """Show the derived cron and the real fire times BEFORE saving.

    This is the guardrail against a schedule that is accepted by the database
    but silently never fires.
    """
    cadence = Cadence.from_dict({"kind": payload.kind, **payload.config})
    try:
        expr = to_cron(cadence)
        upcoming = next_runs(cadence, payload.timezone, count=payload.count)
        return CadencePreviewResponse(
            ok=True, cadence_label=describe(cadence), cron=expr, upcoming=upcoming
        )
    except CadenceError as exc:
        return CadencePreviewResponse(
            ok=False, cadence_label=describe(cadence), problems=[str(exc)]
        )


# ---------------------------------------------------------------------------
# Used by the rules API when a rule is activated or paused
# ---------------------------------------------------------------------------
async def sync_schedules_for_code(session, code: str) -> int:
    """(Re)register or remove every schedule for a rule code.

    `rules.status` is the switch, so changing it must be reflected in
    APScheduler immediately - otherwise a paused rule keeps firing overnight.
    """
    rows = (
        await session.execute(
            select(RuleSchedule).where(RuleSchedule.rule_code == code)
        )
    ).scalars().unique().all()

    registered = 0
    for sched in rows:
        try:
            if await scheduler.sync_schedule(sched) is not None:
                registered += 1
        except CadenceError as exc:
            log.error(
                "schedule %s of rule %s has an invalid cadence and was skipped: %s",
                sched.id, code, exc,
            )
    return registered


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("", response_model=dict)
async def list_schedules(
    session: SessionDep,
    _user: CurrentUser,
    rule_code: str | None = None,
) -> dict:
    stmt = select(RuleSchedule)
    if rule_code is not None:
        stmt = stmt.where(RuleSchedule.rule_code == rule_code)
    stmt = stmt.order_by(RuleSchedule.id)

    rows = (await session.execute(stmt)).scalars().all()
    return {"data": [_to_out(r).model_dump(mode="json") for r in rows]}


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(
    schedule_id: int, session: SessionDep, _user: CurrentUser
) -> ScheduleOut:
    sched = await session.get(RuleSchedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _to_out(sched)


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    payload: ScheduleCreate, session: SessionDep, admin: AdminUser
) -> ScheduleOut:
    cadence = Cadence.from_dict({"kind": payload.kind, **payload.config})
    try:
        expr = to_cron(cadence)
    except CadenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    code = (payload.rule_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="rule_code is required")

    # Validate the code resolves to a live rule, so a schedule is never created
    # against a typo that would silently never fire.
    live = (
        await session.execute(
            select(Rule.id).where(
                Rule.code == code, Rule.is_version_active.is_(True)
            )
        )
    ).scalar_one_or_none()
    if live is None:
        raise HTTPException(
            status_code=404, detail=f"No rule with code {code!r}"
        )

    label = (payload.label or "").strip() or describe(cadence)

    # Duplicate detection on the DERIVED CRON, not on the JSONB config column.
    # Comparing a JSONB column to a Python dict through asyncpg is unreliable,
    # and the stored config embeds `kind`, so a raw-config comparison never
    # matches. The cron is the semantic identity of a schedule.
    existing_rows = (
        await session.execute(
            select(RuleSchedule).where(RuleSchedule.rule_code == code)
        )
    ).scalars().unique().all()
    for other in existing_rows:
        try:
            same = cron_for(other) == expr
        except CadenceError:
            continue
        if same:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This rule already has a '{describe(cadence_of(other))}' "
                    f"schedule (#{other.id})"
                ),
            )

    sched = RuleSchedule(
        rule_code=code,
        label=label,
        kind=cadence.kind,
        config=cadence.to_dict(),
        cron=expr,
        timezone=payload.timezone,
        data_lag_days=payload.data_lag_days,
        created_by=admin.username,
    )
    session.add(sched)
    await session.flush()

    # Register with APScheduler only if the rule is already active; otherwise
    # it is stored and picked up when the rule activates.
    await scheduler.sync_schedule(sched)
    await session.commit()
    await session.refresh(sched)
    return _to_out(sched)


@router.put("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: int, payload: ScheduleUpdate, session: SessionDep, admin: AdminUser
) -> ScheduleOut:
    sched = await session.get(RuleSchedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")

    kind_changed = False
    if payload.kind is not None and payload.kind != sched.kind:
        sched.kind = payload.kind
        kind_changed = True
    if payload.config is not None:
        sched.config = payload.config
        kind_changed = True
    if payload.label is not None:
        sched.label = payload.label
    if payload.timezone is not None:
        sched.timezone = payload.timezone
    if payload.data_lag_days is not None:
        sched.data_lag_days = payload.data_lag_days

    cadence = Cadence.from_dict(sched.config)
    try:
        sched.cron = to_cron(cadence)
    except CadenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if kind_changed and not payload.label:
        sched.label = describe(cadence)

    await scheduler.sync_schedule(sched)
    await session.commit()
    await session.refresh(sched)
    return _to_out(sched)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: int, session: SessionDep, admin: AdminUser
) -> None:
    sched = await session.get(RuleSchedule, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await scheduler.remove_schedule(schedule_id)
    await session.delete(sched)
    await session.commit()


@router.get("/cron/jobs")
async def live_jobs(_user: CurrentUser) -> dict:
    """What APScheduler currently holds. For debugging schedules."""
    return {
        "jobs": scheduler.status(),
        "now": datetime.now(timezone.utc).isoformat(),
    }