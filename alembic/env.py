"""Alembic environment — async-aware migration runner.

Mirrors Flyway's behaviour : reads the DB URL from app config (env vars),
discovers SQLAlchemy metadata for autogenerate, runs upgrades inside a
transaction.

Two modes :
- offline : emits SQL to stdout (useful for review + DBA-managed deploys).
- online : connects to the DB and runs migrations directly (CI/CD + dev).

Both modes use the same metadata source (`Base.metadata`) so autogenerate
sees every model imported here.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import every model module so its ORM classes are registered on Base.metadata.
# Without these explicit imports, autogenerate would compare against an empty
# metadata and either silently produce a no-op migration or worse, drop tables.
from iris_service.auth import models as auth_models  # noqa: F401
from iris_service.config.settings import get_settings
from iris_service.customer import models as customer_models  # noqa: F401
from iris_service.db.base import Base

config = context.config

# Logging from the alembic.ini sections — only if a config file was found.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime DB URL into the alembic config — env-derived by
# default, but respect any URL already set on the config (tests override
# via `config.set_main_option("sqlalchemy.url", "sqlite:///...")` to
# point at a SQLite scratch DB without spinning up Postgres).
if not config.get_main_option("sqlalchemy.url"):
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.db.url)

# Metadata that autogenerate compares against the live DB.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout (no DB connection).

    Use case : a DBA wants to review the migration as raw SQL before
    applying it manually. Run as `alembic upgrade head --sql`.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Inner sync runner — invoked via run_sync from the async context."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # detect column type changes (VARCHAR(120) → 200)
        compare_server_default=True,  # detect default-value changes
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Online mode : connect with the async engine, run sync migrations inside."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # short-lived connection — no pooling for migrations
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Sync entry point that bootstraps the async migration runner."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
