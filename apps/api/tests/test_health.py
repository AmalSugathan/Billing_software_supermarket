from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from supermarket.config import Settings
from supermarket.database import SCHEMA_REVISION, build_engine
from supermarket.main import create_app


def test_liveness_does_not_depend_on_database() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}
        assert response.headers["cache-control"] == "no-store"


def test_readiness_fails_closed_without_database() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}


def test_schema_must_exist_and_match() -> None:
    # SQLite exercises HTTP failure branches only. PostgreSQL has a separate CI gate.
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    with TestClient(create_app(Settings(), engine)) as client:
        assert client.get("/api/v1/health/ready").status_code == 503
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            connection.execute(text("CREATE TABLE deployment_metadata (key TEXT, value TEXT)"))
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"),
                {"revision": SCHEMA_REVISION},
            )
            connection.execute(
                text("INSERT INTO deployment_metadata VALUES ('schema_baseline', 'wrong')")
            )
        assert client.get("/api/v1/health/ready").status_code == 503
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE deployment_metadata SET value = 'development-pipeline'")
            )
        assert client.get("/api/v1/health/ready").json() == {"status": "ready"}
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = 'unknown'"))
        assert client.get("/api/v1/health/ready").status_code == 503
    engine.dispose()


def test_contract_exposes_identity_and_protects_business_endpoints() -> None:
    with TestClient(create_app(Settings())) as client:
        schema = client.get("/openapi.json").json()
        assert "/api/v1/auth/register" in schema["paths"]
        assert "/api/v1/businesses" in schema["paths"]
        assert "/api/v1/sales" not in schema["paths"]
        assert client.get("/api/v1/businesses").status_code == 401
        assert "503" in schema["paths"]["/api/v1/health/ready"]["get"]["responses"]


def test_configuration_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/test")
    assert Settings.from_environment().database_url == "postgresql+psycopg://localhost/test"
    monkeypatch.setenv("DATABASE_URL", "")
    assert Settings.from_environment().database_url is None


def test_runtime_rejects_non_postgresql_database() -> None:
    import pytest

    with pytest.raises(ValueError, match="PostgreSQL"):
        build_engine("sqlite://")


def test_configured_postgresql_engine_is_lazy() -> None:
    # Construction must not require a running server; readiness checks do.
    with TestClient(create_app(Settings("postgresql+psycopg://localhost:1/missing"))) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/api/v1/health/ready").status_code == 503


def test_auth_database_failure_is_sanitized() -> None:
    configuration = Settings(
        "postgresql+psycopg://localhost:1/missing", allowed_origins=("http://testserver",)
    )
    with TestClient(create_app(configuration)) as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"email": "owner@example.com", "password": "a-long-test-password"},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "Database operation unavailable"}


def test_unconfigured_registration_reports_unavailable() -> None:
    with TestClient(create_app(Settings(allowed_origins=("http://testserver",)))) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://testserver"},
            json={
                "email": "owner@example.com",
                "password": "a-long-test-password",
                "display_name": "Owner",
            },
        )
        assert response.status_code == 503


def test_secure_configuration(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(ValueError, match="secure cookies"):
        Settings.from_environment()
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://shop.example.com")
    assert Settings.from_environment().cookie_secure
