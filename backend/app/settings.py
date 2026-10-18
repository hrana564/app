"""Application settings, loaded from environment / .env."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/settings.py -> backend/app -> backend
BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_DIR.parent


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

    # --- paths ----------------------------------------------------------
    # The CSV landing zone is mounted, not vendored: a volume in production,
    # a generated tree in development. Nothing in the app writes to it.
    sourcedata_dir: Path = APP_ROOT / "mount" / "SourceData"

    # Per-run execution folders. Each holds the exact rule source and the
    # exact CSVs one run read, so a failure can be replayed byte for byte.
    runs_dir: Path = APP_ROOT / "Rules_Executions"

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