from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.deps import SessionDep, require
from app.api.rules import (
    RulesReader,
    RulesWriter,
    data_date_for,
    require_head,
    source_or_409,
)
from app.engine.catalog import database_catalog
from app.engine.datasources import default_entity
from app.engine.llm import is_configured
from app.engine.rule_generator import preview_context
from app.engine.rules import scoped_code
from app.models import Rule, User
from app.permissions import RULES_GENERATE
from app.schemas import (
    AgentStep,
    GenerationAttempt,
    RuleGenerateRequest,
    RuleGenerateResponse,
    RuleReviseRequest,
    TrialOut,
)
from app.settings import settings

router = APIRouter(prefix="/api/rules", tags=["rules"])

RulesGenerator = Annotated[User, Depends(require(RULES_GENERATE))]

@router.get("/generate/status")
async def generate_status(_user: RulesReader) -> dict:
    return {"configured": is_configured(), "model": settings.llm_model or None}


@router.get("/generate/context")
async def generate_context(
    session: SessionDep,
    _user: RulesReader,
    entity: str | None = None,
    data_date: date | None = None,
) -> dict:
    code = (entity or default_entity()).upper()
    catalog = await database_catalog(session)
    return preview_context(catalog, code, data_date or data_date_for(code))


@router.post("/generate", response_model=RuleGenerateResponse)
async def generate(
    payload: RuleGenerateRequest, session: SessionDep, admin: RulesGenerator
) -> RuleGenerateResponse:
    """Ask for a rule, and get back one that has been run.

    Nothing is created here. The agent writes a rule, runs it against the
    bytes the estate actually holds, and repairs it if it does not run - and
    what comes back is a piece of source and the verdict on it. The rule row
    and the file are written when the admin presses Save, so a rule that has
    never been reviewed never reaches the catalogue.
    """
    entity = (payload.entity or default_entity()).upper()
    target = payload.data_date or data_date_for(entity)

    catalog = await database_catalog(session)
    await _check_registered(catalog, entity, payload.datasources)

    result = await run_in_threadpool(
        _run_agent,
        catalog,
        payload.prompt,
        entity,
        target,
        payload.datasources,
        payload.lookback_days,
    )

    return _agent_response(
        result,
        entity=entity,
        data_date=target,
        lookback_days=payload.lookback_days,
        suggested_code=await _next_generated_code(session, entity),
    )


@router.post("/{code}/revise", response_model=RuleGenerateResponse)
async def revise(
    code: str, payload: RuleReviseRequest, session: SessionDep, admin: RulesWriter
) -> RuleGenerateResponse:
    """Rewrite an existing rule from a plain-language change request.

    Nothing is created. The agent is given the rule's current source - the draft
    being worked on if there is one, otherwise the live version - and told to
    change only what the request asks. It proves the result runs, and what comes
    back is a candidate and the verdict on it. It becomes a version when the
    admin saves it, which is what keeps an unreviewed rule out of the catalogue.
    """
    current = await require_head(session, code)
    base = await _revision_base(session, code, current)
    source = source_or_409(base)

    entity = base.entity
    target = payload.data_date or data_date_for(entity)
    lookback = payload.lookback_days or base.lookback_days

    catalog = await database_catalog(session)
    await _check_registered(catalog, entity, payload.datasources)

    result = await run_in_threadpool(
        _run_revision,
        catalog,
        payload.prompt,
        source,
        entity,
        target,
        payload.datasources,
        lookback,
    )
    return _agent_response(
        result, entity=entity, data_date=target, lookback_days=lookback
    )
async def _check_registered(catalog, entity: str, datasources: list[str]) -> None:
    registered = sorted(catalog.names_for(entity))
    unknown = [t for t in datasources if t not in registered]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{', '.join(unknown)} is not registered for {entity}. "
                f"Registered: {', '.join(registered) or '(none)'}"
            ),
        )


def _agent_response(
    result,
    *,
    entity: str,
    data_date: date,
    lookback_days: int,
    suggested_code: str | None = None,
) -> RuleGenerateResponse:
    trial = None
    if result.trial is not None:
        t = result.trial
        trial = TrialOut(
            status=t.status,
            ok=t.ok,
            outcome=t.outcome,
            summary=t.summary,
            rows=t.rows,
            metrics=t.metrics,
            error=t.error,
            duration_ms=t.duration_ms,
            warnings=t.warnings,
        )

    return RuleGenerateResponse(
        ok=result.ok,
        entity=entity,
        data_date=data_date,
        lookback_days=lookback_days,
        source_code=result.source,
        name=result.name,
        description=result.description,
        datasources_used=result.datasources_used,
        # The one shape a description has, straight from the agent. It is
        # stored with the rule when the admin saves it, so the draft is born
        # complete and publishing never has to ask a model.
        explanation=result.explanation,
        trial=trial,
        attempts=[
            GenerationAttempt(
                n=a["n"], ok=a["ok"], error=a.get("error"), traceback=a.get("traceback")
            )
            for a in result.attempts
        ],
        suggested_code=suggested_code,
        error=result.error,
        trace=[
            AgentStep(
                tool=step["tool"],
                args=step.get("args") or {},
                summary=step.get("summary", ""),
            )
            for step in result.trace
        ],
    )


async def _revision_base(session, code: str, current: Rule) -> Rule:
    newest = (
        await session.execute(
            select(Rule)
            .where(Rule.code == code)
            .order_by(Rule.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return newest or current


def _run_revision(catalog, prompt, current_source, entity, data_date, tables, lookback_days):
    from app.engine.rule_agent import run_revision

    return run_revision(
        catalog=catalog,
        request=prompt,
        current_source=current_source,
        entity=entity,
        data_date=data_date,
        tables=tables,
        lookback_days=lookback_days,
        allowed=tables,
    )


def _run_agent(catalog, prompt, entity, data_date, tables, lookback_days):
    from app.engine.rule_agent import run_generation

    return run_generation(
        catalog=catalog,
        prompt=prompt,
        entity=entity,
        data_date=data_date,
        tables=tables,
        lookback_days=lookback_days,
        allowed=tables,
    )


async def _next_generated_code(session, entity: str) -> str:
    rows = (
        await session.execute(
            select(Rule.code).where(
                Rule.entity == entity, Rule.code.like("%-GEN-%")
            )
        )
    ).scalars().all()
    numbers = []
    for code in rows:
        tail = str(code).split("-")[-1]
        if tail.isdigit():
            numbers.append(int(tail))
    return scoped_code(f"GEN-{(max(numbers) + 1) if numbers else 1:02d}", entity)
