"""ABM Audit Portal - FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, datasources, params, rules, runs, schedules, tickets
from app.api.deps import SessionDep
from app.db import SessionLocal, init_models, table_names
from app.engine import scheduler
from app.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
)
log = logging.getLogger("abm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting ABM Audit Portal API")
    await init_models()
    await _bootstrap()
    scheduler.start()
    await scheduler.load_all()
    yield
    scheduler.shutdown()
    log.info("API stopped")


async def _bootstrap() -> None:
    """Seed the two bootstrap users and any missing baseline parameters."""
    from sqlalchemy import select

    from app.models import Param, User
    from app.security import ROLE_ADMIN, ROLE_AUDITOR, hash_password

    async with SessionLocal() as session:
        created: list[str] = []

        async def ensure_user(username: str, password: str, role: str, full_name: str):
            if not password:
                log.warning("%s has no password configured - not seeded", username)
                return
            found = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if found:
                return
            session.add(
                User(
                    username=username,
                    full_name=full_name,
                    password_hash=hash_password(password),
                    role=role,
                )
            )
            created.append(f"{username} ({role})")

        await ensure_user(
            settings.admin_username, settings.admin_password, ROLE_ADMIN,
            "Audit Administrator",
        )
        await ensure_user(
            settings.auditor_username, settings.auditor_password, ROLE_AUDITOR,
            "Audit Reviewer",
        )

        if not settings.jwt_secret:
            log.warning(
                "JWT_SECRET not set - using an ephemeral dev secret; "
                "tokens will not survive a restart"
            )

        defaults = [
            {
                "key": "lad.last_hour_ratio",
                "label": "LAD-CA-03 last-hour ratio limit",
                "value_numeric": 2.0,
                "description": "Maximum multiple of the hourly average value allowed "
                               "in the final hour of Laduree store trading.",
            },
            {
                "key": "lad.single_staff_cash_share",
                "label": "LAD-CA-04 single-staff cash share limit (%)",
                "value_numeric": 50.0,
                "description": "Maximum share of a day's cash-tendered invoices that "
                               "one staff member may settle at Laduree.",
            },
            {
                "key": "lad.ex_staff_min_days_after_exit",
                "label": "LAD-CA-06 days after exit before flagging POS activity",
                "value_numeric": 0.0,
                "description": "Grace period after an employee exits before POS "
                               "activity is treated as a control breach.",
            },
            {
                "key": "abm.bank_match_min_similarity",
                "label": "ABM-CA-22 vendor/employee bank match threshold (%)",
                "value_numeric": 92.0,
                "description": "Similarity threshold for matching vendor bank details "
                               "in Business Central against HRMS employee records.",
            },
            {
                "key": "template.some_threshold",
                "label": "Template rule threshold",
                "value_numeric": 10.0,
                "description": "Placeholder used by rules/_template.py only.",
            },
        ]
        for d in defaults:
            found = (
                await session.execute(select(Param).where(Param.key == d["key"]))
            ).scalar_one_or_none()
            if not found:
                session.add(Param(**d, updated_by="system"))

        await session.commit()
        if created:
            log.info("seeded users: %s", ", ".join(created))


app = FastAPI(
    title="ABM Audit Portal API",
    description=(
        "Continuous auditing platform for Al Boom Marine / FSCS.\n\n"
        "Rules are plain Python files; each returns "
        "`no_rows_returned` or `rows_returned`. A failing run raises a ticket "
        "that an auditor acknowledges."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(rules.router)
app.include_router(schedules.router)
app.include_router(runs.router)
app.include_router(tickets.router)
app.include_router(params.router)
app.include_router(datasources.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    from sqlalchemy import text

    from app.db import engine

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        log.error("health check failed: %s", exc)
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "tables": await table_names() if db_ok else [],
        "scheduler_running": scheduler.scheduler.running,
        "scheduled_jobs": len(scheduler.status()),
        "sourcedata_dir": str(settings.sourcedata_dir),
        "rules_dir": str(settings.rules_dir),
        "llm_configured": bool(settings.llm_base_url and settings.llm_model),
    }


@app.get("/api/dashboard", tags=["meta"])
async def dashboard(session: SessionDep) -> dict:
    """One aggregated payload for the landing screen."""
    from sqlalchemy import func, select

    from app.models import Rule, RuleSchedule, Run, Ticket

    async def count(model, *where) -> int:
        stmt = select(func.count()).select_from(model)
        for clause in where:
            stmt = stmt.where(clause)
        return (await session.execute(stmt)).scalar_one()

    active_rules = await count(Rule, Rule.status == "active")
    total_rules = await count(Rule)
    total_schedules = await count(RuleSchedule)

    return {
        "rules": {
            "total": total_rules,
            "active": active_rules,
            "draft": await count(Rule, Rule.status == "draft"),
            "paused": await count(Rule, Rule.status == "paused"),
            "validated": await count(Rule, Rule.validation_status == "valid"),
        },
        "schedules": {
            "total": total_schedules,
            # A schedule runs iff its rule is active, so this is the count that
            # actually matters operationally.
            "live": await count(
                RuleSchedule,
                RuleSchedule.rule_id.in_(select(Rule.id).where(Rule.status == "active")),
            ),
        },
        "runs": {
            "total": await count(Run),
            "pass": await count(Run, Run.status == "pass"),
            "fail": await count(Run, Run.status == "fail"),
            "error": await count(Run, Run.status == "error"),
        },
        "tickets": {
            "total": await count(Ticket),
            "open": await count(Ticket, Ticket.status == "open"),
            "acknowledged": await count(Ticket, Ticket.status == "acknowledged"),
            "dismissed": await count(Ticket, Ticket.status == "dismissed"),
        },
        "scheduler": {"running": scheduler.scheduler.running, "jobs": len(scheduler.status())},
    }