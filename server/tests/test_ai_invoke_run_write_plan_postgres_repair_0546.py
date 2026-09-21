"""ai_invoke_runs.write_requested_by_human / allow_test_edits: PostgreSQL repair +
real round-trip regression (flowgate.default.0546 T0004, NR0003).

Migration 105 declared these two columns BOOLEAN on PostgreSQL only (sqlite got
INTEGER, mysql got TINYINT(1)), while the writer (db/ai_invoke_runs.py upsert())
binds every boolean-shaped column on this table -- including these two -- as
None/0/1, one statement across all three dialects. PostgreSQL will not coerce an
integer into a boolean column, so a resolve_conflict run (the only action_scope
that ever puts a non-NULL value in either column) failed terminal persist with::

    psycopg2.errors.DatatypeMismatch: column "write_requested_by_human" is of type
    boolean but expression is of type integer

115_ai_invoke_run_write_plan_bool_repair.sql converges the live column back to the
table's existing nullable-INTEGER-0/1 contract. There is no PostgreSQL in the
ordinary test environment, so the live classes below SKIP unless FLOWGATE_PG_TEST_DSN
is set -- same opt-in convention as test_review_postgres_integration_0535.py and
test_migration_107_postgres_schema_drift_0539.py, which this file follows closely.

Run it against a database that already has migration 105 applied::

    # PowerShell, from server/
    $env:FLOWGATE_PG_TEST_DSN = "postgresql://flowgate:<password>@192.168.0.250:5432/flowgate"
    python -m pytest tests/test_ai_invoke_run_write_plan_postgres_repair_0546.py -v -rs

Every live test works inside its own throwaway schema (via search_path) wrapped in one
SAVEPOINT that is always rolled back, so a run against the staging database leaves no
trace behind (0539's convention). Tier 1 below proves the migration text converges a
minimal boolean-holding surrogate table. Tier 2 rebuilds the REAL ai_invoke_runs column
set by replaying the actual migration files that shaped it (076b through 105, then 115
under test) inside that same throwaway schema, then exercises the real
db.ai_invoke_runs.upsert()/get() and finalize._persist_run_record() -- not a
reimplementation of their SQL -- against it.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

_POSTGRES_DIR = _SERVER_DIR / "sql" / "migrations" / "postgres"
_SQLITE_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_MYSQL_DIR = _SERVER_DIR / "sql" / "migrations" / "mysql"
_MIGRATION_115 = _POSTGRES_DIR / "115_ai_invoke_run_write_plan_bool_repair.sql"

DSN_ENV = "FLOWGATE_PG_TEST_DSN"
DSN = os.environ.get(DSN_ENV)

try:
    import psycopg2
except ImportError:
    psycopg2 = None

if psycopg2 is None:
    _SKIP_LIVE = "psycopg2 is not installed; the live PostgreSQL check cannot run here"
elif not DSN:
    _SKIP_LIVE = (
        f"{DSN_ENV} is not set -- this is the opt-in live PostgreSQL check "
        "(see the module docstring for how to run it)"
    )
else:
    _SKIP_LIVE = None

live_postgres = pytest.mark.skipif(_SKIP_LIVE is not None, reason=_SKIP_LIVE or "")


# --- offline: the migration text itself must not regress ---------------------------


def test_the_repair_declares_integer_and_never_reintroduces_boolean():
    body = _MIGRATION_115.read_text(encoding="utf-8")
    assert "write_requested_by_human TYPE INTEGER" in body
    assert "allow_test_edits TYPE INTEGER" in body
    assert "write_requested_by_human BOOLEAN" not in body, (
        "the repair must not redeclare the column as BOOLEAN"
    )
    assert "allow_test_edits BOOLEAN" not in body, (
        "the repair must not redeclare the column as BOOLEAN"
    )


def test_the_repair_maps_null_true_false_to_null_1_0_in_the_using_clause():
    """The USING clause must preserve existing data as NULL/1/0 for NULL/true/false --
    the live test below (test_boolean_columns_converge_to_checked_nullable_integers)
    proves this against real PostgreSQL; this offline check pins the same mapping in
    the migration text itself."""
    body = _MIGRATION_115.read_text(encoding="utf-8")
    assert body.count("CASE") >= 2
    assert body.count("IS NULL THEN NULL") >= 2
    assert "THEN 1" in body
    assert "ELSE 0" in body


def test_the_repair_adds_a_nullable_0_1_check_for_both_columns():
    body = _MIGRATION_115.read_text(encoding="utf-8")
    assert "write_requested_by_human IS NULL OR write_requested_by_human IN (0, 1)" in body
    assert "allow_test_edits IS NULL OR allow_test_edits IN (0, 1)" in body


def test_105_itself_is_left_untouched():
    """105 is already applied on the live database; rewriting it would not change the
    column type sqloader already created there (NR0003 Sec.12). The fix must be a new,
    forward-only file."""
    body = (_POSTGRES_DIR / "105_ai_invoke_run_write_plan.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS write_requested_by_human BOOLEAN" in body
    assert "ADD COLUMN IF NOT EXISTS allow_test_edits BOOLEAN" in body


@pytest.mark.parametrize("dialect_dir", [_SQLITE_DIR, _MYSQL_DIR])
def test_the_sibling_dialect_115_file_exists_and_changes_nothing(dialect_dir):
    """sqlite/mysql's own 105 already used INTEGER/TINYINT(1) -- nothing to converge --
    but test_migration_numbering.py requires the same filename set on every dialect."""
    path = dialect_dir / "115_ai_invoke_run_write_plan_bool_repair.sql"
    assert path.is_file(), f"{path} is missing"
    assert "ALTER TABLE" not in path.read_text(encoding="utf-8")


# --- live: proven against real PostgreSQL -------------------------------------------


def _stub_fk_tables(cur) -> None:
    """The tables ai_invoke_runs FKs to -- just enough for the REFERENCES in 076b/086b
    to resolve; referential behavior itself is not under test here."""
    cur.execute("CREATE TABLE groups (group_id TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE ai_providers (provider_id TEXT PRIMARY KEY)")
    cur.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")


_TXN_CONTROL_LINE = re.compile(r"^\s*(BEGIN|COMMIT)\s*;\s*$", re.MULTILINE)


def _strip_txn_control(sql_text: str) -> str:
    """Several of the replayed files (080a/086c/095/101/105) wrap their own body in
    BEGIN;/COMMIT; -- fine for sqloader applying them standalone, but fatal for this
    fixture's own SAVEPOINT-per-test isolation: a bare COMMIT ends the *outer*
    transaction the SAVEPOINT lives in, discarding search_path along with it, so every
    later unqualified relation in the sequence (e.g. 086b's `ai_invoke_paused_chains`)
    resolves against the real `public` schema instead of the throwaway one -- and
    whatever ran before the COMMIT is now permanently applied to the live database
    instead of rolled back. Stripping these lines keeps the whole sequence inside the
    one transaction `schema_case` already manages."""
    return _TXN_CONTROL_LINE.sub("", sql_text)


def _ai_invoke_runs_ddl_sequence() -> list[str]:
    """The real migration statements that shaped ai_invoke_runs, in application order,
    read live off disk (not re-typed here) so this stays honest if any of them change.

    076b also touches ai_invoke_paused_chains, which this throwaway schema does not
    have -- only its ai_invoke_runs CREATE TABLE + indexes are kept.
    """
    text_076b = (_POSTGRES_DIR / "076b_ai_invoke_runs.sql").read_text(encoding="utf-8")
    creation_only = text_076b[: text_076b.index("ALTER TABLE ai_invoke_paused_chains")]

    text_086b = (_POSTGRES_DIR / "086b_ai_invoke_restart_max_attempts.sql").read_text(encoding="utf-8")
    text_086b = text_086b.replace(
        "ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_restart_max_attempts INTEGER;\n",
        "",
    )

    names = [
        "086c_ai_invoke_run_diagnostics.sql",
        "094_ai_invoke_provider_selection.sql",
        "095_ai_invoke_run_transport_diagnostics.sql",
        "096_ai_invoke_api_turn_trace.sql",
        "101_ai_invoke_run_transport_fallback_diagnostics.sql",
        "105_ai_invoke_run_write_plan.sql",
    ]
    text_080a = (_POSTGRES_DIR / "080a_ai_invoke_prompt_audit.sql").read_text(encoding="utf-8")
    rest = [(_POSTGRES_DIR / name).read_text(encoding="utf-8") for name in names]
    blocks = [creation_only, text_080a, text_086b, *rest, _MIGRATION_115.read_text(encoding="utf-8")]
    return [_strip_txn_control(block) for block in blocks]


def _base_run(run_id: str, project_id: str, group_id: str) -> dict:
    now = "2026-09-20T00:00:00+09:00"
    return {
        "run_id": run_id,
        "group_id": group_id,
        "project_id": project_id,
        "doc_ref": "flowgate.default.0546.0001-B",
        "mode": "single",
        "started_at": now,
        "finished_at": now,
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.postgres
@live_postgres
class TestLivePostgresRepairAndRoundTrip:
    """Everything here needs FLOWGATE_PG_TEST_DSN; see the module docstring."""

    @pytest.fixture(scope="class")
    def pg_conn(self):
        conn = psycopg2.connect(DSN)
        conn.autocommit = False
        yield conn
        conn.rollback()
        conn.close()

    @pytest.fixture
    def schema_case(self, pg_conn):
        """A throwaway schema, wrapped in one SAVEPOINT that is always rolled back --
        the convention test_migration_107_postgres_schema_drift_0539.py established."""
        cur = pg_conn.cursor()
        schema = f"fg_test_0546_{uuid.uuid4().hex[:10]}"
        cur.execute("SAVEPOINT fg_case")
        try:
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"SET LOCAL search_path TO {schema}")
            yield cur, schema
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT fg_case")
            cur.close()

    # --- Tier 1: schema convergence on a minimal boolean-holding surrogate ----------

    def test_boolean_columns_converge_to_checked_nullable_integers(self, schema_case):
        cur, schema = schema_case
        cur.execute(
            "CREATE TABLE ai_invoke_runs (run_id TEXT PRIMARY KEY, "
            "write_requested_by_human BOOLEAN, allow_test_edits BOOLEAN)"
        )
        cur.execute(
            "INSERT INTO ai_invoke_runs VALUES "
            "('r_null', NULL, NULL), ('r_false', false, false), ('r_true', true, true)"
        )

        cur.execute(_MIGRATION_115.read_text(encoding="utf-8"))

        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'ai_invoke_runs' "
            "AND column_name IN ('write_requested_by_human', 'allow_test_edits')",
            (schema,),
        )
        types = {row[0]: row[1] for row in cur.fetchall()}
        assert types == {
            "write_requested_by_human": "integer",
            "allow_test_edits": "integer",
        }

        cur.execute(
            "SELECT run_id, write_requested_by_human, allow_test_edits "
            "FROM ai_invoke_runs ORDER BY run_id"
        )
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        assert rows == {
            "r_false": (0, 0),
            "r_null": (None, None),
            "r_true": (1, 1),
        }, "existing false/true/NULL data must survive the repair as 0/1/NULL"

        # The exact bug report: an integer 0/1 bind must be accepted post-repair.
        cur.execute("SAVEPOINT accept")
        cur.execute(
            "INSERT INTO ai_invoke_runs (run_id, write_requested_by_human, allow_test_edits) "
            "VALUES ('r_accept', %s, %s)", (1, 0),
        )
        cur.execute("ROLLBACK TO SAVEPOINT accept")

        # And the new CHECK must reject anything outside NULL/0/1.
        cur.execute("SAVEPOINT reject")
        with pytest.raises(psycopg2.Error):
            cur.execute(
                "INSERT INTO ai_invoke_runs (run_id, write_requested_by_human, allow_test_edits) "
                "VALUES ('r_reject', %s, %s)", (2, 0),
            )
        cur.execute("ROLLBACK TO SAVEPOINT reject")

    def test_a_run_that_never_touched_either_column_stays_null(self, schema_case):
        """The overwhelming majority of runs (every non-resolve_conflict action_scope,
        NR0003 Sec.6): both columns NULL before the repair, both still NULL after."""
        cur, schema = schema_case
        cur.execute(
            "CREATE TABLE ai_invoke_runs (run_id TEXT PRIMARY KEY, "
            "write_requested_by_human BOOLEAN, allow_test_edits BOOLEAN)"
        )
        cur.execute("INSERT INTO ai_invoke_runs VALUES ('r1', NULL, NULL)")

        cur.execute(_MIGRATION_115.read_text(encoding="utf-8"))  # must not raise

        cur.execute("SELECT write_requested_by_human, allow_test_edits FROM ai_invoke_runs")
        assert cur.fetchone() == (None, None)

    def test_the_repair_is_idempotent_against_a_manual_rerun(self, schema_case):
        """Not exercised by sqloader itself (each migration applies once, per the
        `migrations` ledger), but the file's own guard comment promises this."""
        cur, schema = schema_case
        cur.execute(
            "CREATE TABLE ai_invoke_runs (run_id TEXT PRIMARY KEY, "
            "write_requested_by_human BOOLEAN, allow_test_edits BOOLEAN)"
        )
        cur.execute("INSERT INTO ai_invoke_runs VALUES ('r1', true, false)")
        cur.execute(_MIGRATION_115.read_text(encoding="utf-8"))

        cur.execute(_MIGRATION_115.read_text(encoding="utf-8"))  # second application

        cur.execute("SELECT write_requested_by_human, allow_test_edits FROM ai_invoke_runs")
        assert cur.fetchone() == (1, 0)

    # --- Tier 2: the real writer, against the real column set -----------------------

    @pytest.fixture
    def real_shape_env(self, schema_case, monkeypatch):
        """Rebuilds the actual ai_invoke_runs column set (076b..105, then 115 under
        test) in the throwaway schema, then points the real db/ai_invoke_runs.py module
        at this connection -- the same monkeypatch shape as
        test_review_postgres_integration_0535.py's `pg_env` fixture."""
        cur, schema = schema_case
        _stub_fk_tables(cur)
        suffix = uuid.uuid4().hex[:10]
        project_id, group_id = f"proj_{suffix}", f"grp_{suffix}"
        cur.execute("INSERT INTO projects (project_id) VALUES (%s)", (project_id,))
        cur.execute("INSERT INTO groups (group_id) VALUES (%s)", (group_id,))

        for statement_block in _ai_invoke_runs_ddl_sequence():
            cur.execute(statement_block)

        from modules.flow_gate.db import connection as db_connection
        from modules.flow_gate.db import dialect as _dialect

        class _PgDB:
            db_type = _dialect.POSTGRESQL

            def __init__(self, conn):
                self.conn = conn

            def _cursor(self):
                from psycopg2.extras import RealDictCursor
                return self.conn.cursor(cursor_factory=RealDictCursor)

            def execute(self, sql, params=None):
                cursor = self._cursor()
                cursor.execute(sql, list(params or []) or None)
                return cursor

            def commit(self):
                pass  # never actually committed; rolled back with the rest of fg_case

            def fetch_one(self, sql, params=None):
                cursor = self.execute(sql, params)
                row = cursor.fetchone()
                cursor.close()
                return dict(row) if row else None

            def fetch_all(self, sql, params=None):
                cursor = self.execute(sql, params)
                rows = [dict(r) for r in cursor.fetchall()]
                cursor.close()
                return rows

        store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
        store._db, store._sq = _PgDB(cur.connection), None
        monkeypatch.setattr(db_connection, "STORE", store)

        from modules.flow_gate.db import ai_invoke_runs as db_runs

        yield db_runs, suffix, _base_run(f"air_{suffix}", project_id, group_id), cur

    @pytest.mark.parametrize("write_requested, allow_edits", [
        (None, None), (False, False), (True, True),
    ])
    def test_upsert_and_get_round_trip_on_real_postgres(
        self, real_shape_env, write_requested, allow_edits,
    ):
        db_runs, suffix, base_row, cur = real_shape_env
        run_id = f"{base_row['run_id']}_{write_requested}_{allow_edits}"
        row = dict(base_row, run_id=run_id,
                   write_requested_by_human=write_requested, allow_test_edits=allow_edits)

        db_runs.upsert(row)

        cur.execute(
            "SELECT write_requested_by_human, allow_test_edits FROM ai_invoke_runs "
            "WHERE run_id = %s", (run_id,),
        )
        expected_raw = {None: None, False: 0, True: 1}
        assert cur.fetchone() == (
            expected_raw[write_requested], expected_raw[allow_edits],
        ), "PostgreSQL must have stored the 0/1/NULL contract, not rejected the bind"

        fetched = db_runs.get(run_id)
        assert fetched["write_requested_by_human"] is write_requested
        assert fetched["allow_test_edits"] is allow_edits

    def test_persist_run_record_succeeds_for_the_reported_resolve_conflict_shape(
        self, real_shape_env,
    ):
        """The exact combination from the bug report (T doc Sec.9.4/Sec.13):
        write_requested_by_human=False, allow_test_edits=False -- False is the branch
        that raised DatatypeMismatch (True also would; NULL never did)."""
        db_runs, suffix, base_row, cur = real_shape_env
        from modules.flow_gate.services.ai_invoke import finalize as ai_finalize

        run = dict(base_row, run_id=f"{base_row['run_id']}_persist",
                   write_requested_by_human=False, allow_test_edits=False)

        ok = ai_finalize._persist_run_record(run)

        assert ok is True, "terminal cleanup step persist must not fail post-repair"
        stored = db_runs.get(run["run_id"])
        assert stored is not None
        assert stored["write_requested_by_human"] is False
        assert stored["allow_test_edits"] is False
