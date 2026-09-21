"""Application settings.

Secrets are loaded from files (docker secrets), never plain env vars, per
ARCHITECTURE §5. ``METALMARK_SECRET_KEY_FILE`` points at the Fernet key, and the
two database credentials accept the same ``_FILE`` suffix — which is what lets
``deploy/compose.yaml`` generate every credential at install time instead of
carrying one.
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

    # Signup is open by default (ADR-0027): the first signup creates the household,
    # later ones join it. Turn this off to close the door — the app is LAN/VPN-only
    # (ADR-0002), but "anyone who can reach the instance can join the household" is
    # a real property worth being able to switch off.
    open_signup: bool = Field(default=True, alias="METALMARK_OPEN_SIGNUP")

    # Which aggregator a *new* claim uses. Deliberately not a global switch for
    # sync: an existing connection carries its provider on its own row, so
    # changing this never re-points a live connection at a different backend.
    # Defaults to the real provider; set to "fake" for a credential-free demo.
    simplefin_provider: str = Field(default="simplefin", alias="METALMARK_SIMPLEFIN_PROVIDER")

    # Where a broken connection is reported. Unset — the default — means no
    # notifications at all, which is the right default for an instance nobody is
    # watching. See services/notifications.py.
    notify_webhook_url: str | None = Field(default=None, alias="METALMARK_NOTIFY_WEBHOOK_URL")

    secret_key_file: str | None = Field(default=None, alias="METALMARK_SECRET_KEY_FILE")
    # Fallback for non-docker local/test runs only.
    secret_key_inline: str | None = Field(default=None, alias="METALMARK_SECRET_KEY")

    # Docker's `_FILE` convention for the two database credentials. The Fernet key
    # above already worked this way; these follow because a password passed as an
    # environment variable is a password in `docker inspect` and in every process
    # listing on the host, and because a deployment that *generates* its
    # credentials (deploy/compose.yaml) has no other way to hand them over.
    postgres_password_file: str | None = Field(default=None, alias="POSTGRES_PASSWORD_FILE")
    app_db_password_file: str | None = Field(default=None, alias="APP_DB_PASSWORD_FILE")

    _secret_key: str = ""

    @model_validator(mode="after")
    def _load_secrets(self) -> Settings:
        if self.secret_key_inline:
            object.__setattr__(self, "_secret_key", self.secret_key_inline)
        elif self.secret_key_file:
            object.__setattr__(self, "_secret_key", _read_secret(self.secret_key_file))
        # A file that is set but unreadable leaves the environment value in place
        # rather than blanking it: the alternative turns a permissions mistake
        # into `password authentication failed`, which points at the database.
        if self.postgres_password_file:
            self.postgres_password = (
                _read_secret(self.postgres_password_file) or self.postgres_password
            )
        if self.app_db_password_file:
            self.app_db_password = (
                _read_secret(self.app_db_password_file) or self.app_db_password
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


def _read_secret(path: str) -> str:
    """The contents of a `_FILE` secret, stripped; `""` if it cannot be read.

    Stripped, not raw: a secret written by a person or by `echo` ends in a
    newline, and a password carrying a trailing ``\\n`` fails authentication in a
    way that reads as *wrong password* rather than *one stray byte*. The Postgres
    image strips for exactly this reason, and this has to agree with it — the
    same file is read by both sides of the connection.
    """
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
