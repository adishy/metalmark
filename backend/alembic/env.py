"""Alembic environment. Migrations run as the OWNER role via a sync psycopg
driver (superuser in dev), so they can create roles + RLS policies."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

# Import all models so metadata is complete for autogenerate.
import app.models  # noqa: F401,E402
from alembic import context
from app.db import Base
from app.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _owner_sync_url() -> str:
    # Owner DSN uses asyncpg; migrations need a sync driver.
    return get_settings().owner_dsn.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=_owner_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_owner_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
