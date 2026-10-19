"""Tickets: the alert an auditor acknowledges."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import Rule, Run, Ticket
from app.schemas import BulkAck, TicketAck, TicketOut

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


def _filtered(stmt, count_stmt, *, status, rule_code, severity, q):
    """Narrow the page query and the count query identically.

    Both always join rule_runs: the ticket's summary is the run's summary, and
    free-text search needs it too. Keeping the join here means the two queries
    can never disagree about which rows match - and `total` is what pagination
    trusts.

    count_stmt is unused by the bulk endpoint, which passes a throwaway.
    """
    stmt = stmt.join(Run, Ticket.run_id == Run.id)
    count_stmt = count_stmt.join(Run, Ticket.run_id == Run.id)

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
    sort: str = "created_at",
    order: str = "DESC",
) -> dict:
    stmt = select(Ticket)
    stmt, count_stmt = _filtered(
        stmt,
        select(func.count()).select_from(Ticket),
        status=status_, rule_code=rule_code, severity=severity, q=q,
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
    what is on screen, and never on the whole table by accident. The filters are
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
    }


@router.get("/summary")
async def summary(session: SessionDep, _user: CurrentUser) -> dict:
    """Aggregate counts for the dashboard. One round trip, Power Automate friendly."""
    rows = (
        await session.execute(
            select(Ticket.status, func.count()).group_by(Ticket.status)
        )
    ).all()
    by_status = {status: count for status, count in rows}

    sev = (
        await session.execute(
            select(Ticket.severity, func.count()).where(Ticket.status == "open").group_by(Ticket.severity)
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