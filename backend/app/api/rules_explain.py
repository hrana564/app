from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.deps import SessionDep, require
from app.api.rules import RulesReader, RulesWriter, row_for, source_or_409
from app.engine import description
from app.engine.explain import derive, source_hash
from app.engine.llm import is_configured
from app.engine.validation import now
from app.models import Rule, RuleExplanation, User
from app.permissions import RULES_EXPLAIN_REVIEW
from app.schemas import (
    ExplanationDraft,
    ExplanationEdit,
    ExplanationOut,
    ExplanationReview,
    ExplanationStepDraft,
    ExplanationStepOut,
    GateOut,
)

router = APIRouter(prefix="/api/rules", tags=["rules"])

ExplanationReviewer = Annotated[User, Depends(require(RULES_EXPLAIN_REVIEW))]

# ---------------------------------------------------------------------------
# the plain-English explanation
# ---------------------------------------------------------------------------
def _step_out(step: dict[str, Any]) -> ExplanationStepOut:
    return ExplanationStepOut(
        n=int(step.get("n", 0)),
        text=str(step.get("text") or ""),
        lines=list(step.get("lines") or []),
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
    steps: list[dict[str, Any]] = []
    outputs: list[str] = []
    breach = ""
    origin = "none"
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
        steps = list(row.steps or [])
        outputs = list(row.outputs or [])
        breach = row.breach_condition or ""
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

    return ExplanationOut(
        code=rule.code,
        version=rule.version,
        source_sha256=digest or source_hash(source),
        origin=origin,
        stored=row is not None and not stale,
        stale=stale,
        llm_configured=is_configured(),
        # The rule's own one-liner - written by the model that wrote the rule.
        # It is always there, and it needs no model call to produce.
        purpose=rule.description or outline.purpose,
        breach_condition=breach,
        steps=[_step_out(s) for s in steps],
        outputs=outputs,
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
    source = source_or_409(rule)
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
    not stored at all - there is no paraphrase to fall back on, because a
    description nobody checked is not a description. The caller is told which
    checks refused the words, so the failure is actionable rather than a
    silence.

    This is an EXPLICIT action. It is not part of publishing: a rule that
    goes live must not cost a model call, and a Publish button that waits on
    a model is a Publish button that appears broken.
    """
    rule = await row_for(session, code, version)
    source = source_or_409(rule)
    outline = derive(source)
    digest = source_hash(source)

    if not is_configured():
        raise HTTPException(
            status_code=409,
            detail="No model is configured, so a description cannot be written. "
                   "The code below the page is the whole truth.",
        )

    words, gates = await run_in_threadpool(
        description.write_words,
        source,
        description.Meta(
            code=rule.code,
            name=rule.name,
            severity=rule.severity,
            lookback_days=rule.lookback_days,
        ),
    )

    if words is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "The model could not write a description that passed the checks"
                + _gate_detail(gates)
            ),
        )

    row = await description.store(session, rule, source, words)
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "The description did not pass the checks"
                + _gate_detail(description.accept(source, words).gates)
            ),
        )
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
    source = source_or_409(rule)
    outline = derive(source)
    digest = source_hash(source)

    # The columns a reviewer needs were named when the description was first
    # written, and correcting the wording does not change them.
    existing = await _stored(session, rule)

    words = ExplanationDraft(
        breach_condition=payload.breach_condition,
        steps=[
            ExplanationStepDraft(n=int(s.get("n", 0)), text=str(s.get("text") or ""))
            for s in payload.steps
        ],
        outputs=list(existing.outputs or []) if existing else [],
        origin="edited",
        generated_by=user.username,
    )

    judged = description.accept(source, words)
    if judged.unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Step {judged.unknown[0]} is not in this rule's source; it has "
                   f"{len(judged.known)} steps.",
        )
    if judged.missing:
        raise HTTPException(
            status_code=422,
            detail="Every step needs words: " + ", ".join(str(n) for n in judged.missing),
        )

    row = await description.store(session, rule, source, words)
    if row is None:
        raise HTTPException(
            status_code=422,
            detail="The description did not pass the checks"
            + _gate_detail(judged.gates),
        )
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
    source = source_or_409(rule)
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
async def _gate_detail(gates: list) -> str:
    refused = "; ".join(
        f"{g.name}: {g.detail}" for g in gates if not g.ok and g.blocking
    )
    return f": {refused}" if refused else ""
