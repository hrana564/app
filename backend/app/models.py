"""SQLAlchemy models for the audit control store.

Seven tables, matching the four core entities (rules / schedules / runs /
tickets) plus users, datasource registry and rule version history.

Audit thresholds live in the rule source, not in a table. Changing one is a
code change, which produces a new entry in rule_versions - so the audit trail
records the change rather than losing it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="auditor", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# Datasource registry - "ready connections" to the CSV landing zone
# ---------------------------------------------------------------------------
class Datasource(Base, TimestampMixin):
    __tablename__ = "datasources"
    __table_args__ = (UniqueConstraint("entity", "name", name="uq_datasource_entity_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    system: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    path_template: Mapped[str] = mapped_column(String(255), nullable=False)
    grain: Mapped[str] = mapped_column(String(255), default="")
    columns: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    derived: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# Rules - the aggregate root
# ---------------------------------------------------------------------------
class Rule(Base, TimestampMixin):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    query_mode: Mapped[str] = mapped_column(String(16), default="SNAPSHOT", nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)

    # --- validation gate -------------------------------------------------
    # A rule must pass validation before it can be activated, so a broken rule
    # never reaches an unattended 06:30 scheduled run.
    validation_status: Mapped[str] = mapped_column(
        String(16), default="unvalidated", nullable=False
    )
    validation_messages: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by: Mapped[str | None] = mapped_column(String(64))

    # --- alert policy ----------------------------------------------------
    # How a failing run becomes one or more tickets.
    ticket_mode: Mapped[str] = mapped_column(
        String(24), default="ONE_PER_RUN", nullable=False
    )
    group_by: Mapped[str | None] = mapped_column(String(64))
    dedupe_mode: Mapped[str] = mapped_column(
        String(24), default="PER_DATA_DATE", nullable=False
    )
    dedupe_window_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    alerting_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- declared by the rule file ---------------------------------------
    declared_datasources: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    declared_alert: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    datasource_hint: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    origin: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)

    versions: Mapped[list["RuleVersion"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )
    schedules: Mapped[list["RuleSchedule"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )


class RuleVersion(Base):
    """Append-only history. Rollback inserts a new row; it never deletes."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_rule_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rule: Mapped[Rule] = relationship(back_populates="versions")


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
class RuleSchedule(Base, TimestampMixin):
    __tablename__ = "rule_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(16), default="DAILY", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    cron: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    # 1 = audit yesterday's folder. A day folder is normally incomplete when a
    # schedule fires, so 1 is the sane default for daily controls.
    data_lag_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64), default="")

    rule: Mapped[Rule] = relationship(back_populates="schedules")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rule_schedules.id", ondelete="SET NULL"), index=True
    )
    data_date: Mapped[date | None] = mapped_column(Date)
    triggered_by: Mapped[str] = mapped_column(String(32), default="schedule", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    rows_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_shadow: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ---------------------------------------------------------------------------
# Tickets (the alert an auditor acknowledges)
# ---------------------------------------------------------------------------
class Ticket(Base):
    """The alert an auditor acknowledges.

    dedupe_mode PER_DATA_DATE means a second trigger of the same rule against
    the same data date does NOT open a new ticket. It increments `occurrences`
    and repoints `last_run_id`, so the ticket shows how many times the same
    underlying breach was observed instead of flooding the inbox.
    """

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rule_schedules.id", ondelete="SET NULL")
    )
    severity: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)

    # --- dedup bookkeeping ----------------------------------------------
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_run_id: Mapped[int | None] = mapped_column(Integer)
    last_run_id: Mapped[int | None] = mapped_column(Integer)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


