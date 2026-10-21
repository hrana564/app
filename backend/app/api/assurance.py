"""
The assurance read model behind the landing page.

A head of audit does not ask how many rows are in `rule_runs`. They ask three
questions, in this order:

    1. Did my controls actually look at the data?
    2. What did they find, and how much of it is still open?
    3. Is it getting better or worse?

This module answers exactly those, from the runs and tickets that already exist.
Nothing is stored and nothing is denormalised into a summary table: the whole
payload is derived on read, so it cannot drift from the ledger it describes.

THE ONE RULE
------------
A control that did not run has no opinion. It is counted as UNCHECKED - never as
pass, never as absent, never folded into a percentage that quietly excludes it.
This is the distinction the product is built on (see app/engine/ticketing.py),
and a dashboard that flattens it back into "excluded from the bars" throws away
the only fact a head of audit needs: a day a control never ran is an UNASSURED
day, not a clean one.

Each data date therefore partitions the assured controls into four states which
sum back to the total:

    passed + breached + errored + unchecked == assured controls

Exceptions sit alongside that partition rather than inside it. One breaching run
can carry thousands of exception rows, so they are a separate magnitude - the
exposure - not another slice of the same pie.

WINDOWING
---------
The window is measured in DATA DATES, not calendar days, and every figure in it
is filtered the same way: runs by `rule_runs.data_date`, and tickets by the
`data_date` of the run that raised them. A ticket carries no data date of its own
because a finding is about the day the control looked at, not the moment it was
written down.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import SessionDep, require
from app.api.scope import scoped
from app.models import Rule, RuleSchedule, Run, Ticket, User
from app.permissions import ASSURANCE_READ

router = APIRouter(prefix="/api", tags=["assurance"])

# The capability this router's read is gated on. The landing page is the
# first thing anybody sees, so every role holds it - but it is named rather
# than left implicit, because a matrix with an entry nothing enforces is a
# matrix that lies about what it covers.
AssuranceReader = Annotated[User, Depends(require(ASSURANCE_READ))]

UNCHECKED = "unchecked"
PASSED = "passed"
BREACH = "breach"
ERRORED = "errored"

# The screen is a briefing, not a register: a head of audit wants the worst few,
# and the inbox for the rest.
TOP_CONTROLS = 8
TOP_FINDINGS = 6

SEVERITY_ORDER = ("critical", "high", "medium", "low")
"""Severity, worst first. Shared with the ticket inbox's group sort, so the
dashboard and the inbox it sends you to can never rank a finding differently."""


# ---------------------------------------------------------------------------
# Pure derivations - readable without a database, and testable without one
# ---------------------------------------------------------------------------
def state_of(passed: int, breached: int, errored: int) -> str:
    """Which of the four states one control is in, on one data date.

    A breach outranks a pass on the same day, because a control that found
    something and a control that found nothing are different facts and that day
    had both. An execution failure only counts when nothing else happened: a
    control that reported a breach after a failed morning still has an opinion,
    and the failure is visible on its own in the Runs ledger.
    """
    if breached:
        return BREACH
    if passed:
        return PASSED
    if errored:
        return ERRORED
    return UNCHECKED


def summarise_day(assured: int, seen: dict[str, str]) -> dict[str, int]:
    """One data date, as counts over the assured controls.

    `seen` maps a control code to its state for this date. Anything absent owes an
    opinion and did not give one, so `unchecked` is the shortfall rather than a
    separate tally - which is what makes the four numbers sum to `assured`.
    """
    tally = {PASSED: 0, BREACH: 0, ERRORED: 0}
    for value in seen.values():
        tally[value] += 1
    unchecked = max(0, assured - len(seen))
    return {**tally, UNCHECKED: unchecked, "assured": assured, "reported": assured - unchecked}


def age_in_days(moment: datetime | None, now: datetime) -> int:
    """Whole days between two instants, never negative."""
    if moment is None:
        return 0
    return max(0, (now - moment).days)


def rank_controls(rows: list[dict]) -> list[dict]:
    """Worst control first.

    Days breached outrank raw exception counts: a control that breached on every
    day of the window is a systemic failure, while a large one-off count is a
    single bad day that has already been raised. Exceptions only break the tie.
    """
    return sorted(
        rows,
        key=lambda r: (-r["breached_days"], -r["exceptions"], -r["missing_days"], r["code"]),
    )


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------
@router.get("/dashboard")
async def dashboard(
    session: SessionDep,
    user: AssuranceReader,
    entity: str | None = None,
    window: int = Query(14, ge=2, le=90),
) -> dict:
    """One payload, drawn from the ledger, answering the three questions.

    `window` counts DATA DATES, not calendar days, so a day the ETL never
    delivered cannot appear as a day the controls missed.
    """
    now = datetime.now(timezone.utc)
    head = Rule.is_version_active.is_(True)

    # --- the controls this client is expected to be covered by --------------
    # Assured = live and past the validation gate. Anything else owes nothing
    # yet, so counting it as a gap would blame the controls for a control that
    # was never switched on.
    controls = (
        await session.execute(
            scoped(
                select(
                    Rule.code, Rule.name, Rule.severity, Rule.status, Rule.validation_status
                )
                .where(head)
                .order_by(Rule.code),
                Rule,
                entity,
            )
        )
    ).all()

    assured = {
        code: {"code": code, "name": name, "severity": severity}
        for code, name, severity, status, validation in controls
        if status == "active" and validation == "valid"
    }
    pending = [
        {
            "code": code,
            "name": name,
            "severity": severity,
            "status": status,
            "validation_status": validation,
        }
        for code, name, severity, status, validation in controls
        if code not in assured
    ]

    # A cadence, or none. Without this, an on-demand control that last ran a week
    # ago reads identically to a daily control that silently stopped, and only
    # one of those is a fault. A schedule has no entity of its own, so it is
    # scoped by the rule codes it belongs to - which is exactly the set above.
    cadences: dict[str, list[str]] = {}
    for rule_code, label, kind in (
        await session.execute(
            select(RuleSchedule.rule_code, RuleSchedule.label, RuleSchedule.kind)
            .where(RuleSchedule.rule_code.in_(list(assured) or [""]))
        )
    ).all():
        cadences.setdefault(rule_code, []).append(label or (kind or "").title())

    # --- the window ---------------------------------------------------------
    recent = (
        await session.execute(
            scoped(
                select(Run.data_date)
                .where(Run.data_date.isnot(None))
                .distinct()
                .order_by(Run.data_date.desc())
                .limit(window * 2),
                Run,
                entity,
            )
        )
    ).scalars().all()

    dates = sorted(recent[:window])
    previous = sorted(recent[window : window * 2])
    in_scope = dates + previous

    # --- every run in scope, collapsed to one row per date per control ------
    runs = (
        await session.execute(
            scoped(
                select(
                    Run.data_date,
                    Run.rule_code,
                    func.count().filter(Run.outcome == "pass"),
                    func.count().filter(Run.outcome == "fail"),
                    func.count().filter(Run.status == "failed"),
                    func.coalesce(func.sum(func.jsonb_array_length(Run.rows)), 0),
                )
                .where(Run.data_date.in_(in_scope))
                .group_by(Run.data_date, Run.rule_code),
                Run,
                entity,
            )
        )
    ).all()

    state_by_day: dict[date, dict[str, str]] = {}
    rollup: dict[str, dict] = {}
    exceptions_by_day: dict[date, int] = {}
    findings_by_day: dict[date, int] = {}

    for day, code, passed, breached, errored, exceptions in runs:
        # A retired or superseded version can still have runs inside the window.
        # It is not one of the controls being reported on, so it must not inflate
        # coverage on a day it no longer speaks for.
        if code not in assured or day not in dates:
            continue

        state = state_of(passed, breached, errored)
        state_by_day.setdefault(day, {})[code] = state
        exceptions_by_day[day] = exceptions_by_day.get(day, 0) + exceptions
        findings_by_day[day] = findings_by_day.get(day, 0) + breached

        tallied = rollup.setdefault(
            code,
            {
                "days": 0,
                "breached_days": 0,
                "errored_days": 0,
                "exceptions": 0,
                "last_breach": None,
            },
        )
        tallied["days"] += 1
        tallied["exceptions"] += exceptions
        if state == BREACH:
            tallied["breached_days"] += 1
            if tallied["last_breach"] is None or day > tallied["last_breach"]:
                tallied["last_breach"] = day
        elif state == ERRORED:
            tallied["errored_days"] += 1

    days = []
    for day in dates:
        entry = summarise_day(len(assured), state_by_day.get(day, {}))
        entry["date"] = day.isoformat()
        entry["exceptions"] = exceptions_by_day.get(day, 0)
        entry["findings"] = findings_by_day.get(day, 0)
        days.append(entry)

    # --- findings still open -------------------------------------------------
    open_rows = (
        await session.execute(
            scoped(
                select(
                    Ticket.severity,
                    func.count(),
                    func.coalesce(func.sum(func.jsonb_array_length(Ticket.rows)), 0),
                )
                .where(Ticket.status == "open")
                .group_by(Ticket.severity),
                Ticket,
                entity,
            )
        )
    ).all()

    open_by_severity = [
        {"severity": level, "findings": count, "exceptions": exceptions}
        for level, count, exceptions in sorted(
            open_rows,
            key=lambda r: SEVERITY_ORDER.index(r[0])
            if r[0] in SEVERITY_ORDER
            else len(SEVERITY_ORDER),
        )
    ]

    open_by_control = dict(
        (
            await session.execute(
                scoped(
                    select(Ticket.rule_code, func.count())
                    .where(Ticket.status == "open")
                    .group_by(Ticket.rule_code),
                    Ticket,
                    entity,
                )
            )
        ).all()
    )

    oldest_at = (
        await session.execute(
            scoped(
                select(func.min(Ticket.created_at)).where(Ticket.status == "open"),
                Ticket,
                entity,
            )
        )
    ).scalar_one()

    # --- the longest-waiting problems, one row each ---------------------------
    # A control that breaches for a week opens a fresh ticket every day, and a
    # queue of eight near-identical rows tells a head of audit nothing they did
    # not already know. So this groups the open tickets by what they actually
    # say, and reports how many of them there are. The row links to the OLDEST
    # instance, because that is the one that has waited longest.
    longest = (
        await session.execute(
            scoped(
                select(
                    func.min(Ticket.id),
                    Ticket.rule_code,
                    Ticket.severity,
                    Run.summary,
                    func.count(),
                    func.min(Ticket.created_at),
                    func.max(Ticket.created_at),
                    func.coalesce(func.sum(func.jsonb_array_length(Ticket.rows)), 0),
                )
                .join(Run, Ticket.run_id == Run.id)
                .where(Ticket.status == "open")
                .group_by(Ticket.rule_code, Ticket.severity, Run.summary)
                .order_by(func.min(Ticket.created_at))
                .limit(TOP_FINDINGS),
                Ticket,
                entity,
            )
        )
    ).all()

    # --- movement over the window ---------------------------------------------
    async def raised(days_in: list[date]) -> tuple[int, int]:
        """Findings and exception rows raised on the given data dates.

        Only breaching runs carry rows, so filtering to them here makes the sum
        exact rather than relying on passing runs contributing zero.
        """
        if not days_in:
            return (0, 0)
        found = (
            await session.execute(
                scoped(
                    select(
                        func.count(),
                        func.coalesce(func.sum(func.jsonb_array_length(Run.rows)), 0),
                    )
                    .where(Run.data_date.in_(days_in), Run.outcome == "fail"),
                    Run,
                    entity,
                )
            )
        ).one()
        return (found[0] or 0, int(found[1] or 0))

    raised_findings, raised_exceptions = await raised(dates)
    prev_findings, prev_exceptions = await raised(previous)

    throughput = (
        await session.execute(
            scoped(
                select(
                    func.count(),
                    func.avg(
                        func.extract("epoch", Ticket.acknowledged_at - Ticket.created_at)
                        / 86400.0
                    ),
                )
                .select_from(Ticket)
                .join(Run, Ticket.run_id == Run.id)
                .where(Ticket.acknowledged_at.isnot(None), Run.data_date.in_(dates)),
                Ticket,
                entity,
            )
        )
    ).one()

    # --- ranking ---------------------------------------------------------------
    rows = []
    for code, meta in assured.items():
        tallied = rollup.get(code, {})
        missing = [d.isoformat() for d in dates if code not in state_by_day.get(d, {})]
        last_breach = tallied.get("last_breach")
        rows.append(
            {
                **meta,
                # "On demand" rather than a cadence: a control nobody scheduled
                # has no duty to report daily, so its silence is a design
                # decision. A control can hold several cadences at once, and
                # naming just one of them would understate how often it is
                # meant to run - so a control on three schedules reads as three.
                "cadence": (
                    "On demand" if not cadences.get(code)
                    else f"{len(cadences[code])} cadences"
                    if len(cadences[code]) > 1
                    else cadences[code][0]
                ),
                "scheduled": code in cadences,
                "breached_days": tallied.get("breached_days", 0),
                "errored_days": tallied.get("errored_days", 0),
                "reported_days": tallied.get("days", 0),
                "exceptions": tallied.get("exceptions", 0),
                "open_findings": int(open_by_control.get(code, 0)),
                "last_breach": last_breach.isoformat() if last_breach else None,
                "missing_days": len(missing),
                "missing_dates": missing[:5],
            }
        )

    ranked = rank_controls(rows)
    # Silence is only a gap for a control that was due to report. An on-demand
    # control that did not run is simply not covering that day, which the head of
    # audit still wants to see - but it is a coverage note, not a missed deadline.
    gaps = [r for r in ranked if r["missing_days"]]
    overdue = [r for r in gaps if r["scheduled"]]
    as_at = dates[-1] if dates else None

    return {
        "entity": entity.upper() if entity else None,
        "requested_by": user.username,
        "as_at": as_at.isoformat() if as_at else None,
        # How stale the newest opinion is. A blackout - the ETL delivered and
        # nothing ran - shows up here rather than as a quietly shorter grid.
        "as_at_age_days": age_in_days(
            datetime.combine(as_at, time.min).replace(tzinfo=timezone.utc)
            if as_at
            else None,
            now,
        ),
        "window_days": len(dates),
        "controls": {
            "total": len(controls),
            "assured": len(assured),
            "not_assured": pending[:TOP_CONTROLS],
            "not_assured_count": len(pending),
        },
        "days": days,
        "offenders": ranked[:TOP_CONTROLS],
        "gaps": gaps[:TOP_CONTROLS],
        "overdue": overdue[:TOP_CONTROLS],
        "open": {
            "findings": sum(r["findings"] for r in open_by_severity),
            "exceptions": sum(r["exceptions"] for r in open_by_severity),
            "oldest_days": age_in_days(oldest_at, now),
            "by_severity": open_by_severity,
        },
        "longest_open": [
            {
                "id": ticket_id,
                "rule_code": rule_code,
                "severity": severity,
                "summary": summary,
                "count": count,
                "exceptions": exceptions,
                "oldest_at": oldest_at.isoformat(),
                "newest_at": newest_at.isoformat(),
                "age_days": age_in_days(oldest_at, now),
            }
            for (
                ticket_id,
                rule_code,
                severity,
                summary,
                count,
                oldest_at,
                newest_at,
                exceptions,
            ) in longest
        ],
        "movement": {
            "raised_findings": raised_findings,
            "raised_exceptions": raised_exceptions,
            "previous_raised_findings": prev_findings,
            "previous_raised_exceptions": prev_exceptions,
            "acknowledged_findings": int(throughput[0] or 0),
            "ack_days": round(float(throughput[1]), 1) if throughput[1] else None,
        },
    }