"""
Per-rule scheduling.

A rule has many RuleSchedule rows; each becomes one APScheduler job keyed by
`rule_schedule_<id>`. The friendly `kind`/`config` pair is converted to cron by
app.engine.frequency, so this module never sees a user-typed expression.

Two rules that were wrong before and are deliberate now:

1. There is no separate "enabled" switch. `rules.status` decides whether a job
   runs; a schedule row only says *when*. Two switches that could disagree
   were the original defect.
2. `next_run_at` is stored as a proper timezone-aware timestamp. The previous
   code did `.replace(tzinfo=None)`, which silently dropped the offset and made
   a 06:30 Dubai job look like 06:30 UTC.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.engine.frequency import Cadence, CadenceError, to_cron
from app.models import Rule, RuleSchedule
from app.settings import settings

log = logging.getLogger("abm.scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")

DEFAULT_TIMEZONE = "UTC"


def job_id(schedule_id: int) -> str:
    return f"rule_schedule_{schedule_id}"


def cadence_of(schedule: RuleSchedule) -> Cadence:
    return Cadence.from_dict(schedule.config)


def cron_for(schedule: RuleSchedule) -> str:
    """Derive the cron for a schedule row, raising CadenceError if invalid."""
    return to_cron(cadence_of(schedule))


async def head_of(session, code: str) -> Rule | None:
    """Resolve a rule code to its live version row.

    A schedule references `rule_code`, not a row id, so publishing a new version
    can never orphan it. Resolution happens at fire time, which also means a
    version published between the schedule firing and execution is the one that
    runs.
    """
    return (
        await session.execute(
            select(Rule).where(Rule.code == code, Rule.is_version_active.is_(True))
        )
    ).scalar_one_or_none()


def default_data_date(lag_days: int = 1) -> date:
    """The day folder a schedule should audit.

    A day's CSVs are usually still landing when a 06:30 job fires, so lag=1
    (yesterday) is the sane default. Clamped to the newest folder that exists
    so a missing day does not silently audit a week-old date.
    """
    base = settings.sourcedata_dir
    target = datetime.now(timezone.utc).date() - timedelta(days=max(0, lag_days))

    folders: list[date] = []
    if base.exists():
        for entity_dir in base.iterdir():
            if not entity_dir.is_dir():
                continue
            for folder in entity_dir.iterdir():
                if not folder.is_dir():
                    continue
                try:
                    folders.append(date.fromisoformat(folder.name))
                except ValueError:
                    continue
    if folders:
        newest = max(folders)
        return min(target, newest)
    return target


async def run_schedule_job(schedule_id: int) -> None:
    """APScheduler entry point. Never raises - a failing schedule must not
    kill the scheduler process."""
    from app.engine.runner import execute_rule

    try:
        async with SessionLocal() as session:
            sched = await session.get(RuleSchedule, schedule_id)
            if sched is None:
                return

            # Resolve the code to its live version at fire time.
            rule = await head_of(session, sched.rule_code)
            if rule is None:
                log.warning(
                    "schedule %s skipped: no rule matches code %s",
                    schedule_id, sched.rule_code,
                )
                return
            if rule.status != "active":
                log.debug("schedule %s skipped: rule %s is %s",
                          schedule_id, rule.code, rule.status)
                return

            data_date = default_data_date(sched.data_lag_days)
            run = await execute_rule(
                session,
                rule=rule,
                data_date=data_date,
                schedule_id=schedule_id,
                triggered_by="schedule",
            )
            sched.last_run_at = run.finished_at or datetime.now(timezone.utc)
            await session.commit()
            log.info(
                "schedule %s (rule %s) -> run %s status=%s outcome=%s rows=%s",
                schedule_id, rule.code, run.id, run.status, run.outcome,
                len(run.rows or []),
            )
    except Exception:  # noqa: BLE001
        log.exception("schedule %s failed", schedule_id)


async def sync_schedule(schedule: RuleSchedule) -> str | None:
    """Add or replace the live job for one schedule row.

    Returns the derived cron, or None if the schedule was not registered
    because its rule has no live, active version.
    """
    jid = job_id(schedule.id)
    try:
        scheduler.remove_job(jid)
    except JobLookupError:
        pass

    async with SessionLocal() as session:
        rule = await head_of(session, schedule.rule_code)

    if rule is None:
        log.debug("schedule %s not registered: no rule for code %s",
                  schedule.id, schedule.rule_code)
        return None
    if rule.status != "active":
        log.debug("schedule %s not registered: rule %s is %s",
                  schedule.id, rule.code, rule.status)
        return None

    try:
        expr = cron_for(schedule)
    except CadenceError as exc:
        # A schedule that cannot be expressed must never be registered -
        # otherwise APScheduler accepts something that silently never fires.
        log.error("schedule %s has invalid cadence: %s", schedule.id, exc)
        raise

    trigger = CronTrigger.from_crontab(expr, timezone=schedule.timezone or DEFAULT_TIMEZONE)
    job = scheduler.add_job(
        run_schedule_job,
        trigger=trigger,
        id=jid,
        args=[schedule.id],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Store the real next fire time, timezone-aware.
    if job.next_run_time:
        schedule.next_run_at = job.next_run_time
    return expr


async def remove_schedule(schedule_id: int) -> None:
    try:
        scheduler.remove_job(job_id(schedule_id))
    except JobLookupError:
        pass


async def load_all() -> dict:
    """Rehydrate every schedule, resolving rule_code -> live version.

    Returns a summary. `unresolved` lists schedules whose rule_code matched no
    live version. Since `code` is write-once and validated, this should stay
    empty - but reporting it at startup means a mismatch is visible rather than
    being a schedule that silently never fires.
    """
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(RuleSchedule).order_by(RuleSchedule.id))
        ).scalars().all()

        loaded = 0
        invalid = 0
        unresolved: list[dict] = []

        for sched in rows:
            try:
                registered = await sync_schedule(sched)
            except CadenceError:
                invalid += 1
                continue

            if registered is None:
                rule = await head_of(session, sched.rule_code)
                if rule is None:
                    unresolved.append(
                        {"schedule_id": sched.id, "rule_code": sched.rule_code}
                    )
                continue
            loaded += 1

        await session.commit()

    log.info(
        "scheduler loaded %s job(s); %s unresolved, %s invalid",
        loaded, len(unresolved), invalid,
    )
    for item in unresolved:
        log.warning(
            "  schedule %s references rule_code %s, which has no live version",
            item["schedule_id"], item["rule_code"],
        )
    return {"loaded": loaded, "invalid": invalid, "unresolved": unresolved}


def start() -> None:
    if not scheduler.running:
        scheduler.start()
        log.info("scheduler started")


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def status() -> list[dict]:
    return [
        {
            "id": job.id,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        }
        for job in scheduler.get_jobs()
    ]