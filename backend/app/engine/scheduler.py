"""
Per-rule scheduling.

One APScheduler job per enabled Schedule row, keyed by schedule id. Creating,
editing or disabling a schedule updates the live job immediately, so there is
no "restart to apply changes" step.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Schedule
from app.settings import settings

log = logging.getLogger("abm.scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")


def job_id(schedule_id: int) -> str:
    return f"schedule_{schedule_id}"


def validate_cron(expr: str) -> bool:
    """Raise ValueError if the 5-field expression is not valid."""
    CronTrigger.from_crontab(expr)
    return True


async def run_schedule_job(schedule_id: int) -> None:
    """Entry point APScheduler calls. Failures are logged, never raised."""
    from app.engine.runner import execute_rule

    async with SessionLocal() as session:
        sched = await session.get(Schedule, schedule_id)
        if sched is None or not sched.enabled:
            return
        rule = sched.rule
        if rule is None or rule.status != "active":
            log.info("schedule %s skipped: rule not active", schedule_id)
            return

        # Target the most recent day folder that actually holds data - today's
        # CSVs are rarely complete at the time a schedule fires.
        data_date = _default_data_date()

        try:
            run = await execute_rule(
                session,
                rule=rule,
                data_date=data_date,
                schedule_id=schedule_id,
                triggered_by="schedule",
            )
            log.info(
                "schedule %s -> run %s status=%s", schedule_id, run.id, run.status
            )
        except Exception:  # noqa: BLE001
            log.exception("schedule %s failed", schedule_id)


def _default_data_date() -> date:
    """Most recent day folder that actually contains data."""
    base = settings.sourcedata_dir
    if base.exists():
        folders = sorted(
            p.name for p in base.iterdir() if p.is_dir() and _is_iso_date(p.name)
        )
        if folders:
            return date.fromisoformat(folders[-1])
    return date.today()


def _is_iso_date(name: str) -> bool:
    try:
        date.fromisoformat(name)
    except ValueError:
        return False
    return True


async def sync_schedule(schedule: Schedule) -> None:
    """Add or replace the live APScheduler job for one schedule row."""
    jid = job_id(schedule.id)
    try:
        scheduler.remove_job(jid)
    except JobLookupError:
        pass

    if not schedule.enabled:
        return

    trigger = CronTrigger.from_crontab(schedule.cron, timezone=schedule.timezone or "UTC")
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
    if job.next_run_time:
        schedule.next_run_at = job.next_run_time.replace(tzinfo=None)


async def remove_schedule(schedule_id: int) -> None:
    try:
        scheduler.remove_job(job_id(schedule_id))
    except JobLookupError:
        pass


async def load_all() -> None:
    """Rehydrate every enabled schedule on startup."""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Schedule).where(Schedule.enabled.is_(True))
            )
        ).scalars().all()
        for sched in rows:
            if sched.rule is None or sched.rule.status != "active":
                continue
            await sync_schedule(sched)
        log.info("scheduler loaded %s job(s)", len(rows))


def start() -> None:
    if not scheduler.running:
        scheduler.start()
        log.info("scheduler started")


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def status() -> list[dict]:
    out = []
    for job in scheduler.get_jobs():
        out.append(
            {
                "id": job.id,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
        )
    return out