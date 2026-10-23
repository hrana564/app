from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, Ticket

log = logging.getLogger("abm.ticketing")


def run_status_for(rule_status: str) -> str:
    return "success" if rule_status == "success" else "failed"


def run_outcome_for(rule_status: str, row_count: int) -> str | None:
    if rule_status != "success":
        return None
    return "fail" if row_count else "pass"


async def raise_ticket(
    session: AsyncSession, *, run: Run, severity: str, schedule_id: int | None
) -> Ticket | None:
    if run.outcome != "fail" or not run.rows:
        return None

    ticket = Ticket(
        ticket_no=f"{run.rule_code}-TKT-PENDING",
        run_id=run.id,
        rule_id=run.rule_id,
        rule_code=run.rule_code,
        schedule_id=schedule_id,
        severity=severity,
        rows=run.rows,
        status="open",
    )
    session.add(ticket)
    await session.flush()
    # The rule code prefixes the number rather than a fixed brand, so the number
    # names the control that raised it AND the client it was raised for. An
    # auditor reading a ticket reference out of an email can tell where it came
    # from without opening the record.
    ticket.ticket_no = f"{run.rule_code}-TKT-{ticket.id:05d}"

    log.info(
        "ticket %s opened for %s run %s (%s row(s))",
        ticket.ticket_no, run.rule_code, run.id, len(ticket.rows),
    )
    return ticket