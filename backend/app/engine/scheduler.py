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

            rule = sched.rule
            # Single source of truth for "should this run".
            if rule is None or rule.status != "active":
                log.debug("schedule %s skipped: rule status is %s",
                          schedule_id, getattr(rule, "status", "missing"))
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
                "schedule %s (rule %s) -> run %s status=%s rows=%s",
                schedule_id, rule.code, run.id, run.status, len(run.rows or []),
            )
    except Exception:  # noqa: BLE001
        log.exception("schedule %s failed", schedule_id)


async def sync_schedule(schedule: RuleSchedule) -> str | None:
    """Add or replace the live job for one schedule row.

    Returns the derived cron, or None if the schedule was not registered
    because its rule is not active.

    NOTE: `schedule.rule` must already be loaded. Callers load it with
    selectinload; touching an unloaded relationship here would raise
    MissingGreenlet under async SQLAlchemy.
    """
    jid = job_id(schedule.id)
    try:
        scheduler.remove_job(jid)
    except JobLookupError:
        pass

    rule = schedule.rule
    if rule is None or rule.status != "active":
        log.debug("schedule %s not registered: rule is not active", schedule.id)
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


async def load_all() -> int:
    """Rehydrate every schedule belonging to an active rule.

    `selectinload` is mandatory, not an optimisation: touching `sched.rule`
    later would otherwise trigger a lazy load, which SQLAlchemy cannot perform
    in an async context (MissingGreenlet).
    """
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(RuleSchedule)
                .options(selectinload(RuleSchedule.rule))
                .join(RuleSchedule.rule)
                .where(Rule.status == "active")
            )
        ).scalars().unique().all()

        loaded, skipped = 0, 0
        for sched in rows:
            try:
                if await sync_schedule(sched) is not None:
                    loaded += 1
            except CadenceError:
                skipped += 1
        await session.commit()

    log.info("scheduler loaded %s job(s), skipped %s invalid", loaded, skipped)
    return loaded


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