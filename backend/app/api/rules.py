"""
Rules, keyed by their permanent `code`.

A rule is a chain of version rows in `rules_master`; exactly one is flagged
`is_version_active` (the head). Rules are therefore addressed by code, not by
row id - the code is what schedules reference and what filenames derive from.

Two different kinds of change, kept on separate endpoints on purpose:

    PUT /api/rules/{code}            content edit -> rewrites the working draft
    PUT /api/rules/{code}/status     pause / activate / retire -> no new version

Pausing a rule is not a logic change, so it must not burn a version number.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.api.deps import SessionDep, require
from app.engine import explain_llm
from app.engine.catalog import database_catalog
from app.engine.datasources import default_entity
from app.engine.discovery import latest_date
from app.engine.explain import check, derive, derived_prose, source_hash
from app.engine.loader import read_source, write_source
from app.engine.rule_generator import is_configured, preview_context
from app.engine.rules import CodeError, scoped_code, validate_code, versioned_filename
from app.engine.runner import execute_rule
from app.engine.validation import RuleValidator, declared_tables, now
from app.models import Rule, RuleExplanation, RuleSchedule, Run, Ticket, User
from app.permissions import (
    RULES_ACTIVATE,
    RULES_DELETE,
    RULES_EXPLAIN_REVIEW,
    RULES_GENERATE,
    RULES_READ,
    RULES_TEST,
    RULES_WRITE,
)
from app.schemas import (
    AgentStep,
    ExplanationEdit,
    ExplanationOut,
    ExplanationReview,
    ExplanationStepOut,
    GateOut,
    GenerationAttempt,
    PublishOut,
    RollbackOut,
    RuleCreate,
    RuleGenerateRequest,
    RuleGenerateResponse,
    RuleOut,
    RulePublish,
    RuleReviseRequest,
    RuleStatusUpdate,
    RuleTestResponse,
    RuleUpdate,
    RuleVersionOut,
    RunRequest,
    TrialOut,
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
RulesGenerator = Annotated[User, Depends(require(RULES_GENERATE))]
ExplanationReviewer = Annotated[User, Depends(require(RULES_EXPLAIN_REVIEW))]

# Fields copied forward when a new version is created.
CARRIED_FIELDS = (
    "name", "entity", "description", "severity", "lookback_days",
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


async def working_draft(session, code: str) -> Rule | None:
    """The one draft an edit rewrites - the newest version still in draft.

    A rule has at most one, and it is one of two things:

      * the head itself, when the rule has never been published: the first
        version is created as a draft and there is no live control for it to
        sit beside; or
      * the single non-head draft candidate, when a published rule is being
        rewritten before its replacement goes on the air.

    `status == "draft"` is what identifies it; a version that has ever run
    keeps `active`, `paused` or `retired`, so a demoted history row is never
    mistaken for a draft. Taking the NEWEST draft - rather than the head - also
    keeps the working copy the one with the latest edits when an older build
    left a rule with more than one draft.
    """
    return (
        await session.execute(
            select(Rule)
            .where(Rule.code == code, Rule.status == "draft")
            .order_by(Rule.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def row_for(session, code: str, version: int | None = None) -> Rule:
    """The version asked for, or the head when none is asked for.

    Every read that can be pointed at one version goes through here, so the
    page showing v1 and the endpoints serving it cannot disagree about which
    row that is. A version row carries its own source file, so the source, the
    explanation and the hash that pins them all follow from this one lookup.
    """
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
    """
    Create the next version.

    `promote` is the whole of the difference between a candidate and a release:

      promote=True   the new version becomes the head, so the scheduler runs it.
                     The previous head is demoted. This is a rollback, and a
                     first version.
      promote=False  the new version is a DRAFT CANDIDATE. The live version keeps
                     the head and keeps running until this one is published. This
                     is an edit, so the control is never off the air while its
                     replacement is written and read.

    Either way the new row starts as `draft` with validation reset - a candidate
    must be validated, and published, before it runs.
    """
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
    """The newest DRAFT that is not the head - a candidate waiting to publish.

    One number answers the question on both ends of it: the live version's page
    says "v3 is waiting", and v3's own page reads the same number and knows it is
    a candidate rather than a record of something already superseded.

    The `status == "draft"` filter is load-bearing. Without it, publishing a
    draft leaves the version it just replaced as the newest non-head row - and
    the live page announces that demoted history as a draft waiting to be
    published, which is the opposite of what happened.
    """
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
    return preview_context(catalog, code, data_date or _latest_data_date(code))


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
    target = payload.data_date or _latest_data_date(entity)

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
    source = _source_or_409(base)

    entity = base.entity
    target = payload.data_date or _latest_data_date(entity)
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
    """Refuse a selection the client does not have registered.

    The selection binds. A rule that reads a table outside it is rejected before
    it runs, so saying so here names the table and the client rather than leaving
    the agent to discover it.
    """
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
    """The agent's answer, on the wire. Shared by generate and revise - the two
    differ in the prompt they build, not in what they hand back."""
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
    """The version a revision starts from.

    A pending draft is the thing being worked on, so a revision continues it
    rather than starting again from the live code. With no draft it is the live
    version.
    """
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
    """Run a revision in a worker thread, for the same reason as `_run_agent`."""
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
    """Run the agent in a worker thread.

    The model calls and the trials are all blocking, so the whole loop runs in
    this thread and the endpoint awaits it. The bound comes from the model
    client's own timeout, not from here: a request that gives up early leaves a
    thread mid-thought, which is worse than one that waits.
    """
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
    """The next free `<CLIENT>-GEN-nn`.

    Generated rules are numbered separately from the audit programme's own
    codes (`LAD-CA-03`), because a generated rule has not been through the
    programme's numbering and must not look as though it has. The number
    counts per client, so each client's generated rules run 01, 02, 03.
    """
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


# ---------------------------------------------------------------------------
# the plain-English explanation
# ---------------------------------------------------------------------------
def _step_out(step: dict[str, Any], derived: dict[int, Any]) -> ExplanationStepOut:
    n = int(step.get("n", 0))
    source = derived.get(n)
    return ExplanationStepOut(
        n=n,
        text=str(step.get("text") or ""),
        lines=list(step.get("lines") or (source.lines if source else [])),
        kind=source.kind if source else "",
        explainable=source.explainable if source else True,
    )


async def _stored(session, rule: Rule) -> RuleExplanation | None:
    return (
        await session.execute(
            select(RuleExplanation).where(RuleExplanation.rule_id == rule.id)
        )
    ).scalar_one_or_none()


def _explanation_out(
    rule: Rule,
    outline,
    source: str,
    row: RuleExplanation | None,
    *,
    stale: bool,
    digest: str | None = None,
) -> ExplanationOut:
    """What the page is shown: always something true, and never stale words.

    The derivation runs on every read, so the steps and their line ranges are
    current even when the stored prose is not. Stale prose is dropped rather
    than shown - a description of the previous version sitting under this
    version's code is the failure this whole design exists to prevent.
    """
    from app.engine.explain import derived_prose

    prose = derived_prose(outline)
    origin = "derived"
    gates: list[GateOut] = []
    meta = {
        "generated_by": "",
        "model": "",
        "prompt_version": "",
        "generated_at": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }

    if row is not None and not stale:
        prose = {
            "breach_condition": row.breach_condition,
            "steps": row.steps,
            "cannot_explain": row.cannot_explain,
            "outputs": row.outputs,
        }
        origin = row.origin
        gates = [GateOut(**g) for g in (row.gates or [])]
        meta = {
            "generated_by": row.generated_by,
            "model": row.model,
            "prompt_version": row.prompt_version,
            "generated_at": row.created_at,
            "reviewed_by": row.reviewed_by,
            "reviewed_at": row.reviewed_at,
        }

    derived = {s.n: s for s in outline.steps}
    return ExplanationOut(
        code=rule.code,
        version=rule.version,
        source_sha256=digest or source_hash(source),
        origin=origin,
        stored=row is not None and not stale,
        stale=stale,
        llm_configured=is_configured(),
        purpose=outline.purpose,
        breach_condition=prose.get("breach_condition") or outline.purpose,
        steps=[_step_out(s, derived) for s in prose.get("steps") or []],
        outputs=list(prose.get("outputs") or []),
        cannot_explain=list(prose.get("cannot_explain") or []),
        gates=gates,
        **meta,
    )


@router.get("/{code}/explain", response_model=ExplanationOut)
async def get_explanation(
    code: str, session: SessionDep, _user: RulesReader, version: int | None = None
) -> ExplanationOut:
    """What this version does, in plain English.

    Cheap and safe to call: the structure is derived from the version's own file
    on every request, so the page never shows a description of code that has
    moved on. Nothing is generated here - a GET does not spend money at a model.
    """
    rule = await row_for(session, code, version)
    source = _source_or_409(rule)
    outline = derive(source)
    row = await _stored(session, rule)
    digest = source_hash(source)
    stale = row is not None and row.source_sha256 != digest
    return _explanation_out(rule, outline, source, row, stale=stale, digest=digest)


@router.post("/{code}/explain", response_model=ExplanationOut)
async def generate_explanation(
    code: str, session: SessionDep, user: RulesWriter, version: int | None = None
) -> ExplanationOut:
    """Write (or rewrite) the description with the model, and store it.

    Every answer is gated before it is stored, and a model that fails twice is
    not stored at all - the derived wording is, marked as such. With no model
    configured this endpoint still works: it stores the derived description,
    which is the whole point of the derivation being the substrate.
    """
    rule = await row_for(session, code, version)
    source = _source_or_409(rule)
    outline = derive(source)
    digest = source_hash(source)

    prose, gates, model = await run_in_threadpool(
        explain_llm.write,
        outline,
        source,
        {"code": rule.code, "name": rule.name, "severity": rule.severity,
         "lookback_days": rule.lookback_days},
    )

    if prose is None:
        prose = derived_prose(outline)
        origin = "derived"
    else:
        origin = "ai"

    values: dict[str, Any] = {
        "rule_code": rule.code,
        "version": rule.version,
        "source_sha256": digest,
        "breach_condition": prose.get("breach_condition") or "",
        "steps": prose.get("steps") or [],
        "cannot_explain": prose.get("cannot_explain") or outline.cannot_explain,
        "outputs": prose.get("outputs") or outline.outputs,
        "origin": origin,
        "model": model,
        "prompt_version": explain_llm.PROMPT_VERSION if origin == "ai" else "",
        "generated_by": user.username,
        "gates": [g.as_dict() for g in gates],
        # A new description has not been signed off, whatever the old one had.
        "reviewed_by": None,
        "reviewed_at": None,
    }
    row = await _upsert(session, rule, values)
    return _explanation_out(rule, outline, source, row, stale=False, digest=digest)


@router.put("/{code}/explain", response_model=ExplanationOut)
async def edit_explanation(
    code: str, payload: ExplanationEdit, session: SessionDep, user: RulesWriter,
    version: int | None = None,
) -> ExplanationOut:
    """A person correcting the words.

    The text is theirs: it is stored as written and marked `edited`, because a
    description a human has changed is a different kind of evidence from one a
    model wrote. The line ranges are still the derivation's - those are links
    into the file, and a link that does not land where it says is worse than no
    link.
    """
    rule = await row_for(session, code, version)
    source = _source_or_409(rule)
    outline = derive(source)
    spans = {s.n: list(s.lines) for s in outline.steps}

    steps: list[dict[str, Any]] = []
    for step in payload.steps:
        try:
            n = int(step.get("n", 0))
        except (TypeError, ValueError):
            continue
        if n not in spans:
            raise HTTPException(
                status_code=422,
                detail=f"Step {n} is not in this rule's source; it has "
                       f"{len(spans)} steps.",
            )
        steps.append({"n": n, "text": str(step.get("text") or "").strip(), "lines": spans[n]})

    missing = sorted(set(spans) - {s["n"] for s in steps})
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Every step needs words: " + ", ".join(str(n) for n in missing),
        )

    prose = {"breach_condition": payload.breach_condition, "steps": steps}
    gates = check(outline, prose, len(source.splitlines()))
    digest = source_hash(source)

    values: dict[str, Any] = {
        "rule_code": rule.code,
        "version": rule.version,
        "source_sha256": digest,
        "breach_condition": payload.breach_condition,
        "steps": steps,
        "cannot_explain": outline.cannot_explain,
        "outputs": outline.outputs,
        "origin": "edited",
        "model": "",
        "prompt_version": "",
        "generated_by": user.username,
        "gates": [g.as_dict() for g in gates],
        "reviewed_by": None,
        "reviewed_at": None,
    }
    row = await _upsert(session, rule, values)
    return _explanation_out(rule, outline, source, row, stale=False, digest=digest)


@router.post("/{code}/explain/review", response_model=ExplanationOut)
async def review_explanation(
    code: str, payload: ExplanationReview, session: SessionDep,
    user: ExplanationReviewer, version: int | None = None,
) -> ExplanationOut:
    """Sign the description off, or withdraw the sign-off.

    A distinct capability from editing the rule: the reviewer is the person
    answerable for the control, and they are usually not the person who wrote
    it. Withdrawing is the same call with `approved: false`, because a
    rejection that cannot be recorded leaves the page claiming a review that
    did not happen.
    """
    rule = await row_for(session, code, version)
    source = _source_or_409(rule)
    outline = derive(source)
    row = await _stored(session, rule)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="This rule has no description to sign off yet.",
        )
    digest = source_hash(source)
    if row.source_sha256 != digest:
        raise HTTPException(
            status_code=409,
            detail="The description is older than the code. Regenerate it, then "
                   "sign it off.",
        )

    row.reviewed_by = user.username if payload.approved else None
    row.reviewed_at = now() if payload.approved else None
    await session.commit()
    await session.refresh(row)
    return _explanation_out(rule, outline, source, row, stale=False, digest=digest)


async def _upsert(
    session, rule: Rule, values: dict[str, Any]
) -> RuleExplanation:
    row = await _stored(session, rule)
    if row is None:
        row = RuleExplanation(rule_id=rule.id, rule_code=rule.code, version=rule.version)
        session.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return row


def _source_or_409(rule: Rule) -> str:
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
    Delete a rule and every version of it.

    Findings are never deleted: a rule that has raised a ticket is refused, and
    the caller is pointed at 'retired' so the history survives.

    A rule that never ran on a schedule can be deleted along with its manual
    test runs - an experiment must be discardable. A rule that RAN on a
    schedule has history worth keeping even if it found nothing, so it is
    refused too.
    """
    versions = (
        await session.execute(select(Rule).where(Rule.code == code))
    ).scalars().all()
    if not versions:
        raise HTTPException(status_code=404, detail=f"No rule with code {code!r}")

    ids = [r.id for r in versions]

    findings = (
        await session.execute(
            select(func.count()).select_from(Ticket).where(Ticket.rule_id.in_(ids))
        )
    ).scalar_one()
    if findings:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Rule {code} has {findings} finding(s) and cannot be deleted. "
                f"Set it to 'retired' instead so the history is preserved."
            ),
        )

    scheduled = (
        await session.execute(
            select(func.count())
            .select_from(Run)
            .where(Run.rule_id.in_(ids), Run.triggered_by == "schedule")
        )
    ).scalar_one()
    if scheduled:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Rule {code} has {scheduled} scheduled run(s) and cannot be "
                f"deleted. Set it to 'retired' instead so the history is preserved."
            ),
        )

    # Only manual test runs can be attached at this point; they go with the rule
    # rather than blocking it.
    for run in (
        await session.execute(select(Run).where(Run.rule_id.in_(ids)))
    ).scalars().all():
        await session.delete(run)

    # Explanations describe version source that is about to stop existing.
    for expl in (
        await session.execute(
            select(RuleExplanation).where(RuleExplanation.rule_id.in_(ids))
        )
    ).scalars().all():
        await session.delete(expl)

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
        data_date=payload.data_date or _latest_data_date(rule.entity),
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


def _latest_data_date(entity: str | None = None) -> date:
    """The newest day we hold for a client, or today when we hold none.

    The scan itself lives in `discovery.available_dates()`. Two implementations
    of "which days exist" is how an endpoint and the Data Sources screen come to
    disagree about the day they are showing.
    """
    found = latest_date(entity or default_entity())
    return date.fromisoformat(found) if found else date.today()