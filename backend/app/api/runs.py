"""Runs: the execution ledger."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.engine.runner import execute_rule
from app.models import Run, Rule
from app.schemas import RunOut

router = APIRouter(prefix="/api/rule-runs", tags=["rule-runs"])


@router.get("", response_model=dict)
async def list_runs(
    session: SessionDep,
    _user: CurrentUser,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=500),
    status_: str | None = Query(None, alias="status"),
    rule_code: str | None = None,
    sort: str = "started_at",
    order: str = "DESC",
) -> dict:
    stmt = select(Run)
    count_stmt = select(func.count()).select_from(Run)

    if status_:
        stmt = stmt.where(Run.status == status_)
        count_stmt = count_stmt.where(Run.status == status_)
    if rule_code is not None:
        stmt = stmt.where(Run.rule_code == rule_code)
        count_stmt = count_stmt.where(Run.rule_code == rule_code)

    col = getattr(Run, sort, Run.started_at)
    stmt = stmt.order_by(col.desc() if order.upper() == "DESC" else col.asc())
    stmt = stmt.offset((page - 1) * perPage).limit(perPage)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    return {
        "data": [RunOut.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
    }


@router.get("/summary")
async def summary(session: SessionDep, _user: CurrentUser) -> dict:
    rows = (await session.execute(select(Run.status, func.count()).group_by(Run.status))).all()
    by_status = {status: count for status, count in rows}

    since = datetime.now(timezone.utc) - timedelta(days=7)
    recent = (
        await session.execute(
            select(func.count()).select_from(Run).where(Run.started_at >= since)
        )
    ).scalar_one()

    return {
        "total": sum(by_status.values()),
        "pass": by_status.get("pass", 0),
        "fail": by_status.get("fail", 0),
        "error": by_status.get("error", 0),
        "last_7_days": recent,
    }


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: int, session: SessionDep, _user: CurrentUser) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunOut.model_validate(run)


@router.post("/{run_id}/rerun", response_model=RunOut)
async def rerun(run_id: int, session: SessionDep, user: CurrentUser) -> RunOut:
    """Re-run the same rule version against the same data date."""
    previous = await session.get(Run, run_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Use the EXACT version that produced the original run, so a re-run is
    # comparable. Not the current head.
    rule = await session.get(Rule, previous.rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule version no longer exists")

    run = await execute_rule(
        session,
        rule=rule,
        data_date=previous.data_date or date.today(),
        triggered_by=f"rerun:{user.username}",
    )
    return RunOut.model_validate(run)