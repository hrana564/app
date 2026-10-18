"""
Rule runner - the entire execution engine.

    fire (cron or manual)
      -> load rule .py
      -> call run(ctx)
      -> validate the returned dict against RuleResult
      -> insert a rule_runs row
      -> if the rule returned rows, insert a ticket

There is deliberately no more logic than that.
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.ctx import RuleContext
from app.engine.datasources import registry_for
from app.engine.loader import load_rule
from app.engine.ticketing import raise_ticket
from app.models import Rule, Run, Ticket
from app.schemas import RuleResult
from app.settings import settings


def json_safe(value: Any) -> Any:
    """
    Coerce a value into something the JSONB columns accept.

    DuckDB hands back datetime / date / Decimal / UUID objects, which are not
    JSON serialisable. Any rule that selects a timestamp column would otherwise
    fail at INSERT time with an opaque TypeError.
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
        schedule_id=schedule_id,
        data_date=data_date,
        triggered_by=triggered_by,
        status="running",
    )
    session.add(run)
    await session.flush()

    result: RuleResult | None = None

    try:
        module = load_rule(rule.source_file, settings.rules_dir)
        fn = module.run

        def work() -> tuple[dict[str, Any], list[str]]:
            ctx = RuleContext(
                data_date=data_date,
                lookback_days=rule.lookback_days,
                rule_name=rule.name,
                registered=list(registry_for(rule.entity).keys()),
            )
            return fn(ctx), ctx.logs

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

        if result.status == "success":
            run.status = "fail" if result.rows else "pass"
        else:
            run.status = "error"
            run.error_detail = result.error_message or "Rule reported status=failed"

        if logs:
            run.summary = (run.summary + " | logs: " + "; ".join(logs)).strip()

    except Exception as exc:  # noqa: BLE001 - a bad rule must not kill the process
        run.status = "error"
        run.error_detail = f"{type(exc).__name__}: {exc}"
        run.summary = f"Rule {rule.code} raised {type(exc).__name__}"
        run.metrics = {"traceback": traceback.format_exc()[-4000:]}

    run.finished_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.perf_counter() - started) * 1000)

    # A failing run with rows opens exactly one ticket. Ticketing reads the run
    # and nothing else - see app/engine/ticketing.py.
    if run.status == "fail":
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