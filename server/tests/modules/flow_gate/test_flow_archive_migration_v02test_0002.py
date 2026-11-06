"""170: execute each vendor's actual SQL and verify a display-only, idempotent round trip.

The optional engines use the same explicit test DSNs as migration 165 tests.
No production connection defaults are used. Missing engines are reported as skips.
"""
from pathlib import Path
import os
import sqlite3
import uuid
from urllib.parse import unquote, urlparse

import pytest

SQL = Path(__file__).resolve().parents[3] / "sql" / "migrations"
MIGRATION = "170_group_flow_archive.sql"


@pytest.fixture(params=["sqlite", "postgres", "mysql"])
def engine(request, tmp_path):
    dialect = request.param
    if dialect == "sqlite":
        conn = sqlite3.connect(tmp_path / "archive-170.db")
        try:
            yield dialect, conn.cursor(), "?"
        finally:
            conn.close()
        return
    name = "fg_archive_170_" + uuid.uuid4().hex[:12]
    if dialect == "postgres":
        dsn = os.environ.get("FLOWGATE_PG_TEST_DSN") or os.environ.get("FLOWGATE_POSTGRES_TEST_DSN")
        if not dsn:
            pytest.skip("PostgreSQL test DSN is not configured (FLOWGATE_PG_TEST_DSN / FLOWGATE_POSTGRES_TEST_DSN)")
        psycopg = pytest.importorskip("psycopg")
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA "{name}"')
                try:
                    cur.execute(f'SET search_path TO "{name}"')
                    yield dialect, cur, "%s"
                finally:
                    cur.execute('SET search_path TO public')
                    cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        return
    dsn = os.environ.get("FLOWGATE_MYSQL_TEST_DSN")
    if not dsn:
        pytest.skip("MySQL test DSN is not configured (FLOWGATE_MYSQL_TEST_DSN)")
    pymysql = pytest.importorskip("pymysql")
    parsed = urlparse(dsn)
    conn = pymysql.connect(host=parsed.hostname, port=parsed.port or 3306,
                           user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
                           database=parsed.path.lstrip("/"), charset="utf8mb4", autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
            try:
                cur.execute(f"USE `{name}`")
                yield dialect, cur, "%s"
            finally:
                cur.execute(f"DROP DATABASE `{name}`")
    finally:
        conn.close()


def test_170_existing_rows_and_archive_restore_round_trip(engine):
    dialect, cur, placeholder = engine
    table = "`groups`" if dialect == "mysql" else '"groups"'
    cur.execute(f"CREATE TABLE {table} (group_id VARCHAR(191) PRIMARY KEY, title TEXT, "
                "status VARCHAR(20), deleted_at TEXT, updated_at TEXT)")
    for ident in ("target", "untouched"):
        cur.execute(f"INSERT INTO {table} VALUES ({','.join([placeholder] * 5)})",
                    (ident, "보존할 Flow", "OPEN", None, "original"))
    fields = "group_id,title,status,deleted_at,updated_at"
    cur.execute(f"SELECT {fields} FROM {table} ORDER BY group_id")
    before = cur.fetchall()
    script = (SQL / dialect / MIGRATION).read_text(encoding="utf-8")
    # Execute the checked-in vendor SQL verbatim (only split its statements).
    for statement in script.split(";"):
        if statement.strip():
            cur.execute(statement)
    archive_fields = "archived_at,archived_by,archive_restored_at"
    cur.execute(f"SELECT {archive_fields} FROM {table} ORDER BY group_id")
    assert list(cur.fetchall()) == [(None, None, None), (None, None, None)]

    def state(ident="target"):
        cur.execute(f"SELECT {archive_fields} FROM {table} WHERE group_id={placeholder}", (ident,))
        return cur.fetchone()

    def change(archived):
        values = ("2026-11-06T12:00:00Z", "사용자") if archived else ("2026-11-06T12:01:00Z",)
        assignment = (f"archived_at={placeholder},archived_by={placeholder},archive_restored_at=NULL"
                      if archived else f"archived_at=NULL,archived_by=NULL,archive_restored_at={placeholder}")
        predicate = "IS NULL" if archived else "IS NOT NULL"
        cur.execute(f"UPDATE {table} SET {assignment} WHERE group_id={placeholder} AND archived_at {predicate}",
                    (*values, "target"))
        return cur.rowcount

    assert change(True) == 1
    archived = state()
    assert archived == ("2026-11-06T12:00:00Z", "사용자", None)
    assert change(True) == 0 and state() == archived
    assert change(False) == 1
    restored = state()
    assert restored == (None, None, "2026-11-06T12:01:00Z")
    assert change(False) == 0 and state() == restored
    assert change(True) == 1 and state() == archived
    assert state("untouched") == (None, None, None)
    cur.execute(f"SELECT {fields} FROM {table} ORDER BY group_id")
    assert cur.fetchall() == before
