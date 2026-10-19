"""Audit Portal - FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import assurance, auth, datasources, rules, runs, schedules, tickets
from app.db import SessionLocal, init_models, table_names
from app.engine import scheduler
from app.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
)
log = logging.getLogger("audit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting Audit Portal API")
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
    title="Audit Portal API",
    description=(
        "Continuous auditing platform.\n\n"
        "Rules are plain Python files. Each `run(ctx)` returns a `RuleResult` "
        "whose `status` is `success` (the rule executed) or `failed` (it could "
        "not) - and whose `rows` are what it found. Empty `rows` means the "
        "control passed; non-empty `rows` mean it breached, and raise a ticket "
        "that an auditor acknowledges. Execution and outcome are deliberately "
        "separate: a rule that never ran has no opinion about the control."
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
app.include_router(assurance.router)


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


