import os
from collections.abc import Generator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from supermarket.config import Settings
from supermarket.main import create_app


@dataclass
class PostgreSQLCase:
    admin: Engine
    runtime: Engine
    admin_url: str
    settings: Settings

    def client(self) -> TestClient:
        return TestClient(create_app(self.settings, self.runtime))


@pytest.fixture
def postgres_case() -> Generator[PostgreSQLCase]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL tests require TEST_DATABASE_URL")
        pytest.skip("Set TEST_DATABASE_URL to run real identity/RLS integration tests")
    assert url is not None
    schema, role = "identity_test_" + uuid4().hex, "identity_role_" + uuid4().hex
    admin = create_engine(url)
    migration_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    schema_admin = create_engine(migration_url)
    runtime_url = make_url(url).update_query_dict(
        {"options": f"-csearch_path={schema} -crole={role}"}
    )
    runtime = create_engine(runtime_url, pool_size=2, max_overflow=0)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                text(f'CREATE ROLE "{role}" NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            )
        configuration = Config("alembic.ini")
        configuration.attributes["database_url"] = migration_url.render_as_string(
            hide_password=False
        )
        command.upgrade(configuration, "head")
        with admin.begin() as connection:
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            connection.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA "{schema}" TO "{role}"'
                )
            )
            connection.execute(
                text(
                    f'REVOKE UPDATE, DELETE ON "{schema}".audit_log, '
                    f'"{schema}".security_event, "{schema}".stock_batch, '
                    f'"{schema}".stock_movement, "{schema}".purchase, '
                    f'"{schema}".purchase_item FROM "{role}"'
                )
            )
            connection.execute(
                text(
                    f'REVOKE INSERT, UPDATE, DELETE ON "{schema}".permission, '
                    f'"{schema}".deployment_metadata, "{schema}".alembic_version FROM "{role}"'
                )
            )
        yield PostgreSQLCase(
            schema_admin,
            runtime,
            migration_url.render_as_string(hide_password=False),
            Settings(cookie_secure=False, allowed_origins=("http://testserver",)),
        )
    finally:
        runtime.dispose()
        schema_admin.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()
