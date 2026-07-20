"""0273 — the install-time bootstrap must work on every supported engine.

NR0003 P1-1: create_dev_user.py and check_db_ready.py opened the DB with
`import sqlite3`, so all three install paths skipped the admin bootstrap when
DB_TYPE was mysql or postgres. A `DB_TYPE=postgres` install therefore ran to
completion with zero accounts able to log in.

These cover the dialect decisions in db_bootstrap.py — the part that is pure
logic and can be asserted without a live MySQL/PostgreSQL server. The against-a-
real-server runs are recorded in the task report.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]


def _load(module_name: str):
    """Import a top-level server script by path (they are not a package)."""
    if str(_SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(_SERVER_DIR))
    spec = importlib.util.spec_from_file_location(module_name, _SERVER_DIR / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dbb = _load("db_bootstrap")


# ── engine resolution ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("sqlite", dbb.SQLITE), ("sqlite3", dbb.SQLITE), ("local", dbb.SQLITE),
    ("", dbb.SQLITE),          # unset DB_TYPE means the file-backed default
    ("SQLite3", dbb.SQLITE),   # config.py's enum is lowercase; be forgiving
    ("mysql", dbb.MYSQL), ("mariadb", dbb.MYSQL),
    ("postgres", dbb.POSTGRES), ("postgresql", dbb.POSTGRES),
])
def test_resolve_db_type_collapses_aliases(raw, expected):
    assert dbb.resolve_db_type(raw) == expected


def test_resolve_db_type_rejects_unknown_engine():
    """A typo must name itself, not fall through to a silent default."""
    with pytest.raises(dbb.BootstrapDBError) as exc:
        dbb.resolve_db_type("oracle")
    assert "oracle" in str(exc.value)


# ── placeholder translation ──────────────────────────────────────────────────

def test_placeholders_untouched_for_sqlite():
    sql = "SELECT 1 FROM users WHERE email = ? AND username = ?"
    assert dbb.q(sql, dbb.SQLITE) == sql


@pytest.mark.parametrize("db_type", [dbb.MYSQL, dbb.POSTGRES])
def test_placeholders_become_pyformat_for_networked_engines(db_type):
    got = dbb.q("SELECT 1 FROM users WHERE email = ? AND username = ?", db_type)
    assert got == "SELECT 1 FROM users WHERE email = %s AND username = %s"


@pytest.mark.parametrize("db_type", [dbb.MYSQL, dbb.POSTGRES])
def test_question_mark_inside_a_string_literal_is_not_a_placeholder(db_type):
    """Rewriting one would corrupt the literal and shift every later parameter."""
    got = dbb.q("SELECT 1 FROM t WHERE a = ? AND label = 'why?'", db_type)
    assert got == "SELECT 1 FROM t WHERE a = %s AND label = 'why?'"


# ── upsert dialects ──────────────────────────────────────────────────────────

class _RecordingConn:
    """Captures the SQL a helper would execute, without touching a database."""

    def __init__(self):
        self.statements = []

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.statements.append((sql, params))


def test_role_upsert_uses_each_engines_own_spelling():
    params = ("u1", "__SYSTEM__", "role_admin", "2026-07-19")
    got = {}
    for db_type in (dbb.SQLITE, dbb.MYSQL, dbb.POSTGRES):
        conn = _RecordingConn()
        dbb.upsert_user_project_role(conn, db_type, params)
        (sql, sent), = conn.statements
        got[db_type] = sql
        assert sent == params

    # INSERT OR REPLACE is SQLite-only — the other two reject it outright.
    assert "INSERT OR REPLACE" in got[dbb.SQLITE]
    assert "ON DUPLICATE KEY UPDATE" in got[dbb.MYSQL]
    assert "ON CONFLICT (user_id, project_id) DO UPDATE" in got[dbb.POSTGRES]
    for db_type in (dbb.MYSQL, dbb.POSTGRES):
        assert "INSERT OR REPLACE" not in got[db_type]
        assert "?" not in got[db_type], "placeholders must be translated"


def test_role_aggregate_uses_string_agg_only_on_postgres():
    """PostgreSQL has no GROUP_CONCAT; MySQL/SQLite have no STRING_AGG."""
    assert dbb.role_aggregate(dbb.POSTGRES) == "STRING_AGG(upr.role_id, ',')"
    assert dbb.role_aggregate(dbb.MYSQL) == "GROUP_CONCAT(upr.role_id)"
    assert dbb.role_aggregate(dbb.SQLITE) == "GROUP_CONCAT(upr.role_id)"


# ── migration set selection ──────────────────────────────────────────────────

def test_each_engine_selects_its_own_migration_directory():
    """config.py hands the migrator sql/migrations/<engine>; readiness must match."""
    for db_type, name in ((dbb.SQLITE, "sqlite"), (dbb.MYSQL, "mysql"), (dbb.POSTGRES, "postgres")):
        assert dbb.migrations_dirname(db_type) == name
        assert (_SERVER_DIR / "sql" / "migrations" / name).is_dir()


# ── connection guards ────────────────────────────────────────────────────────

def test_sqlite_connect_reports_a_missing_file_instead_of_creating_it(tmp_path, monkeypatch):
    """The readiness probe must never manufacture the empty DB it checks for."""
    absent = tmp_path / "absent.db"
    monkeypatch.setenv("DB_PATH", str(absent))
    with pytest.raises(dbb.BootstrapDBError):
        dbb.connect(dbb.SQLITE, str(tmp_path), readonly=True)
    assert not absent.exists()


def test_networked_connect_requires_a_database_name(monkeypatch):
    monkeypatch.delenv("DB_DATABASE", raising=False)
    for db_type in (dbb.MYSQL, dbb.POSTGRES):
        with pytest.raises(dbb.BootstrapDBError) as exc:
            dbb.connect(db_type, ".")
        assert "DB_DATABASE" in str(exc.value)


def test_non_numeric_port_is_reported_by_name(monkeypatch):
    monkeypatch.setenv("DB_DATABASE", "flowgate")
    monkeypatch.setenv("DB_PORT", "not-a-port")
    with pytest.raises(dbb.BootstrapDBError) as exc:
        dbb.connect(dbb.POSTGRES, ".")
    assert "DB_PORT" in str(exc.value)


# ── row access ───────────────────────────────────────────────────────────────

def test_rows_returns_plain_dicts_across_drivers():
    """Callers index rows by column name; sqlite3.Row and the DB-API tuples differ."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (a TEXT, b TEXT)")
    conn.execute("INSERT INTO t VALUES ('1', '2')")
    cur = dbb.execute(conn, dbb.SQLITE, "SELECT a, b FROM t")
    assert dbb.rows(cur) == [{"a": "1", "b": "2"}]


def test_one_returns_none_on_empty_result():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (a TEXT)")
    assert dbb.one(dbb.execute(conn, dbb.SQLITE, "SELECT a FROM t")) is None
