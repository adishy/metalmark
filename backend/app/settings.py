"""Application settings.

Secrets are loaded from files (docker secrets), never plain env vars, per
ARCHITECTURE §5. ``METALMARK_SECRET_KEY_FILE`` points at the Fernet key.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    env: str = Field(default="dev", alias="METALMARK_ENV")
    log_level: str = Field(default="INFO", alias="METALMARK_LOG_LEVEL")

    # Postgres — the app connects as the NON-superuser APP_DB_USER so RLS applies.
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="metalmark", alias="POSTGRES_DB")
    # Superuser (owner) creds — used only by migrations/bootstrap.
    postgres_user: str = Field(default="metalmark", alias="POSTGRES_USER")
    postgres_password: str = Field(default="metalmark", alias="POSTGRES_PASSWORD")
    # Application role — used by API + worker at runtime (RLS enforced).
    app_db_user: str = Field(default="metalmark_app", alias="APP_DB_USER")
    app_db_password: str = Field(default="metalmark_app", alias="APP_DB_PASSWORD")

    default_base_currency: str = Field(default="USD", alias="METALMARK_DEFAULT_BASE_CURRENCY")

    session_idle_minutes: int = Field(default=1440, alias="METALMARK_SESSION_IDLE_MINUTES")
    session_absolute_hours: int = Field(default=720, alias="METALMARK_SESSION_ABSOLUTE_HOURS")

    secret_key_file: str | None = Field(default=None, alias="METALMARK_SECRET_KEY_FILE")
    # Fallback for non-docker local/test runs only.
    secret_key_inline: str | None = Field(default=None, alias="METALMARK_SECRET_KEY")

    _secret_key: str = ""

    @model_validator(mode="after")
    def _load_secret(self) -> Settings:
        if self.secret_key_inline:
            object.__setattr__(self, "_secret_key", self.secret_key_inline)
        elif self.secret_key_file and Path(self.secret_key_file).exists():
            object.__setattr__(
                self, "_secret_key", Path(self.secret_key_file).read_text().strip()
            )
        return self

    @property
    def secret_key(self) -> str:
        if not self._secret_key:
            raise RuntimeError(
                "METALMARK_SECRET_KEY not available: set METALMARK_SECRET_KEY_FILE "
                "(docker secret) or METALMARK_SECRET_KEY for tests."
            )
        return self._secret_key

    def _dsn(self, user: str, password: str) -> str:
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def app_dsn(self) -> str:
        """Runtime DSN — the RLS-bound application role."""
        return self._dsn(self.app_db_user, self.app_db_password)

    @property
    def owner_dsn(self) -> str:
        """Privileged DSN — migrations/bootstrap only."""
        return self._dsn(self.postgres_user, self.postgres_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
