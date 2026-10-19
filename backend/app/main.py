"""ABM Audit Portal - FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, datasources, rules, runs, schedules, tickets
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
    summary = await scheduler.load_all()
    if summary.get("unresolved"):
        # Visible on purpose: a schedule pointing at an unknown code would
        # otherwise just never fire.
        log.warning(
            "scheduler: %s schedule(s) reference an unknown rule code: %s",
            len(summary["unresolved"]),
            [u["rule_code"] for u in summary["unresolved"]],
        )
    yield
    scheduler.shutdown()
    log.info("API stopped")


async def _bootstrap() -> None:
    """Seed the two bootstrap users and any missing baseline parameters."""
    from sqlalchemy import select

    from app.models import User
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
async def dashboard(session: SessionDep, entity: str | None = None) -> dict:
    """One aggregated payload for the landing screen.

    Scoped to one client, like every other screen. A landing page that counts
    both clients while the lists behind it show one is worse than no numbers: the
    first click out of it disagrees with it.
    """
    from sqlalchemy import and_, func, select

    from app.api.scope import scoped
    from app.models import Rule, RuleSchedule, Run, Ticket

    async def count(model, *where) -> int:
        stmt = select(func.count()).select_from(model)
        for clause in where:
            stmt = stmt.where(clause)
        return (await session.execute(scoped(stmt, model, entity))).scalar_one()

    head = Rule.is_version_active.is_(True)

    # A schedule is a rule's cadence and is keyed by the rule's code, so scoping
    # schedules to a client means scoping them to the codes that client's rules
    # hold. `scoped_head` is that rule-scope, folded into one clause.
    scoped_head = head if not entity else and_(head, Rule.entity == entity.upper())

    async def schedule_count(codes_of) -> int:
        stmt = select(func.count()).select_from(RuleSchedule).where(
            RuleSchedule.rule_code.in_(codes_of)
        )
        return (await session.execute(stmt)).scalar_one()

    return {
        "entity": entity.upper() if entity else None,
        "rules": {
            # Only head versions count as rules; earlier versions are history.
            "total": await count(Rule, head),
            "active": await count(Rule, head, Rule.status == "active"),
            "draft": await count(Rule, head, Rule.status == "draft"),
            "paused": await count(Rule, head, Rule.status == "paused"),
            "validated": await count(Rule, head, Rule.validation_status == "valid"),
            "versions": await count(Rule),
        },
        "schedules": {
            "total": await schedule_count(select(Rule.code).where(scoped_head)),
            # A schedule runs iff its rule code has a live, active head.
            "live": await schedule_count(
                select(Rule.code).where(scoped_head, Rule.status == "active")
            ),
        },
        "runs": {
            "total": await count(Run),
            # Execution, then control outcome - counted separately because
            # "the rule ran" and "the rule found something" are not the same
            # question and collapsing them makes working rules look broken.
            "running": await count(Run, Run.status == "running"),
            "completed": await count(Run, Run.status == "success"),
            "failed": await count(Run, Run.status == "failed"),
            "breaches": await count(Run, Run.outcome == "fail"),
            "cleared": await count(Run, Run.outcome == "pass"),
        },
        "tickets": {
            "total": await count(Ticket),
            "open": await count(Ticket, Ticket.status == "open"),
            "acknowledged": await count(Ticket, Ticket.status == "acknowledged"),
            "dismissed": await count(Ticket, Ticket.status == "dismissed"),
        },
        "scheduler": {"running": scheduler.scheduler.running, "jobs": len(scheduler.status())},
    }