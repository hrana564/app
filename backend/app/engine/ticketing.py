"""
Turning a failing run into a ticket.

One job, no policy:

    a run failed with rows  ->  one ticket holding those rows
    a run passed            ->  nothing

This module deliberately imports no Rule. It reads the run and nothing else, so
it can be exercised without a scheduler, a cadence or a rule object. Delivery
policy lives in the rule file, which is the thing a developer actually edits.

`ticket_no` is assigned after the insert because the number embeds the primary
key, which does not exist until the row is flushed.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, Ticket

log = logging.getLogger("abm.ticketing")


async def raise_ticket(
    session: AsyncSession, *, run: Run, severity: str, schedule_id: int | None
) -> Ticket | None:
    """Open one ticket for a failed run. Returns None if there is nothing to report."""
    if run.status != "fail" or not run.rows:
        return None

    ticket = Ticket(
        ticket_no="ABM-TKT-PENDING",
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
    ticket.ticket_no = f"ABM-TKT-{ticket.id:05d}"

    log.info(
        "ticket %s opened for %s run %s (%s row(s))",
        ticket.ticket_no, run.rule_code, run.id, len(ticket.rows),
    )
    return ticket