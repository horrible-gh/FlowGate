"""Regression for 110: 108 must not leave the tokens schema behind 107/109 semantics.

flowgate.default.0533 TS0006 rev1 (rejection): the text-only checks below were the whole
file before this revision. They read migration files as strings and never opened a
database, so they could not tell a working repair from a merely plausible-looking one --
exactly what the rejection said. The tests further down actually run the real
sqloader.migrator.DatabaseMigrator against a real sqlite3 database (always) and against a
real PostgreSQL/MySQL server when one is opted in (FLOWGATE_PG_TEST_DSN /
FLOWGATE_MYSQL_TEST_DSN, same opt-in convention as test_migration_107_postgres_schema_drift_0539.py),
and prove the claim TR0005 makes: 110 repairs the tokens schema whether 108 ran before or
after 109, without losing any row.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

ROOT = Path(__file__).resolve().parents[1] / "sql" / "migrations"
_SQLITE_DIR = ROOT / "sqlite"

_INVERSION_FILE = "108_tokens_resolve_base_dirty_scope.sql"
_REPAIR_FILE = "110_repair_tokens_after_base_dirty_rebuild.sql"

# TR0005 rev0's text (== git commit ccb2fe8) -- the copy step named `source_access`
# alongside the two failure_origin columns instead of leaving it out. Used only as a
# negative control below, so the reproduction is proven real rather than assumed.
_PRE_FIX_110 = """-- 110_repair_tokens_after_base_dirty_rebuild.sql
PRAGMA foreign_keys=OFF;
BEGIN;
ALTER TABLE tokens RENAME TO tokens_before_110_repair;
CREATE TABLE tokens (
 token_id TEXT PRIMARY KEY, hash TEXT NOT NULL UNIQUE, pepper_id TEXT NOT NULL,
 project TEXT NOT NULL REFERENCES projects(project_id), group_id TEXT REFERENCES groups(group_id), doc_ref TEXT,
 action_scope TEXT NOT NULL CHECK (action_scope IN ('new','edit','workflow_decide','review','test_run','workflow_sequence_edit','resolve_conflict','chat','failure_origin_review','resolve_base_dirty')),
 issued_to TEXT NOT NULL REFERENCES users(user_id), created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 consumed_at TEXT, revoked_at TEXT, scratch_dir TEXT, dry_run_count INTEGER NOT NULL DEFAULT 0,
 continuation_target_seq INTEGER, continuation_review_mode INTEGER NOT NULL DEFAULT 0, continuation_locale TEXT,
 merge_id INTEGER, continuation_instruction_mode TEXT, provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
 ai_run_id TEXT, continuation_auto_approve_item_seqs TEXT, revoke_claim TEXT,
 failure_origin_target_run_id TEXT, failure_origin_before_marker TEXT,
 source_access TEXT CHECK (source_access IN ('read','read_write'))
);
INSERT INTO tokens (token_id,hash,pepper_id,project,group_id,doc_ref,action_scope,issued_to,created_at,expires_at,consumed_at,revoked_at,scratch_dir,dry_run_count,continuation_target_seq,continuation_review_mode,continuation_locale,merge_id,continuation_instruction_mode,provider_id,ai_run_id,continuation_auto_approve_item_seqs,revoke_claim,source_access)
SELECT token_id,hash,pepper_id,project,group_id,doc_ref,action_scope,issued_to,created_at,expires_at,consumed_at,revoked_at,scratch_dir,dry_run_count,continuation_target_seq,continuation_review_mode,continuation_locale,merge_id,continuation_instruction_mode,provider_id,ai_run_id,continuation_auto_approve_item_seqs,revoke_claim,source_access FROM tokens_before_110_repair;
DROP TABLE tokens_before_110_repair;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);
COMMIT;
PRAGMA foreign_keys=ON;
"""


# ── offline: the file text itself ────────────────────────────────────────────────


def test_110_repairs_failure_origin_columns_and_scope_for_every_dialect():
    for dialect in ("sqlite", "postgres", "mysql"):
        body = (ROOT / dialect / "110_repair_tokens_after_base_dirty_rebuild.sql").read_text(encoding="utf-8")
        assert "failure_origin_target_run_id" in body
        assert "failure_origin_before_marker" in body
        assert "failure_origin_review" in body
        assert "resolve_base_dirty" in body
        assert "source_access" in body


def test_postgres_108_is_the_historical_schema_loss_and_110_is_forward_repair():
    old = (ROOT / "postgres" / "108_tokens_resolve_base_dirty_scope.sql").read_text(encoding="utf-8")
    repair = (ROOT / "postgres" / "110_repair_tokens_after_base_dirty_rebuild.sql").read_text(encoding="utf-8")
    assert "failure_origin_target_run_id" not in old
    assert "failure_origin_before_marker" not in old
    assert "failure_origin_review" not in old
    assert "ADD COLUMN IF NOT EXISTS failure_origin_target_run_id" in repair
    assert "ADD COLUMN IF NOT EXISTS failure_origin_before_marker" in repair


def test_sqlite_110_no_longer_selects_source_access_from_the_old_table():
    """Static guard against re-introducing the exact bug TR0005 fixed."""
    body = (_SQLITE_DIR / _REPAIR_FILE).read_text(encoding="utf-8")
    insert_select = body.split("INSERT INTO tokens", 1)[1].split(";", 1)[0]
    assert "source_access" not in insert_select, (
        "the copy step must not name source_access -- it does not exist on a tokens table "
        "that 108 rebuilt after 109 already added it (the ordering this group's incident had)"
    )


# ── sqlite: real DatabaseMigrator, both merge orders, proven live ─────────────────


def _ordinal(fname: str) -> int:
    return int(re.match(r"(\d+)", fname).group(1))


def _disk_sqlite_upto_110() -> list[str]:
    return sorted(p.name for p in _SQLITE_DIR.glob("*.sql") if _ordinal(p.name) <= 110)


def _seed_dir(tmp_path: Path, name: str, exclude: frozenset[str] = frozenset()) -> Path:
    d = tmp_path / name
    d.mkdir()
    for fname in _disk_sqlite_upto_110():
        if fname in exclude:
            continue
        shutil.copy(_SQLITE_DIR / fname, d / fname)
    return d


def _table_columns(db_path: Path, table: str = "tokens") -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _seed_token_row(db_path: Path, token_id: str, source_access) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
            "issued_to, created_at, expires_at, source_access) VALUES "
            "(?, ?, 'p1', 'flowgate', 'flowgate.default.0533.0005-TR', 'chat', 'u1', "
            "'2026-09-08T00:00:00', '2026-09-09T00:00:00', ?)",
            (token_id, f"h_{token_id}", source_access),
        )
        conn.commit()
    finally:
        conn.close()


def test_110_repairs_the_schema_when_108_actually_ran_after_109(tmp_path):
    """Reproduces the real deployment ledger: 109 merged & booted first, 108 merged later.

    sqloader tracks only filenames already recorded in `migrations`, so a boot that only
    has 001..107, 108_user_chat_source_access and 109 on disk applies through 109, and a
    *later* boot that adds 108_tokens_resolve_base_dirty_scope.sql applies just that file
    -- after source_access already exists -- rebuilding tokens from a column list that
    does not know about it. TR0005 found exactly this ledger (109 applied
    2026-09-08 12:46:54, 108 applied later the same day at 21:01:24) on a real deployment
    sqlite copy.
    """
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "inverted.db"
    seed = _seed_dir(tmp_path, "seed_inverted", exclude=frozenset({_INVERSION_FILE, _REPAIR_FILE}))

    # boot 1: 109 (and everything up to it) applies while 108(tokens) is still missing
    # from disk -- the state of the branch that merged 109 first.
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    assert "source_access" in _table_columns(db_path)
    _seed_token_row(db_path, "tok_before_108", "read_write")

    # boot 2: 108(tokens) merges later and is now pending; applying it rebuilds tokens
    # from a column list that predates source_access, dropping it.
    shutil.copy(_SQLITE_DIR / _INVERSION_FILE, seed / _INVERSION_FILE)
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    assert "source_access" not in _table_columns(db_path), (
        "108's rebuild should have dropped source_access here -- if this fails, the "
        "fixture no longer reproduces the ledger TR0005 found and the test below is not "
        "proving what it claims to prove"
    )

    # boot 3: 110 must repair this without raising "no such column: source_access".
    shutil.copy(_SQLITE_DIR / _REPAIR_FILE, seed / _REPAIR_FILE)
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)

    cols = _table_columns(db_path)
    assert {"source_access", "failure_origin_target_run_id", "failure_origin_before_marker"} <= cols

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        assert set(_disk_sqlite_upto_110()) <= applied

        row = conn.execute(
            "SELECT source_access, action_scope FROM tokens WHERE token_id = 'tok_before_108'"
        ).fetchone()
        assert row is not None, "the pre-existing row must survive the rebuild"
        # TR0005's documented one-time NULL reset (not silent corruption of an unrelated
        # column): source_access resets, action_scope (never excluded from the copy) does not.
        assert row[0] is None
        assert row[1] == "chat"

        # the widened CHECK 110 installs must accept both new scopes.
        conn.execute(
            "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
            "issued_to, created_at, expires_at) VALUES "
            "('tok_new_scope', 'h_new', 'p1', 'flowgate', 'flowgate.default.0533.0006-TS', "
            "'failure_origin_review', 'u1', '2026-09-10T00:00:00', '2026-09-11T00:00:00')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
                "issued_to, created_at, expires_at) VALUES "
                "('tok_bad_scope', 'h_bad', 'p1', 'flowgate', 'flowgate.default.0533.0006-TS', "
                "'not_a_real_scope', 'u1', '2026-09-10T00:00:00', '2026-09-11T00:00:00')"
            )
    finally:
        conn.close()


def test_110_repairs_the_schema_when_108_ran_before_109_the_ordinary_order(tmp_path):
    """Control: the ordinary merge order (108 already on disk before 109 ever runs) must
    also boot clean through 110 -- the repair must not depend on which order actually
    happened, only on the schema it finds.
    """
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "ordinary.db"
    seed = _seed_dir(tmp_path, "seed_ordinary")  # every file up to 110 present from boot 1

    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)

    cols = _table_columns(db_path)
    assert {"source_access", "failure_origin_target_run_id", "failure_origin_before_marker"} <= cols
    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        assert set(_disk_sqlite_upto_110()) <= applied
    finally:
        conn.close()


def test_the_pre_fix_110_reproduces_the_rejected_boot_without_the_fix(tmp_path):
    """Negative control: TR0005 rev0's 110 (source_access left in the copy) really does
    raise "no such column: source_access" against the inverted-order ledger the two tests
    above build -- proof the fixture reproduces a real failure, not an imagined one, and
    that TC-4's "order-independent recovery" claim is not accidental.
    """
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "inverted_prefix.db"
    seed = _seed_dir(tmp_path, "seed_inverted_prefix", exclude=frozenset({_INVERSION_FILE, _REPAIR_FILE}))
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    shutil.copy(_SQLITE_DIR / _INVERSION_FILE, seed / _INVERSION_FILE)
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    assert "source_access" not in _table_columns(db_path)

    (seed / _REPAIR_FILE).write_text(_PRE_FIX_110, encoding="utf-8")
    with pytest.raises(Exception) as excinfo:
        DatabaseMigrator(SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    assert "no such column: source_access" in str(excinfo.value), excinfo.value


# ── postgres: real server, opt-in (FLOWGATE_PG_TEST_DSN) ──────────────────────────
#
# Same opt-in convention as test_migration_107_postgres_schema_drift_0539.py: SKIP unless
# a live server is configured, and run inside a SAVEPOINT that is always rolled back so a
# run against staging leaves no trace.

PG_DSN_ENV = "FLOWGATE_PG_TEST_DSN"
PG_DSN = os.environ.get(PG_DSN_ENV)

try:
    import psycopg2
except ImportError:
    psycopg2 = None

if psycopg2 is None:
    _SKIP_PG = "psycopg2 is not installed; the live PostgreSQL check cannot run here"
elif not PG_DSN:
    _SKIP_PG = f"{PG_DSN_ENV} is not set — this is the opt-in live PostgreSQL check"
else:
    _SKIP_PG = None

live_postgres = pytest.mark.skipif(_SKIP_PG is not None, reason=_SKIP_PG or "")

_PG_110 = (ROOT / "postgres" / _REPAIR_FILE).read_text(encoding="utf-8")


@pytest.mark.postgres
@live_postgres
class TestLivePostgres110Repair:
    @pytest.fixture(scope="class")
    def pg_conn(self):
        conn = psycopg2.connect(PG_DSN)
        conn.autocommit = False
        yield conn
        conn.rollback()
        conn.close()

    @pytest.fixture
    def pg_case(self, pg_conn):
        cur = pg_conn.cursor()
        schema = f"fg_test_0550_{uuid.uuid4().hex[:10]}"
        cur.execute("SAVEPOINT fg_case")
        try:
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"SET LOCAL search_path TO {schema}")
            yield cur, schema
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT fg_case")
            cur.close()

    def test_110_restores_columns_and_scope_after_108s_historical_loss(self, pg_case):
        """Shape 108 actually leaves behind: no failure_origin_*/source_access columns,
        and no action_scope CHECK naming the two scopes 110 must admit.
        """
        cur, schema = pg_case
        cur.execute(
            "CREATE TABLE tokens (token_id TEXT PRIMARY KEY, action_scope TEXT NOT NULL)"
        )
        cur.execute(
            "INSERT INTO tokens (token_id, action_scope) VALUES ('t_old', 'chat')"
        )

        cur.execute(f"SET LOCAL search_path TO {schema}")
        cur.execute(_PG_110)

        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'tokens'",
            (schema,),
        )
        cols = {row[0] for row in cur.fetchall()}
        assert {"source_access", "failure_origin_target_run_id", "failure_origin_before_marker"} <= cols

        cur.execute(
            "SELECT source_access FROM tokens WHERE token_id = 't_old'"
        )
        assert cur.fetchone()[0] is None

        cur.execute(f"SET LOCAL search_path TO {schema}")
        cur.execute("SAVEPOINT accept")
        cur.execute(
            "INSERT INTO tokens (token_id, action_scope) VALUES ('t_ok', 'resolve_base_dirty')"
        )
        cur.execute("ROLLBACK TO SAVEPOINT accept")

        cur.execute("SAVEPOINT reject")
        with pytest.raises(psycopg2.Error):
            cur.execute(
                "INSERT INTO tokens (token_id, action_scope) VALUES ('t_bad', 'not_a_real_scope')"
            )
        cur.execute("ROLLBACK TO SAVEPOINT reject")


# ── mysql: real server, opt-in (FLOWGATE_MYSQL_TEST_DSN) ──────────────────────────
#
# No SAVEPOINT escape hatch -- MySQL DDL is not transactional -- so each case gets its
# own throwaway database, dropped in a finally block instead.

MYSQL_DSN_ENV = "FLOWGATE_MYSQL_TEST_DSN"
MYSQL_DSN = os.environ.get(MYSQL_DSN_ENV)

try:
    import pymysql
except ImportError:
    pymysql = None

if pymysql is None:
    _SKIP_MYSQL = "pymysql is not installed; the live MySQL check cannot run here"
elif not MYSQL_DSN:
    _SKIP_MYSQL = f"{MYSQL_DSN_ENV} is not set — this is the opt-in live MySQL check"
else:
    _SKIP_MYSQL = None

live_mysql = pytest.mark.skipif(_SKIP_MYSQL is not None, reason=_SKIP_MYSQL or "")

_MYSQL_110 = (ROOT / "mysql" / _REPAIR_FILE).read_text(encoding="utf-8")


def _parse_mysql_dsn(dsn: str) -> dict:
    """`mysql://user:pass@host:port/dbname` -> pymysql.connect() kwargs."""
    from urllib.parse import urlparse

    u = urlparse(dsn)
    return {
        "host": u.hostname or "127.0.0.1",
        "port": u.port or 3306,
        "user": u.username,
        "password": u.password or "",
        "database": (u.path or "/").lstrip("/") or None,
    }


@pytest.mark.mysql
@live_mysql
class TestLiveMysql110Repair:
    def test_110_restores_columns_and_scope_after_108s_historical_loss(self):
        kwargs = _parse_mysql_dsn(MYSQL_DSN)
        admin_conn = pymysql.connect(autocommit=True, **kwargs)
        db_name = f"fg_test_0550_{uuid.uuid4().hex[:10]}"
        try:
            with admin_conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE `{db_name}`")
        finally:
            admin_conn.close()

        case_kwargs = dict(kwargs)
        case_kwargs["database"] = db_name
        conn = pymysql.connect(autocommit=True, **case_kwargs)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE tokens (token_id VARCHAR(191) PRIMARY KEY, "
                    "action_scope TEXT NOT NULL)"
                )
                cur.execute("INSERT INTO tokens (token_id, action_scope) VALUES ('t_old', 'chat')")

                for statement in _MYSQL_110.split(";"):
                    statement = statement.strip()
                    if statement:
                        cur.execute(statement)

                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'tokens'",
                    (db_name,),
                )
                cols = {row[0] for row in cur.fetchall()}
                assert {
                    "source_access",
                    "failure_origin_target_run_id",
                    "failure_origin_before_marker",
                } <= cols

                cur.execute("SELECT source_access FROM tokens WHERE token_id = 't_old'")
                assert cur.fetchone()[0] is None

                cur.execute(
                    "INSERT INTO tokens (token_id, action_scope) VALUES "
                    "('t_ok', 'failure_origin_review')"
                )
        finally:
            conn.close()
            admin_conn = pymysql.connect(autocommit=True, **kwargs)
            try:
                with admin_conn.cursor() as cur:
                    cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
            finally:
                admin_conn.close()
