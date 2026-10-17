"""Pydantic contracts - API payloads and the rule return contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RuleStatus = Literal["draft", "active", "paused", "retired"]
Severity = Literal["low", "medium", "high", "critical"]
QueryMode = Literal["DELTA", "SNAPSHOT", "WINDOW"]
RunStatus = Literal["running", "pass", "fail", "error"]
TicketStatus = Literal["open", "acknowledged", "dismissed"]


# ---------------------------------------------------------------------------
# THE RULE CONTRACT
# ---------------------------------------------------------------------------
class RuleResult(BaseModel):
    """What every rule .py file must return from run(ctx).

    no_rows_returned -> run.status = pass, no ticket
    rows_returned    -> run.status = fail, ticket created with `rows`
    error            -> run.status = error, no ticket, traceback stored
    """

    status: Literal["no_rows_returned", "rows_returned", "error"] = Field(
        description="no_rows_returned = pass, rows_returned = fail, error = rule bug"
    )
    summary: str = Field(default="", max_length=2000)
    metrics: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    error: str = Field(default="", max_length=4000)


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
    id: int
    code: str
    name: str
    entity: str
    description: str
    severity: Severity
    query_mode: QueryMode
    window_days: int
    status: RuleStatus
    source_file: str
    current_version: int
    datasource_hint: str
    created_by: str
    origin: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RuleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    entity: str = "ABM"
    description: str = ""
    severity: Severity = "medium"
    query_mode: QueryMode = "SNAPSHOT"
    window_days: int = 1
    source_code: str
    datasource_hint: str = ""
    status: RuleStatus = "draft"


class RuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    severity: Severity | None = None
    query_mode: QueryMode | None = None
    window_days: int | None = None
    status: RuleStatus | None = None
    source_code: str | None = None
    change_note: str = ""


class RuleVersionOut(BaseModel):
    id: int
    rule_id: int
    version: int
    source_code: str
    change_note: str
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


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
# Schedule
# ---------------------------------------------------------------------------
class ScheduleOut(BaseModel):
    id: int
    rule_id: int
    cron: str
    timezone: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ScheduleCreate(BaseModel):
    rule_id: int
    cron: str = Field(min_length=5, max_length=64)
    timezone: str = "UTC"
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
class RunOut(BaseModel):
    id: int
    rule_id: int
    rule_version: int
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
    schedule_id: int | None
    severity: Severity
    summary: str
    rows: list[dict[str, Any]]
    status: TicketStatus
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    ack_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketAck(BaseModel):
    status: Literal["acknowledged", "dismissed"] = "acknowledged"
    note: str = Field(default="", max_length=4000)


# ---------------------------------------------------------------------------
# Param
# ---------------------------------------------------------------------------
class ParamOut(BaseModel):
    id: int
    key: str
    label: str
    value_numeric: float | None
    value_text: str | None
    description: str
    updated_by: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class ParamUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    label: str = ""
    value_numeric: float | None = None
    value_text: str | None = None
    description: str = ""