"""Real PostgreSQL gate; only a newly created disposable schema is mutated."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from supermarket.config import Settings
from supermarket.main import create_app


@pytest.mark.database
def test_postgresql_migration_constraints_rollback_and_readiness() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL CI gate requires TEST_DATABASE_URL")
        pytest.skip("Set TEST_DATABASE_URL for the PostgreSQL integration gate")
    assert database_url is not None
    assert database_url.startswith("postgresql+psycopg://")
    schema = "pipeline_test_" + uuid4().hex
    admin = create_engine(database_url)
    isolated_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(isolated_url)
    configuration = Config("alembic.ini")
    configuration.attributes["database_url"] = isolated_url.render_as_string(hide_password=False)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(configuration, "0002_identity_tenancy")
        existing_business = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO business (id, name, currency, timezone) "
                    "VALUES (:id, 'Existing migration fixture', 'INR', 'Asia/Kolkata')"
                ),
                {"id": existing_business},
            )
        command.upgrade(configuration, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM business WHERE id = :id"), {"id": existing_business}
                ).scalar_one()
                == "Existing migration fixture"
            )
        with TestClient(create_app(Settings(), engine)) as client:
            assert client.get("/api/v1/health/ready").status_code == 200
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("INSERT INTO deployment_metadata (key, value) VALUES ('schema_baseline', 'x')")
            )
        # Transaction failure must leave the original marker intact.
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT value FROM deployment_metadata WHERE key = 'schema_baseline'")
                ).scalar()
                == "development-pipeline"
            )
        command.downgrade(configuration, "base")
        with TestClient(create_app(Settings(), engine)) as client:
            assert client.get("/api/v1/health/ready").status_code == 503
        command.upgrade(configuration, "head")
        with TestClient(create_app(Settings(), engine)) as client:
            assert client.get("/api/v1/health/ready").status_code == 200
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
