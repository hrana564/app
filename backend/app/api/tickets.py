"""Tickets: the alert an auditor acknowledges."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.scope import scoped
from app.models import Rule, Run, Ticket
from app.schemas import BulkAck, TicketAck, TicketOut

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

# Upper bound on rows scanned when grouping. An inbox is small; this exists so
# a pathological filter cannot pull an unbounded result set into memory.
MAX_GROUP_SCAN = 5000


def _filtered(stmt, count_stmt, *, status, rule_code, severity, q, entity=None):
    """Narrow the page query and the count query identically.

    Both always join rule_runs: the ticket's summary is the run's summary, and
    free-text search needs it too. Keeping the join here means the two queries
    can never disagree about which rows match - and `total` is what pagination
    trusts.

    `entity` narrows to one client. It is a parameter of this function rather
    than something the bulk endpoint adds afterwards, because "acknowledge
    everything on screen" must mean the same rows the screen showed - and that
    includes the client the app bar is currently scoped to.

    count_stmt is unused by the bulk endpoint, which passes a throwaway.
    """
    stmt = stmt.join(Run, Ticket.run_id == Run.id)
    count_stmt = count_stmt.join(Run, Ticket.run_id == Run.id)

    stmt = scoped(stmt, Ticket, entity)
    count_stmt = scoped(count_stmt, Ticket, entity)

    for value, column in ((status, Ticket.status), (severity, Ticket.severity)):
        if value:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)
    if rule_code is not None:
        stmt = stmt.where(Ticket.rule_code == rule_code)
        count_stmt = count_stmt.where(Ticket.rule_code == rule_code)
    if q:
        # Ticket number is searchable too, because that is what people paste in.
        like = f"%{q}%"
        stmt = stmt.where(Ticket.ticket_no.ilike(like) | Run.summary.ilike(like))
        count_stmt = count_stmt.where(Ticket.ticket_no.ilike(like) | Run.summary.ilike(like))
    return stmt, count_stmt


@router.get("", response_model=dict)
async def list_tickets(
    session: SessionDep,
    _user: CurrentUser,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=500),
    status_: str | None = Query(None, alias="status"),
    rule_code: str | None = None,
    severity: str | None = None,
    q: str | None = None,
    entity: str | None = None,
    sort: str = "created_at",
    order: str = "DESC",
) -> dict:
    stmt = select(Ticket)
    stmt, count_stmt = _filtered(
        stmt,
        select(func.count()).select_from(Ticket),
        status=status_, rule_code=rule_code, severity=severity, q=q, entity=entity,
    )

    col = getattr(Ticket, sort, Ticket.created_at)
    stmt = stmt.add_columns(Run.summary.label("run_summary"))
    stmt = stmt.order_by(col.desc() if order.upper() == "DESC" else col.asc())
    stmt = stmt.offset((page - 1) * perPage).limit(perPage)

    rows = (await session.execute(stmt)).all()
    total = (await session.execute(count_stmt)).scalar_one()

    data = []
    for ticket, run_summary in rows:
        d = TicketOut.model_validate(ticket).model_dump(mode="json")
        d["summary"] = run_summary or ""
        data.append(d)
    return {"data": data, "total": total}


@router.post("/acknowledge-all", response_model=dict)
async def acknowledge_all(
    payload: BulkAck,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    """
    Acknowledge every OPEN ticket matching the given filters, in one transaction.

    Scoped by the same parameters as the list so the toolbar can act on exactly
    what is on screen, and never on the whole table by accident - including the
    client entity, which is what the app bar is scoped to. The filters are
    echoed back: a bulk audit action that does not report what it did is its own
    problem.
    """
    stmt = select(Ticket).where(Ticket.status == "open")
    stmt, _ = _filtered(
        stmt,
        select(func.count()).select_from(Ticket),
        status=None,  # already pinned to "open"
        rule_code=payload.rule_code,
        severity=payload.severity,
        q=payload.q,
        entity=payload.entity,
    )
    tickets = (await session.execute(stmt)).scalars().all()

    now = datetime.now(timezone.utc)
    for ticket in tickets:
        ticket.status = payload.status
        ticket.acknowledged_by = user.username
        ticket.acknowledged_at = now
        ticket.ack_note = payload.note
    await session.commit()

    return {
        "acknowledged": len(tickets),
        "ticket_nos": [t.ticket_no for t in tickets],
        "rule_code": payload.rule_code,
        "severity": payload.severity,
        "q": payload.q,
        "entity": payload.entity,
    }


@router.get("/grouped", response_model=dict)
async def grouped_tickets(
    session: SessionDep,
    _user: CurrentUser,
    group_by: str = Query("rule_code", pattern="^(rule_code|severity|status)$"),
    status_: str | None = Query(None, alias="status"),
    rule_code: str | None = None,
    severity: str | None = None,
    q: str | None = None,
    entity: str | None = None,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=200),
    order: str = "DESC",
) -> dict:
    """
    Tickets grouped across the WHOLE filtered set, paginated by group.

    Grouping a page of tickets is how a group ends up split across three pages
    with a count on each - and then a header says 8 while the acknowledge-all
    call behind it touches 17. Counts here are computed before paging, so a
    group is never fragmented and its count is the number of tickets it holds.
    """
    stmt = select(Ticket)
    stmt, _ = _filtered(
        stmt,
        select(func.count()).select_from(Ticket),
        status=status_, rule_code=rule_code, severity=severity, q=q, entity=entity,
    )
    stmt = stmt.add_columns(Run.summary.label("run_summary"))
    order_col = (
        Ticket.created_at.desc() if order.upper() == "DESC" else Ticket.created_at.asc()
    )
    stmt = stmt.order_by(order_col).limit(MAX_GROUP_SCAN)

    buckets: dict[str, list[dict]] = {}
    for ticket, run_summary in (await session.execute(stmt)).all():
        d = TicketOut.model_validate(ticket).model_dump(mode="json")
        d["summary"] = run_summary or ""
        buckets.setdefault(str(getattr(ticket, group_by) or "—"), []).append(d)

    ordered = sorted(buckets.items(), key=lambda kv: _group_sort(kv[0], group_by))

    # Grouping by rule reads better with the rule's own name attached.
    rule_names: dict[str, str] = {}
    if group_by == "rule_code" and ordered:
        codes = [k for k, _ in ordered]
        names = (
            await session.execute(
                select(Rule.code, Rule.name).where(Rule.code.in_(codes))
            )
        ).all()
        rule_names = dict(names)

    window = ordered[(page - 1) * perPage : page * perPage]
    groups = [
        {
            "key": key,
            "label": rule_names.get(key, key) if group_by == "rule_code" else None,
            "total": len(items),
            "open": sum(1 for t in items if t["status"] == "open"),
            "severity_mix": _mix(items, "severity"),
            "status_mix": _mix(items, "status"),
            "tickets": items,
        }
        for key, items in window
    ]
    return {
        "groups": groups,
        "total_groups": len(ordered),
        "total_tickets": sum(len(items) for _, items in ordered),
        "scanned": sum(len(items) for _, items in ordered),
    }


def _mix(items: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in items:
        out[t[field]] = out.get(t[field], 0) + 1
    return out


def _group_sort(key: str, group_by: str):
    """Most alarming group first, rather than alphabetical."""
    if group_by == "severity":
        order = ["critical", "high", "medium", "low"]
    elif group_by == "status":
        order = ["open", "acknowledged", "dismissed"]
    else:
        return (0, key)
    return (order.index(key) if key in order else len(order), key)


@router.get("/summary")
async def summary(session: SessionDep, _user: CurrentUser, entity: str | None = None) -> dict:
    """Aggregate counts for the dashboard. One round trip, Power Automate friendly."""
    rows = (
        await session.execute(
            scoped(
                select(Ticket.status, func.count()).group_by(Ticket.status),
                Ticket,
                entity,
            )
        )
    ).all()
    by_status = {status: count for status, count in rows}

    sev = (
        await session.execute(
            scoped(
                select(Ticket.severity, func.count())
                .where(Ticket.status == "open")
                .group_by(Ticket.severity),
                Ticket,
                entity,
            )
        )
    ).all()

    return {
        "total": sum(by_status.values()),
        "open": by_status.get("open", 0),
        "acknowledged": by_status.get("acknowledged", 0),
        "dismissed": by_status.get("dismissed", 0),
        "open_by_severity": {severity: count for severity, count in sev},
    }


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: int, session: SessionDep, _user: CurrentUser
) -> TicketOut:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    run = await session.get(Run, ticket.run_id)
    d = TicketOut.model_validate(ticket).model_dump(mode="json")
    d["summary"] = run.summary if run else ""
    return d


@router.post("/{ticket_id}/acknowledge", response_model=TicketOut)
async def acknowledge(
    ticket_id: int, payload: TicketAck, session: SessionDep, user: CurrentUser
) -> TicketOut:
    """Both roles may acknowledge - this is the auditor's primary action."""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status != "open":
        raise HTTPException(
            status_code=409, detail=f"Ticket already {ticket.status}"
        )

    ticket.status = payload.status
    ticket.acknowledged_by = user.username
    ticket.acknowledged_at = datetime.now(timezone.utc)
    ticket.ack_note = payload.note

    await session.commit()
    await session.refresh(ticket)
    run = await session.get(Run, ticket.run_id)
    d = TicketOut.model_validate(ticket).model_dump(mode="json")
    d["summary"] = run.summary if run else ""
    return d


@router.post("/{ticket_id}/reopen", response_model=TicketOut)
async def reopen(
    ticket_id: int, session: SessionDep, user: CurrentUser
) -> TicketOut:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.status = "open"
    ticket.acknowledged_by = None
    ticket.acknowledged_at = None
    await session.commit()
    await session.refresh(ticket)
    run = await session.get(Run, ticket.run_id)
    d = TicketOut.model_validate(ticket).model_dump(mode="json")
    d["summary"] = run.summary if run else ""
    return d