#!/usr/bin/env python3
"""Engine-neutral DB access for the install-time bootstrap scripts.

0273 NR0003 P1-1. `create_dev_user.py` and `check_db_ready.py` both talked to
SQLite through `import sqlite3`, so all three install paths (setup.sh,
setup.ps1, deploy/docker-entrypoint.sh) skipped the admin bootstrap entirely
when DB_TYPE was mysql or postgres — a `DB_TYPE=postgres` install ran to
completion and left ZERO accounts able to log in. Supporting "DB selection"
(R0001) therefore means making this path engine-neutral, not just accepting the
connection values.

This module deliberately stays on raw DB-API drivers (the ones already pinned in
requirements.txt: PyMySQL, psycopg2-binary) rather than importing `config`.
Importing config constructs DatabaseSetting, which runs the migrator — the
bootstrap scripts must be able to *inspect* a half-migrated DB without advancing
it, and check_db_ready.py's whole job is to report on that state.

The scripts share three dialect differences, handled here so callers stay
readable:
  - parameter placeholders: "?" (sqlite) vs "%s" (mysql/postgres)
  - upsert syntax: INSERT OR REPLACE / ON DUPLICATE KEY UPDATE / ON CONFLICT
  - row access: sqlite3.Row and friends all differ; rows() returns plain dicts
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable

SQLITE = "sqlite"
MYSQL = "mysql"
POSTGRES = "postgres"

# DB_TYPE accepts several spellings for the file-backed engine (see the DBType
# enum in config.py); collapse them so callers only ever branch on three values.
_SQLITE_ALIASES = {"sqlite", "sqlite3", "local", ""}


class BootstrapDBError(RuntimeError):
    """Raised for a misconfigured or unreachable bootstrap connection."""


def resolve_db_type(raw: str | None = None) -> str:
    """Normalize $DB_TYPE to one of sqlite / mysql / postgres."""
    value = (raw if raw is not None else os.environ.get("DB_TYPE", "")).strip().lower()
    if value in _SQLITE_ALIASES:
        return SQLITE
    if value in (MYSQL, "mariadb"):
        return MYSQL
    if value in (POSTGRES, "postgresql"):
        return POSTGRES
    raise BootstrapDBError(
        f"Unsupported DB_TYPE={value!r} (expected sqlite3, mysql, or postgres)."
    )


def sqlite_db_path(base_dir: str) -> str:
    """Resolve DB_PATH the way create_dev_user.py always has."""
    db_path = os.environ.get("DB_PATH", "flowgate.db")
    return db_path if os.path.isabs(db_path) else os.path.join(base_dir, db_path)


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise BootstrapDBError(f"{name}={raw!r} is not an integer.")


def connect(db_type: str, base_dir: str, readonly: bool = False):
    """Open a DB-API connection for `db_type`.

    `readonly` is honoured for SQLite only, where it matters: opening a missing
    file read-write CREATES it, which would make check_db_ready.py manufacture
    the very empty-DB state it exists to detect. The networked engines cannot
    be brought into existence by connecting, so the flag is a no-op there.
    """
    if db_type == SQLITE:
        import sqlite3

        path = sqlite_db_path(base_dir)
        if not os.path.exists(path):
            raise BootstrapDBError(f"DB file not found: {path}")
        if readonly:
            uri = "file:{}?mode=ro".format(
                path.replace("?", "%3f").replace("#", "%23")
            )
            conn = sqlite3.connect(uri, uri=True, timeout=5)
        else:
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA foreign_keys = ON")
        return conn

    host = (os.environ.get("DB_HOST") or "127.0.0.1").strip()
    user = (os.environ.get("DB_USER") or "").strip()
    password = os.environ.get("DB_PASSWORD") or ""
    database = (os.environ.get("DB_DATABASE") or "").strip()
    if not database:
        raise BootstrapDBError("DB_DATABASE is required for mysql/postgres.")

    if db_type == MYSQL:
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - driver is pinned
            raise BootstrapDBError(f"PyMySQL is not installed: {exc}") from exc
        try:
            return pymysql.connect(
                host=host,
                port=_int_env("DB_PORT", 3306),
                user=user,
                password=password,
                database=database,
                charset="utf8mb4",
                autocommit=False,
            )
        except Exception as exc:
            raise BootstrapDBError(f"MySQL connection failed: {exc}") from exc

    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - driver is pinned
        raise BootstrapDBError(f"psycopg2 is not installed: {exc}") from exc
    # DB_SCHEMA defaults to 'public' the same way setup.sh / the entrypoint do;
    # pinning search_path keeps unqualified table names resolving to the schema
    # the migrator actually wrote to.
    schema = (os.environ.get("DB_SCHEMA") or "public").strip()
    try:
        return psycopg2.connect(
            host=host,
            port=_int_env("DB_PORT", 5432),
            user=user,
            password=password,
            dbname=database,
            options=f"-c search_path={schema}",
        )
    except Exception as exc:
        raise BootstrapDBError(f"PostgreSQL connection failed: {exc}") from exc


def q(sql: str, db_type: str) -> str:
    """Translate SQLite-style '?' placeholders to the engine's native form.

    Mirrors what SQLoader does for the shared query files (config.py declares
    "placeholder": "?" for exactly this reason), so the SQL in the bootstrap
    scripts stays written one way. Only bare '?' outside string literals is
    rewritten — the bootstrap SQL contains no literal question marks, and the
    guard keeps that assumption from silently breaking if one is added.
    """
    if db_type == SQLITE:
        return sql
    return re.sub(r"\?(?=(?:[^']*'[^']*')*[^']*$)", "%s", sql)


def rows(cursor) -> list[dict]:
    """Fetch all rows as plain dicts, regardless of driver row type."""
    if cursor.description is None:
        return []
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def one(cursor) -> dict | None:
    result = rows(cursor)
    return result[0] if result else None


def execute(conn, db_type: str, sql: str, params: Iterable[Any] = ()):
    """Run a statement with placeholder translation and return the cursor."""
    cursor = conn.cursor()
    cursor.execute(q(sql, db_type), tuple(params))
    return cursor


def upsert_user_project_role(conn, db_type: str, params: tuple) -> None:
    """Grant a project role, replacing any existing grant for the same user.

    `INSERT OR REPLACE` is SQLite-only; the other engines need their own upsert
    spelling against the (user_id, project_id) primary key.
    """
    if db_type == SQLITE:
        sql = (
            "INSERT OR REPLACE INTO user_project_roles "
            "(user_id, project_id, role_id, granted_at) VALUES (?, ?, ?, ?)"
        )
    elif db_type == MYSQL:
        sql = (
            "INSERT INTO user_project_roles "
            "(user_id, project_id, role_id, granted_at) VALUES (?, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE role_id = VALUES(role_id), "
            "granted_at = VALUES(granted_at)"
        )
    else:
        sql = (
            "INSERT INTO user_project_roles "
            "(user_id, project_id, role_id, granted_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id, project_id) DO UPDATE SET "
            "role_id = EXCLUDED.role_id, granted_at = EXCLUDED.granted_at"
        )
    execute(conn, db_type, sql, params)


def role_aggregate(db_type: str) -> str:
    """Comma-joined role list expression for the --list query."""
    if db_type == POSTGRES:
        return "STRING_AGG(upr.role_id, ',')"
    return "GROUP_CONCAT(upr.role_id)"


def describe_target(db_type: str, base_dir: str) -> str:
    """Human-readable connection target, for progress output."""
    if db_type == SQLITE:
        return f"sqlite:{sqlite_db_path(base_dir)}"
    host = os.environ.get("DB_HOST") or "127.0.0.1"
    port = os.environ.get("DB_PORT") or ("5432" if db_type == POSTGRES else "3306")
    return f"{db_type}:{host}:{port}/{os.environ.get('DB_DATABASE') or ''}"


def migrations_dirname(db_type: str) -> str:
    """Migration subdirectory sqloader applies for this engine (config.py)."""
    return {SQLITE: "sqlite", MYSQL: "mysql", POSTGRES: "postgres"}[db_type]
