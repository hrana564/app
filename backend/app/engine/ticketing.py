"""
Turning a failing run into tickets.

Separated from the runner because the policy here is a product decision, not
mechanics. Three questions:

  1. How many tickets does one failing run produce?  (rules.ticket_mode)
  2. Does a repeat trigger of the same breach open a new ticket, or bump the
     existing one?  (rules.dedupe_mode)
  3. What happens if the breach continues after an auditor acknowledged the
     first ticket?

The policy chosen for (3):
  - still OPEN      -> bump `occurrences`. Do not flood the inbox with N
                       identical tickets for one ongoing problem.
  - ACKNOWLEDGED /
    DISMISSED       -> open a NEW ticket, referencing the predecessor. An
                       auditor has already looked at this; silently keeping it
                       on the same ticket would hide that it is still
                       breaching every single day.

`alerting_enabled = false` mutes tickets without pausing the rule, which is the
"we know, we're not ready" state.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Rule, Run, Ticket
from app.settings import settings

log = logging.getLogger("abm.ticketing")

TICKET_MODE_ONE_PER_RUN = "ONE_PER_RUN"
TICKET_MODE_ONE_PER_GROUP = "ONE_PER_GROUP"
TICKET_MODE_ONE_PER_ROW = "ONE_PER_ROW"

DEDUPE_NONE = "NONE"
DEDUPE_PER_DATA_DATE = "PER_DATA_DATE"
DEDUPE_FINGERPRINT = "FINGERPRINT"

# Hard ceiling on tickets raised by a single run, so a badly written rule
# cannot flood the inbox with thousands of alerts.
MAX_TICKETS_PER_RUN = 50


def fingerprint(rows: list[dict[str, Any]]) -> str:
    """Stable hash of an exception set.

    Rows are sorted by their canonical JSON so key ordering in the dict (which
    the query engine may vary) cannot change the hash.
    """
    canonical = sorted(
        json.dumps(row, sort_keys=True, default=str) for row in (rows or [])
    )
    digest = hashlib.sha256("\n".join(canonical).encode("utf-8"))
    return digest.hexdigest()[:32]


def partition_rows(
    run: Run, ticket_mode: str, group_by: str | None
) -> list[tuple[str | None, list[dict[str, Any]]]]:
    """Split a run's exception rows into the ticket groups to raise."""
    rows = run.rows or []

    if ticket_mode == TICKET_MODE_ONE_PER_ROW:
        return [(None, [row]) for row in rows]

    if ticket_mode == TICKET_MODE_ONE_PER_GROUP and group_by:
        buckets: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
        for row in rows:
            key = str(row.get(group_by, "(blank)"))
            buckets.setdefault(key, []).append(row)
        return list(buckets.items())

    return [(None, rows)]


async def raise_tickets(
    session: AsyncSession, *, rule: Rule, run: Run, schedule_id: int | None
) -> list[Ticket]:
    """Create or bump tickets for a failing run. Returns affected tickets."""
    if run.status != "fail":
        return []
    if not rule.alerting_enabled:
        log.debug("rule %s: alerting muted, no ticket raised", rule.code)
        return []

    groups = partition_rows(run, rule.ticket_mode, rule.group_by)
    if len(groups) > MAX_TICKETS_PER_RUN:
        log.warning(
            "rule %s produced %s ticket groups; capping at %s for this run",
            rule.code, len(groups), MAX_TICKETS_PER_RUN,
        )
        groups = groups[:MAX_TICKETS_PER_RUN]

    affected: list[Ticket] = []
    for group_key, rows in groups:
        ticket = await _upsert_ticket(
            session,
            rule=rule,
            run=run,
            schedule_id=schedule_id,
            group_key=group_key,
            rows=rows,
        )
        if ticket is not None:
            affected.append(ticket)
    return affected


async def _upsert_ticket(
    session: AsyncSession,
    *,
    rule: Rule,
    run: Run,
    schedule_id: int | None,
    group_key: str | None,
    rows: list[dict[str, Any]],
) -> Ticket | None:
    fingerprint_value = (
        fingerprint(rows) if rule.dedupe_mode == DEDUPE_FINGERPRINT else None
    )

    existing = None
    if rule.dedupe_mode in (DEDUPE_PER_DATA_DATE, DEDUPE_FINGERPRINT):
        existing = await _find_repeat(
            session, rule=rule, run=run, group_key=group_key,
            fingerprint_value=fingerprint_value,
        )

    if existing is not None and existing.status == "open":
        # Same breach, still open: bump rather than duplicate.
        existing.occurrences += 1
        existing.last_run_id = run.id
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.rows = rows
        await session.flush()
        log.debug(
            "ticket %s bumped to %s occurrences (rule %s)",
            existing.ticket_no, existing.occurrences, rule.code,
        )
        return existing

    # Either no repeat, or the repeat was already acknowledged - in which case
    # the auditor has seen it and a fresh ticket is the honest signal.
    ticket = Ticket(
        ticket_no="ABM-TKT-PENDING",
        run_id=run.id,
        rule_id=rule.id,
        rule_code=rule.code,
        schedule_id=schedule_id,
        severity=rule.severity,
        rows=rows,
        status="open",
        occurrences=1,
        first_run_id=run.id,
        last_run_id=run.id,
        fingerprint=fingerprint_value,
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(ticket)
    await session.flush()
    ticket.ticket_no = f"ABM-TKT-{ticket.id:05d}"
    return ticket


async def _find_repeat(
    session: AsyncSession,
    *,
    rule: Rule,
    run: Run,
    group_key: str | None,
    fingerprint_value: str | None,
) -> Ticket | None:
    """
    Find a prior ticket for the same rule against the same data date.

    Tickets do not copy `data_date`, so the comparison joins back to runs.
    For DEDUPE_PER_DATA_DATE the newest such ticket is the candidate; the
    caller decides whether to bump it (still open) or supersede it
    (already acknowledged).
    """
    from app.models import Run as RunModel

    if run.data_date is None:
        # No data date means no meaningful dedupe key; do not guess.
        return None

    stmt = (
        select(Ticket)
        .join(RunModel, Ticket.run_id == RunModel.id)
        .where(
            Ticket.rule_id == rule.id,
            RunModel.data_date == run.data_date,
        )
        .order_by(Ticket.created_at.desc())
        .limit(1)
    )

    if fingerprint_value:
        stmt = stmt.where(Ticket.fingerprint == fingerprint_value)

    result = await session.execute(stmt)
    return result.scalar_one_or_none()
