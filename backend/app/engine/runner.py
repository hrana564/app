"""
Rule runner - the entire execution engine.

    fire (cron or manual)
      -> insert a rule_runs row
      -> stage the inputs into mount/Rules_Executions/<run_id>/
      -> load rule .py
      -> call run(ctx)
      -> validate the returned dict against RuleResult
      -> write result.json / error.txt into the run folder
      -> if the rule returned rows, insert a ticket

There is deliberately no more logic than that.

THE RUN FOLDER IS THE ONLY READ PATH
------------------------------------
The rule reads the staged CSVs, never the mount. That is what makes the folder
worth keeping: replaying it runs against the same bytes the original run read,
so a failure you can reproduce is a failure you can fix.
"""

from __future__ import annotations

import time
import traceback
# `time` is aliased because a bare `from datetime import time` would shadow the
# stdlib `time` module and break time.perf_counter() below.
import logging
from datetime import date, datetime, time as dt_time, timezone
from decimal import Decimal
from typing import Any

log = logging.getLogger("abm.runner")

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.ctx import invoke_rule
from app.engine.catalog import database_catalog
from app.engine.loader import load_rule
from app.engine.snapshot import capture
from app.engine.ticketing import raise_ticket, run_outcome_for, run_status_for
from app.models import Rule, Run
from app.schemas import RuleResult
from app.settings import settings


def json_safe(value: Any) -> Any:
    """
    Coerce a value into something the JSONB columns accept.

    A rule that puts a datetime or a Decimal into `rows` or `metrics` would
    otherwise fail at INSERT time with an opaque TypeError.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dt_time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


async def execute_rule(
    session: AsyncSession,
    *,
    rule: Rule,
    data_date: date,
    schedule_id: int | None = None,
    triggered_by: str = "schedule",
) -> Run:
    """Run one rule and persist the run (+ ticket when the rule fails)."""
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)

    run = Run(
        rule_id=rule.id,
        rule_code=rule.code,
        rule_version=rule.version,
        schedule_id=schedule_id,
        data_date=data_date,
        triggered_by=triggered_by,
        status="running",
    )
    session.add(run)
    await session.flush()

    result: RuleResult | None = None

    # Freeze the inputs before the rule gets a chance to touch anything. A
    # snapshot that cannot be staged is an error, not an empty result: a rule
    # handed nothing reports "no breach", and an audit that claims a clean day
    # it never checked is worse than one that failed loudly.
    try:
        snap = capture(
            run.id,
            entity=rule.entity,
            sources=list(rule.declared_datasources or []),
            anchor=data_date,
            lookback_days=rule.lookback_days,
            source_file=rule.source_file,
            rule_code=rule.code,
            rule_version=rule.version,
        )
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.outcome = None
        run.error_detail = f"snapshot failed: {type(exc).__name__}: {exc}"
        run.summary = f"Could not stage inputs for {rule.code}"
        run.metrics = {"traceback": traceback.format_exc()[-4000:]}
        run.finished_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        await session.commit()
        await session.refresh(run)
        return run

    problem = snap.blocking_problem()
    if problem:
        run.status = "failed"
        run.outcome = None
        run.error_detail = f"missing input: {problem}"
        run.summary = f"{rule.code} could not run - source data incomplete"
        run.metrics = {
            "missing_days": snap.missing_days,
            "missing_files": snap.missing_files,
        }
        snap.write_error(run.error_detail)
        run.finished_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        await session.commit()
        await session.refresh(run)
        return run

    try:
        module = load_rule(rule.source_file, settings.rules_dir)

        # What this entity may read, read once per run and closed over. The
        # rule thread below never touches the database - it only gets names.
        allowed = sorted((await database_catalog(session)).names_for(rule.entity))

        def work() -> tuple[dict[str, Any], list[str]]:
            return invoke_rule(
                module,
                data_date=data_date,
                lookback_days=rule.lookback_days,
                registered=allowed,
                snapshot=snap,
            )

        raw, logs = await _run_in_thread(work)
        result = RuleResult.model_validate(raw)

        # Guard against a rule returning a runaway number of rows.
        if len(result.rows) > settings.max_rows_in_ticket:
            result.rows = result.rows[: settings.max_rows_in_ticket]
            result.summary += (
                f" (truncated to {settings.max_rows_in_ticket} rows)"
            )

        run.summary = result.summary or _default_summary(result)
        run.metrics = json_safe(result.metrics)
        run.rows = json_safe(result.rows)

        # Two independent questions, answered independently: did the rule run,
        # and what did it find. Both derived in one place and shared with replay.
        run.status = run_status_for(result.status)
        run.outcome = run_outcome_for(result.status, len(result.rows))
        if result.status != "success":
            run.error_detail = result.error_message or "Rule reported status=failed"

        if logs:
            run.summary = (run.summary + " | logs: " + "; ".join(logs)).strip()

    except Exception as exc:  # noqa: BLE001 - a bad rule must not kill the process
        run.status = "failed"
        run.outcome = None
        run.error_detail = f"{type(exc).__name__}: {exc}"
        run.summary = f"Rule {rule.code} raised {type(exc).__name__}"
        run.metrics = {"traceback": traceback.format_exc()[-4000:]}
        snap.write_error(run.error_detail, run.metrics["traceback"])

    run.finished_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.perf_counter() - started) * 1000)

    # The run folder carries its own copy of the outcome so it stays readable
    # with the database offline. The authoritative record is still the run row.
    snap.write_result(
        {
            "run_id": run.id,
            "rule_code": run.rule_code,
            "data_date": str(run.data_date),
            "status": run.status,
            "outcome": run.outcome,
            "summary": run.summary,
            "metrics": run.metrics,
            "rows": run.rows,
            "error_detail": run.error_detail,
            "duration_ms": run.duration_ms,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    )

    # A run that completed and found a breach opens exactly one ticket.
    # Ticketing reads the run and nothing else - see app/engine/ticketing.py.
    if run.outcome == "fail":
        try:
            await raise_ticket(
                session, run=run, severity=rule.severity, schedule_id=schedule_id
            )
        except Exception:  # noqa: BLE001
            # Never lose the run record because ticketing failed.
            log.exception("failed to raise ticket for rule %s", rule.code)

    await session.commit()
    await session.refresh(run)
    return run


def _default_summary(result: RuleResult) -> str:
    if not result.rows:
        return "Rule passed - no exception rows"
    return f"{len(result.rows)} exception row(s)"


async def _run_in_thread(fn):
    import anyio

    return await anyio.to_thread.run_sync(fn)