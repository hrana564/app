"""
Rules, keyed by their permanent `code`.

A rule is a chain of version rows in `rules_master`; exactly one is flagged
`is_version_active` (the head). Rules are therefore addressed by code, not by
row id - the code is what schedules reference and what filenames derive from.

Two different kinds of change, kept on separate endpoints on purpose:

    PUT /api/rules/{code}            content edit -> creates version n+1
    PUT /api/rules/{code}/status     pause / activate / retire -> no new version

Pausing a rule is not a logic change, so it must not burn a version number.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.engine.loader import read_source, write_source
from app.engine.rule_generator import (
    generate_rule_source,
    is_configured,
    preview_context,
)
from app.engine.rules import CodeError, validate_code, versioned_filename
from app.engine.runner import execute_rule
from app.engine.validation import RuleValidator, now
from app.models import Rule, RuleSchedule, Run, Ticket
from app.schemas import (
    RuleCreate,
    RuleGenerateRequest,
    RuleGenerateResponse,
    RuleOut,
    RuleStatusUpdate,
    RuleTestResponse,
    RuleUpdate,
    RuleVersionOut,
    RunRequest,
    ValidationReport,
)
from app.settings import settings

log = logging.getLogger("abm.api.rules")

router = APIRouter(prefix="/api/rules", tags=["rules"])

# Fields copied forward when a new version is created.
CARRIED_FIELDS = (
    "name", "entity", "description", "severity", "query_mode", "window_days",
    "ticket_mode", "group_by", "dedupe_mode", "dedupe_window_hours",
    "alerting_enabled", "datasource_hint",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def head(session, code: str) -> Rule | None:
    """The live version of a rule, or None."""
    return (
        await session.execute(
            select(Rule).where(Rule.code == code, Rule.is_version_active.is_(True))
        )
    ).scalar_one_or_none()


async def require_head(session, code: str) -> Rule:
    rule = await head(session, code)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"No rule with code {code!r}")
    return rule


async def max_version(session, code: str) -> int:
    current = (
        await session.execute(
            select(func.coalesce(func.max(Rule.version), 0)).where(Rule.code == code)
        )
    ).scalar_one()
    return int(current or 0)


async def publish_version(
    session,
    *,
    source: Rule,
    code: str,
    new_source: str,
    change_note: str,
    author: str,
    origin: str | None = None,
) -> Rule:
    """
    Create the next version and move the head flag onto it.

    The previous head is retained, so history is append-only and a rollback can
    copy any earlier version forward. A new version always starts as `draft`
    with validation reset - it must be re-validated and re-approved.
    """
    version = await max_version(session, code) + 1
    filename = versioned_filename(code, version)
    write_source(filename, new_source, settings.rules_dir)

    nxt = Rule(
        code=code,
        version=version,
        is_version_active=True,
        status="draft",
        source_file=filename,
        change_note=change_note,
        created_by=author,
        origin=origin or source.origin,
        validation_status="unvalidated",
        validation_messages=[],
        validated_at=None,
        validated_by=None,
        declared_datasources=list(source.declared_datasources or []),
        declared_alert=dict(source.declared_alert or {}),
    )
    for field in CARRIED_FIELDS:
        setattr(nxt, field, getattr(source, field))

    source.is_version_active = False
    session.add(nxt)
    await session.flush()
    return nxt


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
@router.get("", response_model=dict)
async def list_rules(
    session: SessionDep,
    _user: CurrentUser,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=500),
    sort: str = "code",
    order: str = "ASC",
    status_: str | None = Query(None, alias="status"),
    entity: str | None = None,
    q: str | None = None,
    all_versions: bool = False,
) -> dict:
    """List rules.

    Default returns the HEAD version of each rule - one row per code, which is
    what a rules list should show. `all_versions=true` returns every row.
    """
    stmt = select(Rule)
    count_stmt = select(func.count()).select_from(Rule)

    if not all_versions:
        stmt = stmt.where(Rule.is_version_active.is_(True))
        count_stmt = count_stmt.where(Rule.is_version_active.is_(True))

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

    column = getattr(Rule, sort, Rule.code)
    stmt = stmt.order_by(column.desc() if order.upper() == "DESC" else column.asc())
    stmt = stmt.offset((page - 1) * perPage).limit(perPage)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    return {
        "data": [RuleOut.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
    }


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
    """Prompt -> a new DRAFT rule. Never auto-approved."""
    entity = payload.entity.upper()
    target = payload.data_date or _latest_data_date(entity)
    generated = generate_rule_source(payload.prompt, entity, target)

    seq = (
        await session.execute(select(func.count()).select_from(Rule))
    ).scalar_one()
    try:
        code = validate_code(f"AUTO-{entity}-{target:%Y%m%d}-{seq + 1:03d}")
    except CodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = await head(session, code)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Rule {code} already exists")

    filename = versioned_filename(code, 1)
    write_source(filename, generated["source"], settings.rules_dir)

    rule = Rule(
        code=code,
        version=1,
        is_version_active=True,
        status="draft",
        source_file=filename,
        name=generated["explanation"][:255],
        entity=entity,
        description=payload.prompt.strip()[:2000],
        severity="medium",
        query_mode="SNAPSHOT",
        window_days=1,
        ticket_mode="ONE_PER_RUN",
        dedupe_mode="PER_DATA_DATE",
        alerting_enabled=True,
        datasource_hint=", ".join(generated["datasources_used"]),
        change_note=f"generated: {payload.prompt[:200]}",
        created_by=admin.username,
        origin="llm",
    )
    session.add(rule)
    await session.flush()
    await session.commit()
    await session.refresh(rule)

    return RuleGenerateResponse(
        rule=RuleOut.model_validate(rule),
        source_code=generated["source"],
        explanation=generated["explanation"],
        datasources_used=generated["datasources_used"],
    )


@router.get("/{code}")
async def get_rule(code: str, session: SessionDep, _user: CurrentUser) -> RuleOut:
    return RuleOut.model_validate(await require_head(session, code))


@router.get("/{code}/source")
async def get_source(code: str, session: SessionDep, _user: CurrentUser) -> dict:
    rule = await require_head(session, code)
    try:
        source = read_source(rule.source_file, settings.rules_dir)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Source file {rule.source_file} is missing on disk",
        ) from exc
    return {
        "code": rule.code,
        "version": rule.version,
        "source_file": rule.source_file,
        "source_code": source,
    }


@router.get("/{code}/versions", response_model=list[RuleVersionOut])
async def list_versions(
    code: str, session: SessionDep, _user: CurrentUser
) -> list[RuleVersionOut]:
    """Full history for a rule, newest first."""
    rows = (
        await session.execute(
            select(Rule).where(Rule.code == code).order_by(Rule.version.desc())
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No rule with code {code!r}")

    out: list[RuleVersionOut] = []
    for r in rows:
        try:
            source = read_source(r.source_file, settings.rules_dir)
        except (FileNotFoundError, ValueError):
            source = "(source file missing)"
        out.append(
            RuleVersionOut(
                version=r.version,
                is_version_active=r.is_version_active,
                status=r.status,
                validation_status=r.validation_status,
                source_file=r.source_file,
                source_code=source,
                change_note=r.change_note,
                created_by=r.created_by,
                created_at=r.created_at,
            )
        )
    return out


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(
    payload: RuleCreate, session: SessionDep, admin: AdminUser
) -> RuleOut:
    try:
        code = validate_code(payload.code)
    except CodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if await head(session, code) is not None:
        raise HTTPException(status_code=409, detail=f"Rule {code} already exists")

    filename = versioned_filename(code, 1)
    try:
        write_source(filename, payload.source_code, settings.rules_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rule = Rule(
        code=code,
        version=1,
        is_version_active=True,
        status=payload.status,
        source_file=filename,
        name=payload.name,
        entity=payload.entity.upper(),
        description=payload.description,
        severity=payload.severity,
        query_mode=payload.query_mode,
        window_days=payload.window_days,
        ticket_mode=payload.ticket_mode,
        group_by=payload.group_by,
        dedupe_mode=payload.dedupe_mode,
        alerting_enabled=payload.alerting_enabled,
        datasource_hint=payload.datasource_hint,
        change_note="initial version",
        created_by=admin.username,
        origin="manual",
    )
    session.add(rule)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Rule {code} already exists") from exc
    await session.refresh(rule)
    return RuleOut.model_validate(rule)


# ---------------------------------------------------------------------------
# update - content edits create a version
# ---------------------------------------------------------------------------
@router.put("/{code}", response_model=RuleOut)
async def update_rule(
    code: str, payload: RuleUpdate, session: SessionDep, admin: AdminUser
) -> RuleOut:
    """
    Editing a rule's logic creates version n+1 and moves the head flag.

    The new version starts as `draft` with validation reset, so it must be
    re-validated and re-approved before it can run.
    """
    current = await require_head(session, code)
    if code != validate_code(code):
        raise HTTPException(status_code=400, detail="Rule code is malformed")

    source = read_source(current.source_file, settings.rules_dir)
    if payload.source_code is not None:
        source = payload.source_code
    elif payload.change_note and not any(
        payload.field is not None
        for field in ("name", "description", "severity", "query_mode", "window_days")
    ):
        source = source  # metadata-only change still produces a version

    nxt = await publish_version(
        session,
        source=current,
        code=code,
        new_source=source,
        change_note=payload.change_note or "updated",
        author=admin.username,
    )
    for field in ("name", "description", "severity", "query_mode", "window_days"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(nxt, field, value)

    await session.commit()
    await session.refresh(nxt)
    return RuleOut.model_validate(nxt)


@router.put("/{code}/status", response_model=RuleOut)
async def set_status(
    code: str, payload: RuleStatusUpdate, session: SessionDep, admin: AdminUser
) -> RuleOut:
    """Lifecycle change only - no new version is created."""
    rule = await require_head(session, code)
    new_status = payload.status

    if new_status == "active" and rule.validation_status != "valid":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Rule {code} has not passed validation, so it cannot be "
                f"activated. Run Validate first."
            ),
        )

    rule.status = new_status
    await session.commit()
    await session.refresh(rule)

    # Schedules follow the rule: activating registers them, pausing removes them.
    from app.api.schedules import sync_schedules_for_code

    await sync_schedules_for_code(session, code)
    await session.commit()
    await session.refresh(rule)
    return RuleOut.model_validate(rule)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
@router.post("/{code}/validate", response_model=ValidationReport)
async def validate_rule(
    code: str, session: SessionDep, _user: CurrentUser
) -> ValidationReport:
    rule = await require_head(session, code)
    report = await _validate_and_store(session, rule, username=None)
    await session.commit()
    await session.refresh(rule)
    return report


@router.get("/{code}/validate-report", response_model=ValidationReport)
async def validation_report(
    code: str, session: SessionDep, _user: CurrentUser
) -> ValidationReport:
    rule = await require_head(session, code)
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
        declared_datasources=rule.declared_datasources or [],
        declared_alert=rule.declared_alert or {},
        name=rule.name,
        can_activate=rule.validation_status == "valid",
    )


async def _validate_and_store(session, rule: Rule, *, username: str | None) -> ValidationReport:
    validator = RuleValidator(rule.source_file, rule.entity, rules_dir=settings.rules_dir)
    result = await validator.validate(session)

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
    rule.declared_datasources = result.declared_datasources
    rule.declared_alert = result.declared_alert

    return ValidationReport(
        status=rule.validation_status,
        errors=result.errors,
        warnings=result.warnings,
        declared_datasources=result.declared_datasources,
        declared_alert=result.declared_alert,
        name=meta.get("name"),
        description=meta.get("description"),
        can_activate=result.ok,
    )


# ---------------------------------------------------------------------------
# rollback / delete / test
# ---------------------------------------------------------------------------
@router.post("/{code}/rollback/{version}", response_model=RuleOut)
async def rollback(
    code: str, version: int, session: SessionDep, admin: AdminUser
) -> RuleOut:
    """
    Roll back by publishing an OLD version's source as a NEW head.

    History is never rewritten: this reads like "v3 restored v1's logic", and
    the version number only moves forward.
    """
    current = await require_head(session, code)

    target = (
        await session.execute(
            select(Rule).where(Rule.code == code, Rule.version == version)
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"{code} has no version {version}"
        )

    try:
        source = read_source(target.source_file, settings.rules_dir)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot roll back: {target.source_file} is missing on disk",
        ) from exc

    nxt = await publish_version(
        session,
        source=current,
        code=code,
        new_source=source,
        change_note=f"rollback to v{version}",
        author=admin.username,
    )
    await session.commit()
    await session.refresh(nxt)
    return RuleOut.model_validate(nxt)


@router.delete("/{code}", status_code=204)
async def delete_rule(code: str, session: SessionDep, admin: AdminUser) -> None:
    """
    Delete a rule and every version of it.

    Refused when runs or tickets reference it: findings must not disappear
    because someone deleted a rule. Retire it instead.
    """
    versions = (
        await session.execute(select(Rule).where(Rule.code == code))
    ).scalars().all()
    if not versions:
        raise HTTPException(status_code=404, detail=f"No rule with code {code!r}")

    ids = [r.id for r in versions]
    for model, label in ((Run, "run"), (Ticket, "ticket")):
        count = (
            await session.execute(
                select(func.count()).select_from(model).where(model.rule_id.in_(ids))
            )
        ).scalar_one()
        if count:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Rule {code} has {count} {label}(s) and cannot be deleted. "
                    f"Set it to 'retired' instead so the history is preserved."
                ),
            )

    # No FK from schedules, so they must be cleaned up explicitly.
    for sched in (
        await session.execute(select(RuleSchedule).where(RuleSchedule.rule_code == code))
    ).scalars().all():
        await scheduler_remove(sched.id)
        await session.delete(sched)

    # Collect the source files before the rows go, so the files on disk can be
    # removed with them - otherwise deleting a rule orphans its version files.
    source_files = [r.source_file for r in versions if r.source_file]
    file_names = {Path(f).name for f in source_files}

    for rule in versions:
        await session.delete(rule)
    await session.commit()

    for name in file_names:
        try:
            (Path(settings.rules_dir) / name).unlink(missing_ok=True)
        except OSError as exc:
            # A stray file is untidy, not dangerous - never fail the delete.
            log.warning("could not remove %s: %s", name, exc)


async def scheduler_remove(schedule_id: int) -> None:
    from app.engine import scheduler

    await scheduler.remove_schedule(schedule_id)


@router.post("/{code}/test", response_model=RuleTestResponse)
async def test_rule(
    code: str, payload: RunRequest, session: SessionDep, _user: CurrentUser
) -> RuleTestResponse:
    rule = await require_head(session, code)
    run = await execute_rule(
        session,
        rule=rule,
        data_date=payload.data_date or _latest_data_date(rule.entity),
        triggered_by="manual_test",
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
        for name in reversed(sorted(p.name for p in base.iterdir() if p.is_dir())):
            try:
                return date.fromisoformat(name)
            except ValueError:
                continue
    return date.today()