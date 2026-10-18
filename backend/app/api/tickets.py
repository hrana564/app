"""Tickets: the alert an auditor acknowledges."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import Rule, Ticket
from app.schemas import TicketAck, TicketOut

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.get("", response_model=dict)
async def list_tickets(
    session: SessionDep,
    _user: CurrentUser,
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=500),
    status_: str | None = Query(None, alias="status"),
    rule_code: str | None = None,
    severity: str | None = None,
    sort: str = "created_at",
    order: str = "DESC",
) -> dict:
    stmt = select(Ticket)
    count_stmt = select(func.count()).select_from(Ticket)

    for value, column in ((status_, Ticket.status), (severity, Ticket.severity)):
        if value:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)
    if rule_code is not None:
        stmt = stmt.where(Ticket.rule_code == rule_code)
        count_stmt = count_stmt.where(Ticket.rule_code == rule_code)

    col = getattr(Ticket, sort, Ticket.created_at)
    stmt = stmt.order_by(col.desc() if order.upper() == "DESC" else col.asc())
    stmt = stmt.offset((page - 1) * perPage).limit(perPage)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    return {
        "data": [TicketOut.model_validate(t).model_dump(mode="json") for t in rows],
        "total": total,
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
    return TicketOut.model_validate(ticket)


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
    return TicketOut.model_validate(ticket)


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
    return TicketOut.model_validate(ticket)