"""Explicit development-only runtime role provisioning; never a production migration policy."""

import os

from sqlalchemy import create_engine, text


def main() -> None:
    if os.environ.get("APP_ENV") != "development":
        raise RuntimeError("Set APP_ENV=development explicitly; this helper is development-only")
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("Set MIGRATION_DATABASE_URL to your local migration/admin connection")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supermarket_app') THEN
                CREATE ROLE supermarket_app LOGIN PASSWORD 'local_app_only'
                  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
              END IF;
            END $$;
        """)
        )
        connection.execute(text("GRANT USAGE ON SCHEMA public TO supermarket_app"))
        connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                "IN SCHEMA public TO supermarket_app"
            )
        )
        connection.execute(
            text("REVOKE UPDATE, DELETE ON audit_log, security_event FROM supermarket_app")
        )
        connection.execute(
            text(
                "REVOKE INSERT, UPDATE, DELETE ON permission, deployment_metadata, "
                "alembic_version FROM supermarket_app"
            )
        )
    engine.dispose()
    print("Development role provisioned. Use supermarket_app for the API, not the migration role.")


if __name__ == "__main__":
    main()
