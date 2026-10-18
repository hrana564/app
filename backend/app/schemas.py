"""Pydantic contracts - API payloads and the rule return contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RuleStatus = Literal["draft", "active", "paused", "retired"]
Severity = Literal["low", "medium", "high", "critical"]
QueryMode = Literal["SNAPSHOT", "WINDOW"]
RunStatus = Literal["running", "pass", "fail", "error"]
TicketStatus = Literal["open", "acknowledged", "dismissed"]
CadenceKind = Literal[
    "HOURLY", "DAILY", "WEEKDAYS", "WEEKLY", "MONTHLY", "CUSTOM"
]
TicketMode = Literal["ONE_PER_RUN", "ONE_PER_GROUP", "ONE_PER_ROW"]
DedupeMode = Literal["NONE", "PER_DATA_DATE", "FINGERPRINT"]
ValidationStatus = Literal["unvalidated", "valid", "invalid"]


# ---------------------------------------------------------------------------
# THE RULE CONTRACT
# ---------------------------------------------------------------------------
class RuleResult(BaseModel):
    """What every rule .py file must return from run(ctx).

    status          - did the rule EXECUTE correctly? "success" or "failed"
    error_message   - if status="failed", a human-readable reason; else None
    rows            - the findings. Non-empty -> ticket raised. Empty -> no ticket.

    A rule that runs and finds nothing returns {"status": "success", "rows": []}.
    A rule that cannot run at all (missing data, query error) returns
    {"status": "failed", "error_message": "...", "rows": []}.
    """

    status: Literal["success", "failed"] = Field(
        description="success = rule executed; failed = rule could not run"
    )
    error_message: str | None = Field(default=None, max_length=4000)
    summary: str = Field(default="", max_length=2000)
    metrics: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str = ""
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ---------------------------------------------------------------------------
# Datasource
# ---------------------------------------------------------------------------
class DatasourceOut(BaseModel):
    id: int
    name: str
    entity: str
    system: str
    description: str
    path_template: str
    grain: str
    columns: dict[str, Any]
    derived: dict[str, str]
    is_active: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------
class RuleOut(BaseModel):
    """One VERSION of a rule, as addressed by its permanent code."""

    id: int
    code: str
    version: int
    is_version_active: bool
    change_note: str
    name: str
    entity: str
    description: str
    severity: Severity
    query_mode: QueryMode
    window_days: int
    status: RuleStatus
    validation_status: ValidationStatus
    validation_messages: list[dict[str, Any]]
    validated_at: datetime | None
    validated_by: str | None
    ticket_mode: TicketMode
    group_by: str | None
    dedupe_mode: DedupeMode
    dedupe_window_hours: int
    alerting_enabled: bool
    declared_datasources: list[str]
    declared_alert: dict[str, Any]
    source_file: str
    datasource_hint: str
    created_by: str
    origin: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RuleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    entity: str = "LAD"
    description: str = ""
    severity: Severity = "medium"
    query_mode: QueryMode = "SNAPSHOT"
    window_days: int = 1
    source_code: str
    datasource_hint: str = ""
    status: RuleStatus = "draft"
    ticket_mode: TicketMode = "ONE_PER_RUN"
    group_by: str | None = None
    dedupe_mode: DedupeMode = "PER_DATA_DATE"
    alerting_enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    severity: Severity | None = None
    query_mode: QueryMode | None = None
    window_days: int | None = None
    status: RuleStatus | None = None
    source_code: str | None = None
    change_note: str = ""
    ticket_mode: TicketMode | None = None
    group_by: str | None = None
    dedupe_mode: DedupeMode | None = None
    dedupe_window_hours: int | None = None
    alerting_enabled: bool | None = None


class ValidationReport(BaseModel):
    """Result of the static validation gate for one rule."""

    status: ValidationStatus
    errors: list[str]
    warnings: list[str]
    declared_datasources: list[str]
    declared_alert: dict[str, Any]
    name: str | None = None
    description: str | None = None
    can_activate: bool = False


class RuleVersionOut(BaseModel):
    """One version in a rule's history."""

    version: int
    is_version_active: bool
    status: str
    validation_status: str
    source_file: str
    source_code: str
    change_note: str
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RuleStatusUpdate(BaseModel):
    """Lifecycle only. Never creates a version - pausing is not a logic change."""

    status: RuleStatus


class RuleGenerateRequest(BaseModel):
    prompt: str = Field(min_length=5)
    entity: str = "LAD"
    data_date: date | None = None


class RuleGenerateResponse(BaseModel):
    rule: RuleOut
    source_code: str
    explanation: str
    datasources_used: list[str]


class RuleTestResponse(BaseModel):
    run_id: int
    status: RunStatus
    summary: str
    metrics: dict[str, Any]
    rows: list[dict[str, Any]]
    error_detail: str
    duration_ms: int


# ---------------------------------------------------------------------------
# Rule schedules - friendly cadence, never raw cron in the UI
# ---------------------------------------------------------------------------
class ScheduleOut(BaseModel):
    id: int
    rule_code: str
    label: str
    kind: CadenceKind
    config: dict[str, Any]
    cron: str
    timezone: str
    data_lag_days: int
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_by: str
    created_at: datetime

    # Computed from kind/config by the API layer. Defaults exist so
    # model_validate() can build the object from the ORM row first; the API
    # then overwrites them with real values.
    cadence_label: str = ""
    next_run_relative: str | None = None
    upcoming_runs: list[datetime] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ScheduleCreate(BaseModel):
    rule_code: str
    label: str = ""
    kind: CadenceKind = "DAILY"
    config: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    data_lag_days: int = Field(default=1, ge=0, le=30)


class ScheduleUpdate(BaseModel):
    label: str | None = None
    kind: CadenceKind | None = None
    config: dict[str, Any] | None = None
    timezone: str | None = None
    data_lag_days: int | None = Field(default=None, ge=0, le=30)


class CadencePreviewRequest(BaseModel):
    kind: CadenceKind
    config: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    count: int = Field(default=5, ge=1, le=10)


class CadencePreviewResponse(BaseModel):
    """What the UI shows BEFORE saving: the derived cron plus the real fire times."""
    ok: bool
    cadence_label: str
    cron: str | None = None
    upcoming: list[datetime] = []
    problems: list[str] = []


class CadenceOption(BaseModel):
    """One choice in the friendly frequency picker."""

    id: str
    label: str
    kind: CadenceKind
    config: dict[str, Any]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
class RunOut(BaseModel):
    id: int
    rule_id: int
    rule_code: str
    schedule_id: int | None
    data_date: date | None
    triggered_by: str
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus
    summary: str
    metrics: dict[str, Any]
    rows: list[dict[str, Any]]
    rows_scanned: int
    is_shadow: bool
    error_detail: str
    duration_ms: int

    model_config = {"from_attributes": True}


class RunRequest(BaseModel):
    data_date: date | None = None
    shadow: bool = False


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------
class TicketOut(BaseModel):
    id: int
    ticket_no: str
    run_id: int
    rule_id: int
    rule_code: str
    schedule_id: int | None
    severity: Severity
    rows: list[dict[str, Any]]
    occurrences: int
    first_run_id: int | None
    last_run_id: int | None
    last_seen_at: datetime | None
    status: TicketStatus
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    ack_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketAck(BaseModel):
    status: Literal["acknowledged", "dismissed"] = "acknowledged"
    note: str = Field(default="", max_length=4000)


