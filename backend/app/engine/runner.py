"""
Rule runner - the entire execution engine.

    fire (cron or manual)
      -> load rule .py
      -> build DuckDB views for the run's date
      -> call run(ctx)
      -> validate the returned dict against RuleResult
      -> insert a runs row
      -> if rows_returned, insert a ticket

There is deliberately no more logic than that.
"""

from __future__ import annotations

import time
import traceback
# `time` is aliased because a bare `from datetime import time` would shadow the
# stdlib `time` module and break time.perf_counter() below.
from datetime import date, datetime, time as dt_time, timezone
from decimal import Decimal
from typing import Any

import duckdb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.ctx import RuleContext, build_views
from app.engine.datasources import registry_for
from app.engine.loader import load_rule
from app.models import Param, Rule, Run, Schedule, Ticket, Watermark
from app.schemas import RuleResult
from app.settings import settings


async def load_params(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(Param))).scalars().all()
    out: dict[str, Any] = {}
    for p in rows:
        out[p.key] = p.value_numeric if p.value_numeric is not None else p.value_text
    return out


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
    shadow: bool = False,
) -> Run:
    """Run one rule and persist the run (+ ticket when the rule fails)."""
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)

    run = Run(
        rule_id=rule.id,
        rule_version=rule.current_version,
        schedule_id=schedule_id,
        data_date=data_date,
        triggered_by=triggered_by,
        status="running",
        is_shadow=shadow,
    )
    session.add(run)
    await session.flush()

    params = await load_params(session)
    counts: dict[str, int] = {}
    result: RuleResult | None = None

    try:
        module = load_rule(rule.source_file, settings.rules_dir)
        fn = module.run

        def work() -> tuple[dict[str, Any], dict[str, int], list[str]]:
            # A private in-memory DuckDB per execution: no state bleed between
            # concurrent rules, and nothing survives the run.
            con = duckdb.connect(":memory:")
            try:
                c = build_views(
                    con,
                    sourcedata_dir=settings.sourcedata_dir,
                    entity=rule.entity,
                    data_date=data_date,
                    query_mode=rule.query_mode,
                    window_days=rule.window_days,
                )
                ctx = RuleContext(
                    con=con,
                    data_date=data_date,
                    params=params,
                    query_mode=rule.query_mode,
                    rule_name=rule.name,
                    registered=list(registry_for(rule.entity).keys()),
                )
                raw = fn(ctx)
                return raw, c, ctx.logs
            finally:
                con.close()

        raw, counts, logs = await _run_in_thread(work)
        result = RuleResult.model_validate(raw)

        # Guard against a rule returning a runaway number of rows.
        if len(result.rows) > settings.max_rows_in_ticket:
            result.rows = result.rows[: settings.max_rows_in_ticket]
            result.summary += (
                f" (truncated to {settings.max_rows_in_ticket} rows)"
            )

        run.rows_scanned = sum(counts.values())
        run.summary = result.summary or _default_summary(result)
        run.metrics = json_safe(result.metrics)
        run.rows = json_safe(result.rows)

        if result.status == "rows_returned":
            run.status = "fail"
        elif result.status == "no_rows_returned":
            run.status = "pass"
        else:
            run.status = "error"
            run.error_detail = result.error or "Rule reported status=error"

        if logs:
            run.summary = (run.summary + " | logs: " + "; ".join(logs)).strip()

    except Exception as exc:  # noqa: BLE001 - a bad rule must not kill the process
        run.status = "error"
        run.error_detail = f"{type(exc).__name__}: {exc}"
        run.summary = f"Rule {rule.code} raised {type(exc).__name__}"
        run.rows_scanned = sum(counts.values())
        run.metrics = {"traceback": traceback.format_exc()[-4000:]}

    run.finished_at = datetime.now(timezone.utc)
    run.duration_ms = int((time.perf_counter() - started) * 1000)

    # A failing rule raises a ticket, unless this was a shadow run.
    if run.status == "fail" and not shadow:
        ticket = Ticket(
            ticket_no=_pending_ticket_no(session),
            run_id=run.id,
            rule_id=rule.id,
            schedule_id=schedule_id,
            severity=rule.severity,
            summary=run.summary,
            rows=run.rows,
            status="open",
        )
        session.add(ticket)
        await session.flush()
        ticket.ticket_no = f"ABM-TKT-{ticket.id:05d}"

    if schedule_id:
        sched = await session.get(Schedule, schedule_id)
        if sched:
            sched.last_run_at = run.finished_at
            await _advance_watermark(session, rule, run)

    await session.commit()
    await session.refresh(run)
    return run


def _default_summary(result: RuleResult) -> str:
    if result.status == "no_rows_returned":
        return "Rule passed - no exception rows"
    return f"{len(result.rows)} exception row(s)"


def _pending_ticket_no(session: AsyncSession) -> str:
    """Placeholder; overwritten with the real number once the row has an id."""
    return "ABM-TKT-PENDING"


async def _run_in_thread(fn):
    import anyio

    return await anyio.to_thread.run_sync(fn)


async def _advance_watermark(session: AsyncSession, rule: Rule, run: Run) -> None:
    """Record the high-water mark for DELTA-mode rules."""
    if rule.query_mode != "DELTA" or run.status != "pass":
        return
    wm = await session.execute(
        select(Watermark).where(
            Watermark.rule_id == rule.id, Watermark.source_key == "last_run"
        )
    )
    row = wm.scalar_one_or_none()
    stamp = run.finished_at.isoformat() if run.finished_at else ""
    if row:
        row.last_value = stamp
    else:
        session.add(
            Watermark(rule_id=rule.id, source_key="last_run", last_value=stamp)
        )