"""Pydantic contracts - API payloads and the rule return contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.permissions import permissions_list, role_label

RuleStatus = Literal["draft", "active", "paused", "retired"]
Severity = Literal["low", "medium", "high", "critical"]
# Did the rule EXECUTE? Independent of what it found.
RunStatus = Literal["running", "success", "failed"]
# What did the control find? None when the rule never got to an answer.
RunOutcome = Literal["pass", "fail"]
TicketStatus = Literal["open", "acknowledged", "dismissed"]
CadenceKind = Literal[
    "HOURLY", "DAILY", "WEEKDAYS", "WEEKLY", "MONTHLY", "CUSTOM"
]
ValidationStatus = Literal["unvalidated", "valid", "invalid"]
# How a rule came to exist. "llm" means the source was machine-written and a
# human read it before it was saved - a different thing from a hand-written
# rule, and worth knowing when you are deciding how much to trust it.
RuleOrigin = Literal["manual", "llm"]


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
    # What that role is CALLED, derived alongside the capabilities. A screen
    # showing a person's account should not have to fetch the whole matrix to
    # write "Administrator" instead of "admin".
    role_label: str = ""
    is_active: bool
    # What this user may do, derived from the role. Carried on the wire so
    # the frontend never re-derives it from the role string - a second copy
    # of the matrix is a second answer to "may I?".
    permissions: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _resolve_permissions(cls, data: Any) -> Any:
        """Fill `permissions` and `role_label` from the role.

        Derived on the way out so every place that validates a User gets the
        same list. There is no way to hand out a user without their
        capabilities, and no call site that has to remember to ask.
        """
        if isinstance(data, dict):
            role = data.get("role")
            if role is None:
                return data
            return {
                **data,
                "permissions": data.get("permissions") or permissions_list(role),
                "role_label": data.get("role_label") or role_label(role),
            }

        role = getattr(data, "role", None)
        if role is None:
            return data
        return {
            "id": data.id,
            "username": data.username,
            "full_name": data.full_name,
            "role": data.role,
            "role_label": role_label(role),
            "is_active": data.is_active,
            "permissions": permissions_list(role),
        }


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=128)
    role: str = "auditor"


class UserUpdate(BaseModel):
    """A partial change to a user. A field left out is left alone."""

    full_name: str | None = Field(default=None, max_length=128)
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DemoAccount(BaseModel):
    """One account the sign-in screen offers, with its password.

    Only ever served when the deployment has opted in - see
    `settings.show_demo_accounts`. A deployment that has not gets an empty
    list, and the panel does not render.
    """

    username: str
    password: str
    role: str
    blurb: str


# ---------------------------------------------------------------------------
# The role matrix
# ---------------------------------------------------------------------------
class CapabilityOut(BaseModel):
    """One capability, named the way a screen shows it."""

    name: str
    label: str
    group: str
    group_label: str


class RoleOut(BaseModel):
    """One role, and what it holds."""

    name: str
    label: str
    description: str
    capabilities: list[str]


class RoleMatrixOut(BaseModel):
    """The whole matrix: the rows, the columns, and which crosses where.

    Served rather than assembled in the frontend. The matrix in
    `app/permissions.py` is the only answer to "what may this role do", and a
    screen that showed its own copy of it would be a second answer - one that
    drifts the first time a capability is added.
    """

    capabilities: list[CapabilityOut]
    roles: list[RoleOut]


# ---------------------------------------------------------------------------
# Datasource
# ---------------------------------------------------------------------------
class DatasourceOut(BaseModel):
    """A registered table.

    `used_by`, `landing`, `row_count` and `last_seen` are derived at read time
    and default to empty - they describe the moment of the request, not the row.
    """

    id: int
    name: str
    entity: str
    system: str
    description: str
    path_template: str
    grain: str
    columns: dict[str, Any]

    used_by: list[str] = []
    landing: str = "absent"          # landed | absent
    row_count: int | None = None
    last_seen: str | None = None

    model_config = {"from_attributes": True}


class DatasourcePreview(BaseModel):
    """The first rows of one landed file, so an admin can see what it holds.

    `total_rows` is the whole file while `limit` is how much of it is shown, so
    the screen can say "10 of 4,318" rather than implying it is all of it.

    `columns` rides along with `rows` because the type belongs beside the name it
    describes. Returning them separately just meant the screen drew two lists of
    the same columns.
    """

    entity: str
    name: str
    data_date: str
    total_rows: int | None = None
    columns: dict[str, str] = {}
    rows: list[dict[str, str | None]] = []
    limit: int = 10


class DatasourceRegister(BaseModel):
    """Register a table the ETL delivered.

    `columns` is optional: omitted means "read them from the file", which is
    what an admin wants nine times out of ten.
    """

    name: str
    entity: str
    system: str = ""
    description: str = ""
    grain: str = ""
    columns: dict[str, str] | None = None
    data_date: str | None = None


class DatasourceDiscovered(BaseModel):
    """One CSV that landed, described entirely by reading the file."""

    name: str
    entity: str
    data_date: str
    columns: dict[str, str]
    column_count: int
    row_count: int
    size_bytes: int
    modified_at: datetime
    state: str                        # new | registered


class DatasourceOverview(BaseModel):
    """Everything the screen needs, in one response."""

    entity: str
    entity_name: str
    systems: list[str]
    data_date: str
    dates: list[str]
    registered: list[DatasourceOut]
    discovered: list[DatasourceDiscovered]


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
    status: RuleStatus
    lookback_days: int
    validation_status: ValidationStatus
    validation_messages: list[dict[str, Any]]
    validated_at: datetime | None
    validated_by: str | None
    declared_datasources: list[str]
    source_file: str
    created_by: str
    origin: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RuleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    entity: str | None = None
    description: str = ""
    severity: Severity = "medium"
    lookback_days: int = Field(default=1, ge=1, le=365)
    source_code: str
    status: RuleStatus = "draft"
    # How the rule came to exist. "llm" says the source was machine-written and
    # a human read it - which is a different thing from a hand-written rule,
    # and a catalogue that cannot tell the two apart is not telling the truth.
    origin: RuleOrigin = "manual"


class RuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    severity: Severity | None = None
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    status: RuleStatus | None = None
    source_code: str | None = None
    change_note: str = ""


class ValidationReport(BaseModel):
    """Result of the static validation gate for one rule."""

    status: ValidationStatus
    errors: list[str]
    warnings: list[str]
    declared_datasources: list[str]
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


class RollbackOut(BaseModel):
    """What restoring an old version did.

    `activated` is separate from the report because the two answers differ: the
    copy can be created and validated and still not go live, and a client that
    assumed otherwise would tell the operator a control is running when it is
    not.
    """

    rule: RuleOut
    validation: ValidationReport
    activated: bool


class RuleStatusUpdate(BaseModel):
    """Lifecycle only. Never creates a version - pausing is not a logic change."""

    status: RuleStatus


class RuleGenerateRequest(BaseModel):
    """What the admin asked for, and the scope it may read.

    `datasources` is the selection, and it binds: the model is shown only
    those tables, and a rule that reads any other is rejected before it runs.
    A selector the model may wander outside of is a selector that selects
    nothing.
    """

    prompt: str = Field(min_length=5)
    entity: str | None = None
    datasources: list[str] = Field(min_length=1)
    lookback_days: int = Field(default=1, ge=1, le=365)
    data_date: date | None = None


class GenerationAttempt(BaseModel):
    """One attempt at a working rule, and how it ended."""

    n: int
    ok: bool
    error: str | None = None
    traceback: str | None = None


class TrialOut(BaseModel):
    """What the trial found when the candidate was run against real data."""

    status: str
    ok: bool
    outcome: str | None = None
    summary: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0


class RuleGenerateResponse(BaseModel):
    """A rule that runs - or the reason there is not one.

    Nothing is created by this call. The rule exists only after the admin has
    read the code and pressed Save.
    """

    ok: bool
    entity: str
    data_date: date
    lookback_days: int
    source_code: str | None = None
    name: str | None = None
    description: str | None = None
    datasources_used: list[str] = Field(default_factory=list)
    trial: TrialOut | None = None
    attempts: list[GenerationAttempt] = Field(default_factory=list)
    suggested_code: str | None = None
    error: str | None = None


class RuleTestResponse(BaseModel):
    run_id: int
    status: RunStatus
    outcome: RunOutcome | None
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
    rule_version: int | None
    schedule_id: int | None
    data_date: date | None
    triggered_by: str
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus
    outcome: RunOutcome | None
    summary: str
    metrics: dict[str, Any]
    rows: list[dict[str, Any]]
    error_detail: str
    duration_ms: int

    model_config = {"from_attributes": True}


class RunRequest(BaseModel):
    data_date: date | None = None


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
    status: TicketStatus
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    ack_note: str | None
    created_at: datetime

    # The finding text lives on the run, not the ticket, so every endpoint that
    # returns a ticket fills this in. It has to be declared here or Pydantic
    # strips it - which is why the ticket detail page showed no summary at all.
    summary: str = ""

    model_config = {"from_attributes": True}


class TicketAck(BaseModel):
    status: Literal["acknowledged", "dismissed"] = "acknowledged"
    note: str = Field(default="", max_length=4000)


class BulkAck(BaseModel):
    """
    Acknowledge many tickets at once.

    Carries the same filters the inbox is showing, so the action can never
    reach tickets the auditor cannot see. Only ever applies to `open` tickets -
    the endpoint pins that itself, so a client cannot acknowledge something
    another reviewer already closed.
    """

    status: Literal["acknowledged", "dismissed"] = "acknowledged"
    note: str = Field(default="", max_length=4000)
    rule_code: str | None = None
    severity: Severity | None = None
    q: str | None = None
    entity: str | None = None




# ---------------------------------------------------------------------------
# Plain-English explanation of a rule
# ---------------------------------------------------------------------------
class ExplanationStepOut(BaseModel):
    """One step, and the lines of the file it came from.

    `lines` is always the derivation's, never the model's - a description that
    points a reviewer at the wrong code is worse than one with no reference at
    all, because it looks checkable.
    """

    n: int
    text: str
    lines: list[int]
    kind: str = ""
    explainable: bool = True


class GateOut(BaseModel):
    """One check the words had to pass before they were shown."""

    name: str
    ok: bool
    detail: str = ""


class ExplanationOut(BaseModel):
    code: str
    version: int
    source_sha256: str

    # derived | ai | edited. What a reader is looking at decides what they are
    # entitled to assume about it.
    origin: str
    stored: bool
    # True when a stored description no longer matches the file on disk. The
    # words are withheld rather than shown, and the page says why.
    stale: bool
    llm_configured: bool

    purpose: str
    breach_condition: str
    steps: list[ExplanationStepOut]
    outputs: list[str]
    cannot_explain: list[dict[str, Any]]
    gates: list[GateOut]

    generated_by: str = ""
    model: str = ""
    prompt_version: str = ""
    generated_at: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class ExplanationEdit(BaseModel):
    """A person correcting the words. Sets origin=edited and clears the sign-off."""

    breach_condition: str = Field(min_length=1, max_length=2000)
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ExplanationReview(BaseModel):
    approved: bool = True
    note: str = Field(default="", max_length=2000)
