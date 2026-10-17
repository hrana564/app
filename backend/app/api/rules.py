"""Rules: CRUD, version history, LLM generation, dry-run test."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.engine.loader import read_source, rule_path_for, write_source
from app.engine.rule_generator import (
    generate_rule_source,
    is_configured,
    preview_context,
)
from app.engine.runner import execute_rule
from app.engine.validation import RuleValidator, now
from app.models import Rule, RuleVersion
from app.schemas import (
    RuleCreate,
    RuleGenerateRequest,
    RuleGenerateResponse,
    RuleOut,
    RuleTestResponse,
    RuleUpdate,
    RuleVersionOut,
    RunRequest,
    ValidationReport,
)
from app.settings import settings

router = APIRouter(prefix="/api/rules", tags=["rules"])


def _bump_version(
    session, rule: Rule, source: str, note: str, author: str
) -> RuleVersion:
    rule.current_version += 1
    rule.source_file = rule_path_for(rule.id)
    write_source(rule.source_file, source, settings.rules_dir)
    version = RuleVersion(
        rule_id=rule.id,
        version=rule.current_version,
        source_code=source,
        change_note=note,
        created_by=author,
    )
    session.add(version)
    return version


# ---------------------------------------------------------------------------
# generation from a prompt
# Declared before /{rule_id} so the literal path wins over the int-typed param.
# ---------------------------------------------------------------------------
@router.get("/generate/status")
async def generate_status(_user: CurrentUser) -> dict:
    return {"configured": is_configured(), "model": settings.llm_model or None}


@router.get("/generate/context")
async def generate_context(
    _user: CurrentUser, entity: str = "LAD", data_date: date | None = None
) -> dict:
    return preview_context(entity.upper(), data_date or _latest_data_date(entity))


@router.post("/generate", response_model=RuleGenerateResponse)
async def generate(
    payload: RuleGenerateRequest, session: SessionDep, admin: AdminUser
) -> RuleGenerateResponse:
    """
    Prompt -> draft rule. Always lands as status='draft'.
    It is never auto-approved: the UI shows the diff and a test run first.
    """
    entity = payload.entity.upper()
    target = payload.data_date or _latest_data_date(entity)
    generated = generate_rule_source(payload.prompt, entity, target)

    seq = (
        await session.execute(select(func.count()).select_from(Rule))
    ).scalar_one()
    code = f"AUTO-{entity}-{target:%Y%m%d}-{seq + 1:03d}"

    rule = Rule(
        code=code,
        name=generated["explanation"][:255],
        entity=entity,
        description=payload.prompt.strip()[:2000],
        severity="medium",
        query_mode="SNAPSHOT",
        window_days=1,
        status="draft",
        source_file="",
        current_version=0,
        datasource_hint=", ".join(generated["datasources_used"]),
        created_by=admin.username,
        origin="llm",
    )
    session.add(rule)
    await session.flush()
    _bump_version(session, rule, generated["source"], f"generated: {payload.prompt[:200]}", admin.username)

    await session.commit()
    await session.refresh(rule)
    return RuleGenerateResponse(
        rule=RuleOut.model_validate(rule),
        source_code=generated["source"],
        explanation=generated["explanation"],
        datasources_used=generated["datasources_used"],
    )


# ---------------------------------------------------------------------------
# list / read
# ---------------------------------------------------------------------------
@router.get("", response_model=dict)
async def list_rules(
    session: SessionDep,
    _user: CurrentUser,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=500),
    sort: str = "id",
    order: str = "ASC",
    status_: str | None = Query(None, alias="status"),
    entity: str | None = None,
    q: str | None = None,
) -> dict:
    stmt = select(Rule)
    count_stmt = select(func.count()).select_from(Rule)

    if status_:
        stmt = stmt.where(Rule.status == status_)
        count_stmt = count_stmt.where(Rule.status == status_)
    if entity:
        stmt = stmt.where(Rule.entity == entity)
        count_stmt = count_stmt.where(Rule.entity == entity)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Rule.name.ilike(like) | Rule.code.ilike(like))
        count_stmt = count_stmt.where(Rule.name.ilike(like) | Rule.code.ilike(like))

    column = getattr(Rule, sort, Rule.id)
    stmt = stmt.order_by(column.desc() if order.upper() == "DESC" else column.asc())
    stmt = stmt.offset((page - 1) * perPage).limit(perPage)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    return {
        "data": [RuleOut.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
    }


@router.get("/{rule_id}", response_model=RuleOut)
async def get_rule(rule_id: int, session: SessionDep, _user: CurrentUser) -> RuleOut:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return RuleOut.model_validate(rule)


@router.get("/{rule_id}/source")
async def get_source(rule_id: int, session: SessionDep, _user: CurrentUser) -> dict:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    try:
        code = read_source(rule.source_file, settings.rules_dir)
    except FileNotFoundError:
        code = ""
    return {"rule_id": rule_id, "source_file": rule.source_file, "source_code": code}


@router.get("/{rule_id}/versions", response_model=list[RuleVersionOut])
async def list_versions(
    rule_id: int, session: SessionDep, _user: CurrentUser
) -> list[RuleVersionOut]:
    stmt = (
        select(RuleVersion)
        .where(RuleVersion.rule_id == rule_id)
        .order_by(RuleVersion.version.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [RuleVersionOut.model_validate(v) for v in rows]


# ---------------------------------------------------------------------------
# validation gate
# ---------------------------------------------------------------------------
@router.post("/{rule_id}/validate", response_model=ValidationReport)
async def validate_rule(
    rule_id: int, session: SessionDep, _user: CurrentUser
) -> ValidationReport:
    """
    Static check: compiles the module and inspects what it declares, without
    touching data. Records the verdict on the rule so the UI can show it and
    activation can require it.
    """
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    report = await _validate_and_store(session, rule, username=None)
    await session.commit()
    await session.refresh(rule)
    return report


async def _validate_and_store(
    session, rule: Rule, *, username: str | None
) -> ValidationReport:
    validator = RuleValidator(rule.source_file, rule.entity, rules_dir=settings.rules_dir)
    result = await validator.validate(session)

    # Adopt the file's declared name/description if the DB has none useful.
    meta = result.metadata or {}
    if meta.get("name") and (not rule.name or rule.name == rule.code):
        rule.name = str(meta["name"])[:255]
    if meta.get("description") and not rule.description:
        rule.description = str(meta["description"])[:4000]

    rule.validation_status = "valid" if result.ok else "invalid"
    rule.validation_messages = (
        [{"level": "error", "message": m} for m in result.errors]
        + [{"level": "warning", "message": m} for m in result.warnings]
    )
    rule.validated_at = now()
    rule.validated_by = username
    rule.declared_params = result.declared_params
    rule.declared_datasources = result.declared_datasources
    rule.declared_alert = result.declared_alert

    return ValidationReport(
        status=rule.validation_status,
        errors=result.errors,
        warnings=result.warnings,
        declared_params=result.declared_params,
        declared_datasources=result.declared_datasources,
        declared_alert=result.declared_alert,
        name=meta.get("name"),
        description=meta.get("description"),
        can_activate=result.ok,
    )


@router.get("/{rule_id}/validate-report", response_model=ValidationReport)
async def validation_report(
    rule_id: int, session: SessionDep, _user: CurrentUser
) -> ValidationReport:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return ValidationReport(
        status=rule.validation_status,
        errors=[
            m["message"] for m in (rule.validation_messages or [])
            if m.get("level") == "error"
        ],
        warnings=[
            m["message"] for m in (rule.validation_messages or [])
            if m.get("level") == "warning"
        ],
        declared_params=rule.declared_params or [],
        declared_datasources=rule.declared_datasources or [],
        declared_alert=rule.declared_alert or {},
        can_activate=rule.validation_status == "valid",
    )


# ---------------------------------------------------------------------------
# create / update / delete  (admin only)
# ---------------------------------------------------------------------------
@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(
    payload: RuleCreate, session: SessionDep, admin: AdminUser
) -> RuleOut:
    existing = (
        await session.execute(select(Rule).where(Rule.code == payload.code))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Rule code '{payload.code}' already exists")

    rule = Rule(
        code=payload.code,
        name=payload.name,
        entity=payload.entity,
        description=payload.description,
        severity=payload.severity,
        query_mode=payload.query_mode,
        window_days=payload.window_days,
        status=payload.status,
        source_file="",  # assigned once we know the id
        current_version=0,
        datasource_hint=payload.datasource_hint,
        created_by=admin.username,
        origin="manual",
    )
    session.add(rule)
    await session.flush()

    _bump_version(session, rule, payload.source_code, "initial version", admin.username)
    await session.commit()
    await session.refresh(rule)
    return RuleOut.model_validate(rule)


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: int, payload: RuleUpdate, session: SessionDep, admin: AdminUser
) -> RuleOut:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    if payload.name is not None:
        rule.name = payload.name
    if payload.description is not None:
        rule.description = payload.description
    if payload.severity is not None:
        rule.severity = payload.severity
    if payload.query_mode is not None:
        rule.query_mode = payload.query_mode
    if payload.window_days is not None:
        rule.window_days = payload.window_days
    if payload.ticket_mode is not None:
        rule.ticket_mode = payload.ticket_mode
    if payload.group_by is not None:
        rule.group_by = payload.group_by or None
    if payload.dedupe_mode is not None:
        rule.dedupe_mode = payload.dedupe_mode
    if payload.dedupe_window_hours is not None:
        rule.dedupe_window_hours = payload.dedupe_window_hours
    if payload.alerting_enabled is not None:
        rule.alerting_enabled = payload.alerting_enabled

    if payload.source_code is not None:
        _bump_version(
            session, rule, payload.source_code, payload.change_note, admin.username
        )
        # New code invalidates the old verdict - the gate must be re-run.
        rule.validation_status = "unvalidated"
        rule.validated_at = None
        rule.validated_by = None
        rule.validation_messages = []

    if payload.status is not None and payload.status != rule.status:
        if payload.status == "active" and rule.validation_status != "valid":
            raise HTTPException(
                status_code=409,
                detail=(
                    "This rule has not passed validation, so it cannot be activated. "
                    "Run Validate first."
                ),
            )
        rule.status = payload.status
        # Schedules follow the rule: activating registers them, pausing removes them.
        from app.api.schedules import sync_schedules_for_rule

        await sync_schedules_for_rule(session, rule)

    await session.commit()
    await session.refresh(rule)
    return RuleOut.model_validate(rule)


@router.post("/{rule_id}/rollback/{version}", response_model=RuleOut)
async def rollback_rule(
    rule_id: int, version: int, session: SessionDep, admin: AdminUser
) -> RuleOut:
    """Roll back by creating a NEW version from an old one. History is never deleted."""
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    target = (
        await session.execute(
            select(RuleVersion).where(
                RuleVersion.rule_id == rule_id, RuleVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Version not found")

    _bump_version(
        session,
        rule,
        target.source_code,
        f"rollback to v{version}",
        admin.username,
    )
    await session.commit()
    await session.refresh(rule)
    return RuleOut.model_validate(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, session: SessionDep, admin: AdminUser) -> None:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    await session.delete(rule)
    await session.commit()


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------
@router.post("/{rule_id}/test", response_model=RuleTestResponse)
async def test_rule(
    rule_id: int, payload: RunRequest, session: SessionDep, _user: CurrentUser
) -> RuleTestResponse:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    run = await execute_rule(
        session,
        rule=rule,
        data_date=payload.data_date or _latest_data_date(rule.entity),
        triggered_by="manual_test",
        shadow=payload.shadow,
    )
    return RuleTestResponse(
        run_id=run.id,
        status=run.status,
        summary=run.summary,
        metrics=run.metrics,
        rows=run.rows,
        error_detail=run.error_detail,
        duration_ms=run.duration_ms,
    )


def _latest_data_date(entity: str = "LAD") -> date:
    base = settings.sourcedata_dir / entity.upper()
    if base.exists():
        folders = sorted(p.name for p in base.iterdir() if p.is_dir())
        for name in reversed(folders):
            try:
                return date.fromisoformat(name)
            except ValueError:
                continue
    return date.today()