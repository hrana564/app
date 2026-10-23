from __future__ import annotations

import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep, require
from app.engine import description
from app.engine.datasources import default_entity
from app.engine.discovery import latest_date
from app.engine.loader import read_source, write_source
from app.engine.rules import CodeError, scoped_code, validate_code, versioned_filename
from app.engine.runner import execute_rule
from app.engine.validation import RuleValidator, declared_tables, now
from app.models import Rule, RuleExplanation, RuleSchedule, Run, Ticket, User
from app.permissions import (
    RULES_ACTIVATE,
    RULES_DELETE,
    RULES_READ,
    RULES_TEST,
    RULES_WRITE,
)
from app.schemas import (
    PublishOut,
    RollbackOut,
    RuleCreate,
    RuleOut,
    RulePublish,
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

# The capabilities this router gates. Named once, so the endpoint signature
# below reads as the question being asked rather than as a role.
RulesReader = Annotated[User, Depends(require(RULES_READ))]
RulesTester = Annotated[User, Depends(require(RULES_TEST))]
RulesWriter = Annotated[User, Depends(require(RULES_WRITE))]
RulesActivator = Annotated[User, Depends(require(RULES_ACTIVATE))]
RulesDeleter = Annotated[User, Depends(require(RULES_DELETE))]

# Fields copied forward when a new version is created.
CARRIED_FIELDS = (
    "name", "entity", "description", "severity", "lookback_days",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def head(session, code: str) -> Rule | None:
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


async def working_draft(session, code: str) -> Rule | None:
    return (
        await session.execute(
            select(Rule)
            .where(Rule.code == code, Rule.status == "draft")
            .order_by(Rule.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def row_for(session, code: str, version: int | None = None) -> Rule:
    if version is None:
        return await require_head(session, code)

    row = (
        await session.execute(
            select(Rule).where(Rule.code == code, Rule.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        latest = await max_version(session, code)
        if not latest:
            raise HTTPException(status_code=404, detail=f"No rule with code {code!r}")
        raise HTTPException(
            status_code=404,
            detail=f"{code} has no version {version}; it has v1 to v{latest}.",
        )
    return row


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
    promote: bool = True,
) -> Rule:
    version = await max_version(session, code) + 1
    filename = versioned_filename(code, version)
    write_source(filename, new_source, settings.rules_dir)

    nxt = Rule(
        code=code,
        version=version,
        is_version_active=promote,
        status="draft",
        source_file=filename,
        change_note=change_note,
        created_by=author,
        origin=origin or source.origin,
        validation_status="unvalidated",
        validation_messages=[],
        validated_at=None,
        validated_by=None,
        # From the NEW source, not the old rule. Copying the previous version's
        # list left the recorded truth describing the previous file until
        # somebody pressed Validate - which meant the rule's own page and the
        # catalogue's "used by" both answered for a version nobody was running.
        declared_datasources=declared_tables(new_source),
    )
    for field in CARRIED_FIELDS:
        setattr(nxt, field, getattr(source, field))

    if promote:
        source.is_version_active = False
    session.add(nxt)
    await session.flush()
    return nxt


async def pending_version(session, code: str) -> int | None:
    value = (
        await session.execute(
            select(func.max(Rule.version)).where(
                Rule.code == code,
                Rule.is_version_active.is_(False),
                Rule.status == "draft",
            )
        )
    ).scalar_one()
    return int(value) if value else None
# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
@router.get("", response_model=dict)
async def list_rules(
    session: SessionDep,
    _user: RulesReader,
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

    # One grouped query for the drafts on this page, rather than one per row: the
    # list marks a rule that has a version waiting to be published.
    pending: dict[str, int] = {}
    if rows:
        pending_rows = await session.execute(
            select(Rule.code, func.max(Rule.version))
            .where(
                Rule.code.in_([r.code for r in rows]),
                Rule.is_version_active.is_(False),
                Rule.status == "draft",
            )
            .group_by(Rule.code)
        )
        pending = {code: int(version) for code, version in pending_rows}

    data = []
    for r in rows:
        out = RuleOut.model_validate(r)
        out.pending_version = pending.get(r.code)
        data.append(out.model_dump(mode="json"))
    return {"data": data, "total": total}
@router.get("/{code}")
async def get_rule(
    code: str, session: SessionDep, _user: RulesReader, version: int | None = None
) -> RuleOut:
    """One version row, or the head.

    The page asks for a version by number, because a rule is a chain of rows and
    "the rule" is whichever of them you are reading.
    """
    rule = await row_for(session, code, version)
    out = RuleOut.model_validate(rule)
    out.pending_version = await pending_version(session, code)
    return out


@router.get("/{code}/source")
async def get_source(
    code: str, session: SessionDep, _user: RulesReader, version: int | None = None
) -> dict:
    rule = await row_for(session, code, version)
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
    code: str, session: SessionDep, _user: RulesReader
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
def source_or_409(rule: Rule) -> str:
    try:
        return read_source(rule.source_file, settings.rules_dir)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Source file {rule.source_file} is missing on disk",
        ) from exc
# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(
    payload: RuleCreate, session: SessionDep, admin: RulesWriter
) -> RuleOut:
    try:
        code = validate_code(payload.code)
    except CodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entity = (payload.entity or default_entity()).upper()
    # The code names the client, and the client is the row's business - so the
    # prefix follows the row and is never written into the file.
    code = scoped_code(code, entity)

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
        entity=entity,
        description=payload.description,
        severity=payload.severity,
        lookback_days=payload.lookback_days,
        change_note="initial version",
        created_by=admin.username,
        origin=payload.origin,
        # Read from the source just written, so the rule's page and the
        # catalogue's "used by" are right from the moment it exists rather
        # than after someone remembers to validate it.
        declared_datasources=declared_tables(payload.source_code),
    )
    session.add(rule)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Rule {code} already exists") from exc
    await session.refresh(rule)

    # The description came with the rule, so it is stored with it. The draft
    # is born complete: a reviewer can read what the control does before
    # deciding whether it goes live.
    if payload.explanation is not None:
        await description.store(
            session, rule, payload.source_code, payload.explanation
        )

    return RuleOut.model_validate(rule)
# ---------------------------------------------------------------------------
# update - a content edit rewrites the working draft
# ---------------------------------------------------------------------------
@router.put("/{code}", response_model=RuleOut)
async def update_rule(
    code: str, payload: RuleUpdate, session: SessionDep, admin: RulesWriter
) -> RuleOut:
    """
    Edit a rule's logic.

    The edit lands on the rule's ONE working draft:

      * a rule that has never been published already IS a draft, so the edit
        rewrites that version in place and the number does not move. A new
        version per save is what produced a v2 beside a v1 that had never run.
      * a published rule with a draft candidate waiting has that candidate
        rewritten, so repeated edits still leave exactly one draft.
      * a published rule with no draft gets version n+1 as a new draft
        candidate. The live version keeps running until that is published.

    Either way the live control is never taken off the air by a save, and a
    draft is never numbered twice.
    """
    current = await require_head(session, code)
    if code != validate_code(code):
        raise HTTPException(status_code=400, detail="Rule code is malformed")

    draft = await working_draft(session, code)

    if payload.source_code is not None:
        source = payload.source_code
    else:
        source = read_source((draft or current).source_file, settings.rules_dir)

    if draft is not None:
        # Rewrite the working draft in place. Its version number stays: nothing
        # has been released, so there is no history to keep and no reason to
        # spend the next number. The run folder keeps its own copy of any source
        # a test run used, so rewriting the file here does not rewrite evidence.
        nxt = draft
        write_source(nxt.source_file, source, settings.rules_dir)
        nxt.change_note = payload.change_note or "updated"
        nxt.validation_status = "unvalidated"
        nxt.validation_messages = []
        nxt.validated_at = None
        nxt.validated_by = None
        nxt.declared_datasources = declared_tables(source)
        if payload.origin is not None:
            nxt.origin = payload.origin
    else:
        nxt = await publish_version(
            session,
            source=current,
            code=code,
            new_source=source,
            change_note=payload.change_note or "updated",
            author=admin.username,
            origin=payload.origin,
            promote=False,
        )

    for field in ("name", "description", "severity", "lookback_days"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(nxt, field, value)

    await session.commit()
    await session.refresh(nxt)

    # The description of the draft. A revision by the agent brings its own,
    # so the words always match the code they were written about. An edit that
    # did not bring one leaves the old words against the old code - and the
    # reader withholds a description whose source has changed, so a stale
    # description can never be shown against the wrong code.
    if payload.explanation is not None:
        await description.store(session, nxt, source, payload.explanation)

    out = RuleOut.model_validate(nxt)
    out.pending_version = await pending_version(session, code)
    return out
@router.put("/{code}/status", response_model=RuleOut)
async def set_status(
    code: str, payload: RuleStatusUpdate, session: SessionDep, admin: RulesActivator
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
    code: str, session: SessionDep, admin: RulesWriter, version: int | None = None
) -> ValidationReport:
    """Validate a rule and record the verdict on its row.

    Validation writes to the rule - the status, the timestamp, the recorded
    datasources - so it is a change to the estate and is gated as one. It
    exists to be the precondition for activation, and activation is the
    administrator's.

    A version may be named so the draft on screen is the one validated. Without
    one this is the head, which is what a live rule wants.
    """
    rule = await row_for(session, code, version)
    report = await _validate_and_store(session, rule, username=None)
    await session.commit()
    await session.refresh(rule)
    return report


@router.get("/{code}/validate-report", response_model=ValidationReport)
async def validation_report(
    code: str, session: SessionDep, _user: RulesReader, version: int | None = None
) -> ValidationReport:
    rule = await row_for(session, code, version)
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

    return ValidationReport(
        status=rule.validation_status,
        errors=result.errors,
        warnings=result.warnings,
        declared_datasources=result.declared_datasources,
        name=meta.get("name"),
        description=meta.get("description"),
        can_activate=result.ok,
    )
# ---------------------------------------------------------------------------
# rollback / delete / test
# ---------------------------------------------------------------------------
@router.post("/{code}/rollback/{version}", response_model=RollbackOut)
async def rollback(
    code: str, version: int, session: SessionDep, admin: RulesActivator
) -> RollbackOut:
    """Restore an old version: copy it forward, validate it, put it live.

    History is never rewritten. Restoring v1 publishes v5 with v1's source, so
    the version history reads "restored from v1" and the number only moves
    forward.

    The copy is validated before it is activated, because the estate may have
    moved since v1 ran - a table it read may have been removed. "Restored" must
    not mean "switched the live control to code that cannot read its data", so a
    copy that fails validation stays a draft and nothing goes live.
    """
    current = await require_head(session, code)
    if current.version == version:
        raise HTTPException(
            status_code=409,
            detail=f"{code} v{version} is already the current version.",
        )

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
            detail=f"Cannot restore: {target.source_file} is missing on disk",
        ) from exc

    nxt = await publish_version(
        session,
        source=current,
        code=code,
        new_source=source,
        change_note=f"restored from v{version}",
        author=admin.username,
        origin=target.origin,
    )
    await session.flush()

    # The description of the version being restored travels with it. The
    # source is the same bytes that were described before, so the words are
    # copied rather than paid for again - and the copy is made against the
    # hash of the source, so it cannot be about the wrong code.
    await description.copy_forward(session, nxt, source)

    report = await _validate_and_store(session, nxt, username=admin.username)
    activated = report.status == "valid"
    if activated:
        nxt.status = "active"
    await session.commit()
    await session.refresh(nxt)

    if activated:
        # Schedules follow the rule: activating registers them.
        from app.api.schedules import sync_schedules_for_code

        await sync_schedules_for_code(session, code)
        await session.commit()
        await session.refresh(nxt)

    return RollbackOut(
        rule=RuleOut.model_validate(nxt),
        validation=report,
        activated=activated,
    )


@router.post("/{code}/publish", response_model=PublishOut)
async def publish_rule(
    code: str, payload: RulePublish, session: SessionDep, admin: RulesActivator
) -> PublishOut:
    """Put a draft version on the air.

    A draft exists beside the live version, which is what lets a control be
    rewritten and read without ever leaving the air. Publishing is the move that
    ends that: the candidate is validated, and only if it passes does the head
    flag move to it. The version it replaces stays in the history and stops
    running.

    The validation is not a formality. The estate may have moved since the draft
    was written - a table it reads may have been removed - and promoting code
    that cannot read its data would swap a working control for a broken one.

    A draft that is already the head - a rule that was never published - is
    activated in place rather than a head being moved: there is nothing live
    for it to replace.
    """
    current = await require_head(session, code)
    rule = await row_for(session, code, payload.version)

    if rule.is_version_active and rule.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"{code} v{payload.version} is already the live version.",
        )

    report = await _validate_and_store(session, rule, username=admin.username)
    activated = report.status == "valid"
    if activated:
        if rule.is_version_active:
            # The draft IS the head: nothing to demote, just put it on the air.
            rule.status = "active"
        else:
            current.is_version_active = False
            rule.is_version_active = True
            rule.status = "active"
    await session.commit()
    await session.refresh(rule)

    if activated:
        # Schedules follow the rule: promoting it re-registers them, and the
        # scheduler resolves the code to the new head when one fires.
        from app.api.schedules import sync_schedules_for_code

        await sync_schedules_for_code(session, code)
        await session.commit()
        await session.refresh(rule)

    out = RuleOut.model_validate(rule)
    out.pending_version = await pending_version(session, code)
    return PublishOut(rule=out, validation=report, activated=activated)
@router.delete("/{code}/draft", status_code=204)
async def discard_draft(code: str, session: SessionDep, _user: RulesWriter) -> None:
    """Throw away the rule's working draft.

    There is at most one. Discarding a candidate leaves the published version
    exactly as it was; discarding the draft of a rule that was never published
    leaves nothing, so the whole rule goes with it.

    Gated on `rules.write` rather than `rules.delete`: throwing away your own
    uncommitted work is authoring, not destroying the estate. The version that
    is on the air is never touched here.
    """
    draft = await working_draft(session, code)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"{code} has no draft to discard")

    published = (
        await session.execute(
            select(func.count())
            .select_from(Rule)
            .where(Rule.code == code, Rule.status != "draft")
        )
    ).scalar_one()

    # Everything that goes. A rule that was never published has no version to
    # fall back to, so discarding its draft discards the rule - including any
    # stray drafts an older build left behind.
    if published:
        targets = [draft]
    else:
        targets = (
            await session.execute(select(Rule).where(Rule.code == code))
        ).scalars().all()

    target_ids = [t.id for t in targets]

    # Findings are never thrown away with a draft. A draft cannot raise one
    # until it is live, but if one somehow does the answer is no.
    findings = (
        await session.execute(
            select(func.count()).select_from(Ticket).where(Ticket.rule_id.in_(target_ids))
        )
    ).scalar_one()
    if findings:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{code} has {findings} finding(s) and cannot be discarded. "
                f"Set it to 'retired' instead so the history is preserved."
            ),
        )

    # A test run can point at a draft now, and Run.rule_id is a RESTRICT foreign
    # key, so those runs go before the rows. The run folder keeps its own copy of
    # the source, so nothing else is lost with them.
    for run in (
        await session.execute(select(Run).where(Run.rule_id.in_(target_ids)))
    ).scalars().all():
        await session.delete(run)

    # Explanations are keyed to the version rows and describe code that is about
    # to stop existing.
    for expl in (
        await session.execute(
            select(RuleExplanation).where(RuleExplanation.rule_id.in_(target_ids))
        )
    ).scalars().all():
        await session.delete(expl)

    if not published:
        # Nothing survives, so any schedule is cleaned up rather than left
        # pointing at a code that no longer exists.
        for sched in (
            await session.execute(
                select(RuleSchedule).where(RuleSchedule.rule_code == code)
            )
        ).scalars().all():
            await scheduler_remove(sched.id)
            await session.delete(sched)

    file_names = {Path(t.source_file).name for t in targets if t.source_file}
    for t in targets:
        await session.delete(t)
    await session.commit()

    for name in file_names:
        try:
            (Path(settings.rules_dir) / name).unlink(missing_ok=True)
        except OSError as exc:
            # A stray file is untidy, not dangerous - never fail the discard.
            log.warning("could not remove %s: %s", name, exc)
@router.delete("/{code}", status_code=204)
async def delete_rule(code: str, session: SessionDep, admin: RulesDeleter) -> None:
    """
    Delete a rule, every version of it, and everything it produced.

    Delete is delete: the runs the rule performed, the findings those runs
    raised, the explanations written about it and its schedules all belong to
    the rule and go with it. A finding is not a reason to keep a rule alive -
    keeping the history is what 'retired' is for, and retiring is a separate,
    deliberate choice made on the rule's own status endpoint. Asking the
    estate to forget a rule means forgetting what it found.

    The whole thing is one transaction, ordered children-first so the RESTRICT
    foreign keys are satisfied, and the disk artifacts (the version source
    files and each run's folder) are removed once the rows are gone.
    """
    versions = (
        await session.execute(select(Rule).where(Rule.code == code))
    ).scalars().all()
    if not versions:
        raise HTTPException(status_code=404, detail=f"No rule with code {code!r}")

    ids = [r.id for r in versions]
    run_ids = [
        rid for (rid,) in await session.execute(
            select(Run.id).where(Run.rule_id.in_(ids))
        )
    ]

    # Order is explicit and load-bearing. Rule, Run and Ticket carry no ORM
    # relationship between them, so the unit of work has no dependency graph to
    # sort by and would emit the rule DELETE first - which the RESTRICT foreign
    # keys (rule_runs.rule_id, tickets.rule_id) refuse. A flush between the two
    # groups makes the order the database sees the order written here.
    if run_ids:
        await session.execute(delete(Ticket).where(Ticket.run_id.in_(run_ids)))
    await session.execute(delete(Ticket).where(Ticket.rule_id.in_(ids)))
    await session.flush()

    await session.execute(delete(Run).where(Run.rule_id.in_(ids)))
    await session.flush()

    # Explanations describe version source that is about to stop existing.
    await session.execute(
        delete(RuleExplanation).where(RuleExplanation.rule_id.in_(ids))
    )

    # No FK from schedules, so they must be cleaned up explicitly - and pulled
    # from the scheduler, or it would keep firing a code that no longer exists.
    for sched in (
        await session.execute(select(RuleSchedule).where(RuleSchedule.rule_code == code))
    ).scalars().all():
        await scheduler_remove(sched.id)
        await session.delete(sched)

    # Collect the disk paths before the rows go, so the files on disk can be
    # removed with them - otherwise deleting a rule orphans its version files.
    file_names = {Path(r.source_file).name for r in versions if r.source_file}

    await session.execute(delete(Rule).where(Rule.id.in_(ids)))
    await session.commit()

    for name in file_names:
        try:
            (Path(settings.rules_dir) / name).unlink(missing_ok=True)
        except OSError as exc:
            # A stray file is untidy, not dangerous - never fail the delete.
            log.warning("could not remove %s: %s", name, exc)

    # Each run folder holds the exact source and the exact CSVs that run read.
    # The run is gone, so the folder is a record of nothing.
    for run_id in run_ids:
        folder = Path(settings.runs_dir) / f"{run_id:06d}"
        if folder.exists():
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                log.warning("could not remove run folder %s: %s", folder, exc)
async def scheduler_remove(schedule_id: int) -> None:
    from app.engine import scheduler

    await scheduler.remove_schedule(schedule_id)
@router.post("/{code}/test", response_model=RuleTestResponse)
async def test_rule(
    code: str, payload: RunRequest, session: SessionDep, _user: RulesTester
) -> RuleTestResponse:
    # A version may be named so a draft can be tested. Without one this is the
    # head, which is what a live rule's "Run now" wants. A draft has its own
    # source file, so testing one has to name it or the button runs different
    # code to the page it was pressed on.
    rule = await row_for(session, code, payload.version)
    run = await execute_rule(
        session,
        rule=rule,
        data_date=payload.data_date or data_date_for(rule.entity),
        triggered_by="manual_test",
    )
    return RuleTestResponse(
        run_id=run.id,
        status=run.status,
        outcome=run.outcome,
        summary=run.summary,
        metrics=run.metrics,
        rows=run.rows,
        error_detail=run.error_detail,
        duration_ms=run.duration_ms,
    )
def data_date_for(entity: str | None = None) -> date:
    found = latest_date(entity or default_entity())
    return date.fromisoformat(found) if found else date.today()
