"""SQLAlchemy models for the audit control store.

Seven tables. A rule is not one row - `rules_master` holds one row per VERSION
of a rule, identified by `code`, with exactly one flagged `is_version_active`
(the head). That single flag is enforced by a partial unique index, so two live
versions of the same rule are impossible at the database level.

Thresholds live in the rule source, not in a table. Changing one is a code
change, which produces a new version row - so the audit trail records it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    # No is_active column. A table is either registered or it is not; there is no
    # third state, so there is nothing for a flag to record.


# ---------------------------------------------------------------------------
# Rules - one row per VERSION
# ---------------------------------------------------------------------------
class Rule(Base, TimestampMixin):
    """A single version of a rule.

    `code` identifies the LOGICAL rule and repeats across versions.
    `is_version_active` marks the head - the version the scheduler runs. The
    partial unique index `uq_rules_master_active_version` guarantees one head per
    code in the database, not just in application code. A draft that is NOT the
    head is a candidate: it sits beside the live version without taking it off the
    air, until it is published and the head moves to it.
    """

    __tablename__ = "rules_master"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_rules_master_code_version"),
        Index(
            "uq_rules_master_active_version",
            "code",
            unique=True,
            postgresql_where=text("is_version_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_version_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)

    # How far back from the schedule's anchor date this rule reads.
    # 1 = that day only. 5 = T-5 to T. One number, one job.
    lookback_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # --- validation gate -------------------------------------------------
    # A rule must pass validation before it can be activated, so a broken rule
    # never reaches an unattended 06:30 scheduled run.
    validation_status: Mapped[str] = mapped_column(
        String(16), default="unvalidated", nullable=False
    )
    validation_messages: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by: Mapped[str | None] = mapped_column(String(64))

    # --- declared by the rule file ---------------------------------------
    # The union of the DATASOURCES list and every ctx.table() found in the SQL:
    # the audit trail of what this version actually reads.
    declared_datasources: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)

    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    origin: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)

    schedules: Mapped[list["RuleSchedule"]] = relationship(
        "RuleSchedule", primaryjoin="foreign(RuleSchedule.rule_code) == Rule.code",
        viewonly=True,
    )


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
class RuleSchedule(Base, TimestampMixin):
    """A cadence for a rule.

    Keyed by `rule_code`, NOT by a row id: a schedule belongs to the logical
    rule, so a new version can never orphan it. There is no FK - `code` repeats
    across versions and cannot be unique. The scheduler resolves
    rule_code -> head version at fire time, and reports any code it cannot
    resolve at startup rather than silently dropping those schedules.
    """

    __tablename__ = "rule_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
class Run(Base):
    """One execution of one rule version.

    `rule_id` points at the EXACT version row that ran, so "what code produced
    this run" is a single lookup. `rule_code` is denormalised for list views so
    they never need a join.
    """

    __tablename__ = "rule_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules_master.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Denormalised alongside rule_code for the same reason: a list view must be
    # able to say WHICH version produced a run without joining back to the
    # version history. rule_id remains the authoritative pointer.
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rule_schedules.id", ondelete="SET NULL"), index=True
    )
    data_date: Mapped[date | None] = mapped_column(Date)
    triggered_by: Mapped[str] = mapped_column(String(32), default="schedule", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # status  = did the rule EXECUTE?   running | success | failed
    # outcome = what did the control find? pass | fail, NULL when it never ran
    #
    # These are different questions and used to share one column, which made a
    # rule that worked perfectly and found a breach look identical to a rule
    # that crashed. See app/engine/ticketing.py for the mapping, expressed once.
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False, index=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    
    error_detail: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ---------------------------------------------------------------------------
# Tickets (the alert an auditor acknowledges)
# ---------------------------------------------------------------------------
class Ticket(Base):
    """The alert an auditor acknowledges.

    One failing run opens one ticket. There is no dedupe: a breach that is still
    open tomorrow produces another ticket tomorrow, because "it is still
    breaching" is the fact an auditor needs, not "we told you before".

    `severity` is a snapshot of the rule's severity at the moment the ticket
    was raised. The summary is not stored here - it is read from the run.
    """

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("rule_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules_master.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rule_schedules.id", ondelete="SET NULL")
    )
    severity: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Explanations (what a rule does, for somebody who does not read Python)
# ---------------------------------------------------------------------------
class RuleExplanation(Base, TimestampMixin):
    """One description of one version of a rule.

    Keyed by `rule_id` rather than by code, because a description is about a
    piece of source and each version has its own. A new version therefore gets
    its own row, and the previous one keeps describing the code it was written
    for.

    `source_sha256` is the authority: a row whose hash no longer matches the
    file on disk is withheld rather than shown, because a stale sentence about a
    control is worse than no sentence at all.

    `origin` says who wrote the words - "derived" (from the source alone),
    "ai" (a model, after passing the gates) or "edited" (a person corrected it).
    A person may correct a model; a model may not silently replace a person.
    """

    __tablename__ = "rule_explanations"
    __table_args__ = (
        UniqueConstraint("rule_id", name="uq_rule_explanations_rule"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules_master.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    breach_condition: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    cannot_explain: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    outputs: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)

    # --- who wrote it, and what checked it --------------------------------
    origin: Mapped[str] = mapped_column(String(16), default="derived", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt_version: Mapped[str] = mapped_column(String(16), default="")
    generated_by: Mapped[str] = mapped_column(String(64), default="")
    gates: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)

    # --- and who has read it ---------------------------------------------
    # The page says which of three things a reader is looking at. A model swap
    # clears this, because approval was given to a machine, not to a vendor.
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


