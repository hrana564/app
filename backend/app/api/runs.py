from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import SessionDep, require
from app.api.scope import scoped
from app.engine.runner import execute_rule
from app.models import Run, Rule, User
from app.permissions import RUNS_READ, RUNS_RERUN
from app.schemas import RunOut

router = APIRouter(prefix="/api/rule-runs", tags=["rule-runs"])

# The capabilities this router gates.
RunReader = Annotated[User, Depends(require(RUNS_READ))]
RunRerunner = Annotated[User, Depends(require(RUNS_RERUN))]


@router.get("", response_model=dict)
async def list_runs(
    session: SessionDep,
    _user: RunReader,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=500),
    status_: str | None = Query(None, alias="status"),
    outcome_: str | None = Query(None, alias="outcome"),
    rule_code: str | None = None,
    entity: str | None = None,
    sort: str = "started_at",
    order: str = "DESC",
) -> dict:
    stmt = select(Run)
    count_stmt = select(func.count()).select_from(Run)

    # A run has no entity column of its own - it belongs to the client whose rule
    # produced it. See app/api/scope.py for why that join is defined once.
    stmt = scoped(stmt, Run, entity)
    count_stmt = scoped(count_stmt, Run, entity)

    # `status` asks whether the rule ran; `outcome` asks what it found. They are
    # separate columns because they are separate questions, and filtering one
    # must never imply the other.
    if status_:
        stmt = stmt.where(Run.status == status_)
        count_stmt = count_stmt.where(Run.status == status_)
    if outcome_:
        stmt = stmt.where(Run.outcome == outcome_)
        count_stmt = count_stmt.where(Run.outcome == outcome_)
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
async def summary(session: SessionDep, _user: RunReader, entity: str | None = None) -> dict:
    rows = (
        await session.execute(
            scoped(
                select(Run.status, Run.outcome, func.count()).group_by(Run.status, Run.outcome),
                Run,
                entity,
            )
        )
    ).all()
    tally: dict[tuple[str, str | None], int] = {
        (status, outcome): count for status, outcome, count in rows
    }
    at = lambda s, o: tally.get((s, o), 0)  # noqa: E731

    since = datetime.now(timezone.utc) - timedelta(days=7)
    recent = (
        await session.execute(
            scoped(
                select(func.count()).select_from(Run).where(Run.started_at >= since),
                Run,
                entity,
            )
        )
    ).scalar_one()

    return {
        "total": sum(tally.values()),
        # Execution
        "running": at("running", None),
        "completed": at("success", "pass") + at("success", "fail"),
        "failed": at("failed", None),
        # Control outcome - only over runs that actually produced one
        "breaches": at("success", "fail"),
        "cleared": at("success", "pass"),
        "last_7_days": recent,
    }


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: int, session: SessionDep, _user: RunReader) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunOut.model_validate(run)


@router.post("/{run_id}/rerun", response_model=RunOut)
async def rerun(run_id: int, session: SessionDep, user: RunRerunner) -> RunOut:
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