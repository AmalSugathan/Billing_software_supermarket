"""Explicit database wiring. Application startup never creates or migrates tables."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

SCHEMA_REVISION = "0006_pos_payments"


def build_engine(database_url: str) -> Engine:
    if not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("Runtime database must use PostgreSQL with psycopg")
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )


def database_ready(engine: Engine | None) -> bool:
    if engine is None:
        return False
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            marker = connection.execute(
                text("SELECT value FROM deployment_metadata WHERE key = :key"),
                {"key": "schema_baseline"},
            ).scalar()
            return revision == SCHEMA_REVISION and marker == "development-pipeline"
    except SQLAlchemyError:
        return False
