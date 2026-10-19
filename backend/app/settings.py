"""Application settings, loaded from environment / .env."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
# backend/app/settings.py -> backend/app -> backend
BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_DIR.parent


class ClientEntity(BaseModel):
    """One audited business.

    `code` is what a rule run, a ticket and a run folder carry, so it is
    uppercase and hyphen-safe rather than pretty. `name` is what a human reads
    in the app bar and on the Data Sources screen. `systems` is which upstream
    systems this client draws from - descriptive only, and it changes without a
    rule or a datasource changing, which is why it is configuration.
    """

    code: str
    name: str
    systems: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- database -------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "ABM_Audit_Portal"

    # --- auth -----------------------------------------------------------
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_hours: int = 8

    # bootstrap users, seeded once on first start
    admin_username: str = "admin_user"
    admin_password: str = ""
    auditor_username: str = "audit_user"
    auditor_password: str = ""

    # --- rule generation (any OpenAI-compatible endpoint) ----------------
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # The provider's own knobs, passed through on every call. A reasoning
    # model with a large thinking budget can spend minutes on a single rule;
    # `{"reasoning_effort": "low"}` is the usual answer. It is optional and
    # not standard: an endpoint that does not have it answers 400, and the
    # call is retried once without it.
    llm_extra_params: dict[str, Any] = Field(default_factory=dict)

    # How many times the agent may try to produce a rule that runs. The first
    # attempt is the generation; the rest are repairs of a failure it was shown.
    # Three is enough for a broken column name and one wrong idea, and few
    # enough that a hopeless prompt does not spend the client's money.
    llm_max_attempts: int = 3

    # How long one model call may take. Generous, because a reasoning model
    # with a large thinking budget is slow and a timeout mid-answer wastes
    # everything it had thought.
    llm_timeout_seconds: int = 300

    # How long a candidate rule may run before it is abandoned.
    trial_timeout_seconds: int = 30

    # --- client identity ---------------------------------------------------
    # WHO this deployment audits. Configuration, not code, so the product ships
    # without naming anyone: a deployment writes CLIENT_ENTITIES into its .env
    # and the app bar, the rules, the runs and the tickets all follow.
    #
    # The defaults below are the neutral demo estate, deliberately unnamed. A
    # wrong code here is a scope that silently matches nothing, so the shape is
    # checked at boot rather than at the first request.
    client_entities: list[ClientEntity] = [
        ClientEntity(
            code="CLIENT-A",
            name="Client A",
            systems=["POS", "Warehouse", "HRMS"],
        ),
        ClientEntity(
            code="CLIENT-B",
            name="Client B",
            systems=["POS", "HRMS", "ERP"],
        ),
    ]

    # --- paths ----------------------------------------------------------
    # The CSV landing zone is mounted, not vendored: a volume in production,
    # a generated tree in development. Nothing in the app writes to it.
    sourcedata_dir: Path = APP_ROOT / "mount" / "SourceData"

    # Per-run execution folders. Each holds the exact rule source and the
    # exact CSVs one run read, so a failure can be replayed byte for byte.
    runs_dir: Path = APP_ROOT / "mount" / "Rules_Executions"

    # Scratch space for a candidate rule that is being tested before it is
    # saved. Separate from runs_dir on purpose: run folders are numbered from
    # the same counter as `rule_runs`, so a trial that took one would make
    # every later run fail with FileExistsError.
    trials_dir: Path = APP_ROOT / "mount" / ".trials"

    rules_dir: Path = BACKEND_DIR / "rules"

    # --- rule execution guardrails --------------------------------------
    max_rows_in_ticket: int = 500

    # --- cors (frontend dev server) -------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _entities_are_usable(self) -> "Settings":
        """A bad entity code is a scope that matches nothing, quietly.

        The app bar offers it, the user picks it, and every list comes back
        empty - which looks exactly like a portal with no data. Catching a
        duplicate or a lowercase code here turns that into a refusal to start.
        """
        codes = [e.code for e in self.client_entities]
        if not codes:
            raise ValueError("client_entities must name at least one client")
        if len(set(codes)) != len(codes):
            raise ValueError(f"client_entities has duplicate codes: {codes}")
        for e in self.client_entities:
            if e.code != e.code.upper() or not e.code:
                raise ValueError(
                    f"client entity code {e.code!r} must be uppercase - it is "
                    f"stored on runs, tickets and folders, so it has to match "
                    f"the shape the rest of the app compares against"
                )
        return self

    def resolved_jwt_secret(self) -> str:
        """Generate an ephemeral secret if none is configured (dev convenience)."""
        if self.jwt_secret:
            return self.jwt_secret
        generated = secrets.token_urlsafe(48)
        object.__setattr__(self, "jwt_secret", generated)
        return generated


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()