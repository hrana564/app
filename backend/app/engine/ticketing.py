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


def run_status_for(rule_status: str) -> str:
    """Did the rule EXECUTE? `success`|`failed` from the rule becomes the same.

    Nothing here looks at the rows. A rule that runs and finds a breach ran
    perfectly well, and recording that as a failed execution is how an audit
    tool starts reporting its own successes as faults.
    """
    return "success" if rule_status == "success" else "failed"


def run_outcome_for(rule_status: str, row_count: int) -> str | None:
    """What did the control FIND? `pass`|`fail`, or None if it never ran.

    A rule that could not run has no opinion about the control, so it has no
    outcome. That is the distinction this whole module exists to keep.

    Both this and run_status_for are pure and are used by the runner AND the
    replay tool - if the two derived them differently, a replay would report a
    difference that is not one.
    """
    if rule_status != "success":
        return None
    return "fail" if row_count else "pass"


async def raise_ticket(
    session: AsyncSession, *, run: Run, severity: str, schedule_id: int | None
) -> Ticket | None:
    """Open one ticket for a breach. Returns None if there is nothing to report."""
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