"""Alembic requires an explicit URL; do not hard-code production credentials."""

import os

from alembic import context
from sqlalchemy import create_engine, pool


def database_url() -> str:
    configured = context.config.attributes.get("database_url") or os.environ.get("DATABASE_URL")
    if not isinstance(configured, str) or not configured:
        raise RuntimeError("Set DATABASE_URL before running migrations")
    return configured


if context.is_offline_mode():
    context.configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
