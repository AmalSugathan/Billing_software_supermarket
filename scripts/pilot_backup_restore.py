"""Local-only private snapshot backup and isolated restore verification."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def digests(connection):
    names = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
    result = {}
    for (name,) in names:
        digest = hashlib.sha256()
        count = 0
        rows = connection.execute(
            sql.SQL(
                "SELECT row_to_json(t)::text FROM public.{} t ORDER BY row_to_json(t)::text"
            ).format(sql.Identifier(name))
        )
        for (value,) in rows:
            digest.update(value.encode())
            digest.update(b"\n")
            count += 1
        result[name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, required=True)
    args = parser.parse_args()
    configured = os.environ.get("PILOT_DATABASE_URL")
    runtime = os.environ.get("PILOT_RUNTIME_DATABASE_URL")
    if not configured or not runtime:
        parser.error(
            "Set PILOT_DATABASE_URL and PILOT_RUNTIME_DATABASE_URL locally; "
            "do not pass credentials as command arguments"
        )
    source = make_url(configured)
    app = make_url(runtime)
    if (
        source.host not in {"127.0.0.1", "localhost"}
        or source.database != app.database
        or source.host != app.host
        or source.port != app.port
    ):
        parser.error(
            "This drill only supports the same local development database and runtime role"
        )
    root = Path(__file__).resolve().parents[1]
    folder = (root / ".tools" / "pilot_backups").resolve()
    if not folder.is_relative_to((root / ".tools").resolve()):
        raise RuntimeError("Backup path is outside the private workspace")
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    restored_name = "pilot_restore_" + token
    backup = folder / (token + ".dump")
    environment = {
        **os.environ,
        "PGPASSWORD": source.password or "",
        "PGHOST": source.host or "",
        "PGPORT": str(source.port or 5432),
        "PGUSER": source.username or "",
    }
    connection_args = dict(
        host=source.host,
        port=source.port or 5432,
        user=source.username,
        password=source.password,
        dbname=source.database,
    )
    suffix = ".exe" if os.name == "nt" else ""
    started = datetime.now(UTC)
    with psycopg.connect(**connection_args, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restored_name)))
    try:
        with psycopg.connect(**connection_args) as snapshot:
            snapshot.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot_id = snapshot.execute("SELECT pg_export_snapshot()").fetchone()[0]
            expected = digests(snapshot)
            subprocess.run(  # noqa: S603 -- fixed program and argument list; local admin tool
                [
                    str(args.pg_bin / ("pg_dump" + suffix)),
                    "--format=custom",
                    "--schema=public",
                    "--snapshot=" + snapshot_id,
                    "--file=" + str(backup),
                    source.database,
                ],
                env=environment,
                check=True,
                capture_output=True,
            )  # noqa: S603 -- fixed executable and argument list
        subprocess.run(  # noqa: S603 -- fixed program and argument list; local admin tool
            [
                str(args.pg_bin / ("pg_restore" + suffix)),
                "--exit-on-error",
                "--clean",
                "--if-exists",
                "--dbname=" + restored_name,
                str(backup),
            ],
            env=environment,
            check=True,
            capture_output=True,
        )  # noqa: S603
        with psycopg.connect(**{**connection_args, "dbname": restored_name}) as restored:
            actual = digests(restored)
        if actual != expected:
            raise RuntimeError("Restored rows differ from the consistent source snapshot")
        with psycopg.connect(
            host=app.host,
            port=app.port or 5432,
            user=app.username,
            password=app.password,
            dbname=restored_name,
        ) as restricted:
            role = restricted.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if role != (False, False):
                raise RuntimeError("Runtime role bypasses isolation")
            for table in (
                "stock_movement",
                "financial_movement",
                "sales_invoice",
                "purchase",
                "credit_note",
                "offline_sale",
            ):
                if (
                    restricted.execute(
                        sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table))
                    ).fetchone()[0]
                    != 0
                ):
                    raise RuntimeError("Restored runtime tenant isolation failed")
        report = {
            "completed_at": datetime.now(UTC).isoformat(),
            "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 2),
            "tables_verified": len(expected),
            "rows_verified": sum(item["rows"] for item in expected.values()),
            "full_row_snapshot_match": True,
            "runtime_without_tenant_sees_zero_financial_rows": True,
            "backup_retained_privately": True,
            "isolated_restore_database_removed": True,
        }
        (folder / (token + ".report.json")).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, indent=2))
    finally:
        if restored_name != "pilot_restore_" + token or not token.isalnum():
            raise RuntimeError("Refusing cleanup of an unexpected database")
        with psycopg.connect(**connection_args, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(restored_name)))


if __name__ == "__main__":
    main()
