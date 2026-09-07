"""PostgreSQL migration 107 must converge schema drift, not assume it (flowgate.default.0539).

The rejected boot log:

    Database Migration Failed.Failed to apply migration 107_tokens_failure_origin_review_scope.sql:
    constraint "tokens_action_scope_check" of relation "tokens" does not exist

NR0003 traced this to the PostgreSQL-only statement

    ALTER TABLE tokens DROP CONSTRAINT tokens_action_scope_check;

which has no `IF EXISTS` and hardcodes one legacy constraint name. T0004 replaces it with a
catalog scan keyed on the `action_scope` COLUMN (via `pg_constraint.conkey`), not the
constraint's name or its text, so it survives every schema-drift history NR0003 lists:

    Case A  the canonical name already exists (the lineage everyone expects)
    Case B  no action_scope CHECK exists at all
    Case C  the CHECK exists under a different name
    Case D  an unrelated CHECK on another column must not be touched

There is no PostgreSQL in the ordinary test environment (see
test_review_postgres_integration_0535.py, the existing opt-in precedent this file follows).
The live cases below SKIP unless FLOWGATE_PG_TEST_DSN is set. Each one runs inside a SAVEPOINT
in its own dedicated schema and is rolled back in a fixture finalizer, so a run against the
staging database leaves no trace behind (same convention TR0006 used).

Case E (the 104 -> 107 rename ledger carry-over) is dialect-agnostic bookkeeping untouched by
this fix; it is already covered generically for every entry in `migration_renames.RENAMES` --
including this group's 102->105/103->106/104->107 -- by
`TestFilenameCarryOver.test_an_already_migrated_db_gets_every_name_carried_over` and
`test_every_sqlite_migration_applies_in_name_order` in test_migration_numbering.py.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

MIGRATION_PATH = (
    _SERVER_DIR / "sql" / "migrations" / "postgres"
    / "107_tokens_failure_origin_review_scope.sql"
)

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
        f"{DSN_ENV} is not set — this is the opt-in live PostgreSQL check "
        "(see the module docstring for how to run it)"
    )
else:
    _SKIP_LIVE = None

# Applied to the live-PostgreSQL class only (below) -- the two offline text guards
# just above it must keep running even when there is no database to reach.
live_postgres = pytest.mark.skipif(_SKIP_LIVE is not None, reason=_SKIP_LIVE or "")


# ── offline: the file text itself must not regress to a bare named DROP ────────────


def test_the_migration_no_longer_hardcodes_the_legacy_constraint_name():
    """Static guard against re-introducing the exact bug this group fixed.

    A bare ``DROP CONSTRAINT tokens_action_scope_check`` (no ``IF EXISTS``, no dynamic
    discovery) is exactly what raised ``constraint ... does not exist`` on a schema-drifted
    database. This does not replace the live Cases below -- it just fails fast, without a
    database, the moment someone reverts to the old one-liner.
    """
    body = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT tokens_action_scope_check" not in body, (
        "the fix must discover the legacy CHECK dynamically (by column, via pg_constraint/"
        "conkey), not hardcode the one name that broke on a schema-drifted database"
    )
    assert "failure_origin_target_run_id" in body
    assert "failure_origin_before_marker" in body


def test_the_migration_keys_discovery_on_the_column_not_a_name_substring():
    """`pg_get_constraintdef(...) LIKE '%action_scope%'` would also match an unrelated CHECK
    that merely mentions the column name in a comment or a compound expression involving a
    different column. Discovery must join through `conkey`/`pg_attribute` to the real column.
    """
    body = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "conkey" in body, "constraint discovery must resolve the actual constrained column"


# ── live: schema-drift convergence, proven against real PostgreSQL ─────────────────


def _constraints(cur, schema):
    cur.execute(
        """
        SELECT c.conname, pg_get_constraintdef(c.oid) AS def
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = %s AND t.relname = 'tokens' AND c.contype = 'c'
        ORDER BY c.conname
        """,
        (schema,),
    )
    return cur.fetchall()


def _columns(cur, schema):
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = %s AND table_name = 'tokens'
        ORDER BY column_name
        """,
        (schema,),
    )
    return [row[0] for row in cur.fetchall()]


def _apply_107(cur, schema):
    cur.execute(f"SET LOCAL search_path TO {schema}")
    cur.execute(MIGRATION_PATH.read_text(encoding="utf-8"))


def _assert_converged(cur, schema):
    names = {name for name, _def in _constraints(cur, schema)}
    assert "tokens_action_scope_check" in names
    cols = _columns(cur, schema)
    assert "failure_origin_target_run_id" in cols
    assert "failure_origin_before_marker" in cols

    cur.execute(f"SET LOCAL search_path TO {schema}")
    cur.execute("SAVEPOINT accept")
    cur.execute(
        "INSERT INTO tokens (token_id, action_scope) VALUES ('t_ok', 'failure_origin_review')"
    )
    cur.execute("ROLLBACK TO SAVEPOINT accept")

    cur.execute("SAVEPOINT reject")
    with pytest.raises(psycopg2.Error):
        cur.execute(
            "INSERT INTO tokens (token_id, action_scope) VALUES ('t_bad', 'not_a_real_scope')"
        )
    cur.execute("ROLLBACK TO SAVEPOINT reject")


@pytest.mark.postgres
@live_postgres
class TestLiveSchemaDriftConvergence:
    """Everything in here needs FLOWGATE_PG_TEST_DSN; see the module docstring."""

    @pytest.fixture(scope="class")
    def pg_conn(self):
        conn = psycopg2.connect(DSN)
        conn.autocommit = False
        yield conn
        conn.rollback()
        conn.close()

    @pytest.fixture
    def pg_case(self, pg_conn):
        """A throwaway schema holding nothing but a scenario-shaped `tokens` table.

        Never the real `public.tokens` -- the migration text references the bare name
        `tokens`, so isolation comes from `search_path`, not a different table name. The
        whole thing lives inside one SAVEPOINT that is always rolled back, so nothing
        persists next to production data even if an assertion fails mid-test.
        """
        cur = pg_conn.cursor()
        schema = f"fg_test_0539_{uuid.uuid4().hex[:10]}"
        cur.execute("SAVEPOINT fg_case")
        try:
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"SET LOCAL search_path TO {schema}")
            yield cur, schema
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT fg_case")
            cur.close()

    # Case A — the canonical name already exists, with the pre-107 8-value list.
    def test_case_a_normal_history_converges(self, pg_case):
        cur, schema = pg_case
        cur.execute(
            "CREATE TABLE tokens (token_id TEXT PRIMARY KEY, action_scope TEXT NOT NULL)"
        )
        cur.execute(
            "ALTER TABLE tokens ADD CONSTRAINT tokens_action_scope_check "
            "CHECK (action_scope IN ('new','edit','workflow_decide','review','test_run',"
            "'workflow_sequence_edit','resolve_conflict','chat'))"
        )

        _apply_107(cur, schema)

        _assert_converged(cur, schema)

    # Case B — no action_scope CHECK exists at all.
    def test_case_b_missing_check_does_not_fail_boot(self, pg_case):
        cur, schema = pg_case
        cur.execute(
            "CREATE TABLE tokens (token_id TEXT PRIMARY KEY, action_scope TEXT NOT NULL)"
        )

        _apply_107(cur, schema)  # must not raise "does not exist"

        _assert_converged(cur, schema)

    # Case C — the same CHECK exists, but under a different name and a narrower list.
    def test_case_c_legacy_name_is_removed_and_replaced(self, pg_case):
        cur, schema = pg_case
        cur.execute(
            "CREATE TABLE tokens (token_id TEXT PRIMARY KEY, action_scope TEXT NOT NULL)"
        )
        cur.execute(
            "ALTER TABLE tokens ADD CONSTRAINT legacy_tokens_scope_check "
            "CHECK (action_scope IN ('new','edit'))"
        )

        _apply_107(cur, schema)

        names = {name for name, _def in _constraints(cur, schema)}
        assert "legacy_tokens_scope_check" not in names, (
            "the old narrow CHECK survived alongside the new one and would keep rejecting "
            "failure_origin_review inserts"
        )
        _assert_converged(cur, schema)

    # Case D — an unrelated CHECK on a different column must be left alone.
    def test_case_d_unrelated_check_is_preserved(self, pg_case):
        cur, schema = pg_case
        cur.execute(
            "CREATE TABLE tokens (token_id TEXT PRIMARY KEY, action_scope TEXT NOT NULL, "
            "dry_run_count INTEGER NOT NULL DEFAULT 0)"
        )
        cur.execute(
            "ALTER TABLE tokens ADD CONSTRAINT tokens_dry_run_count_check "
            "CHECK (dry_run_count >= 0)"
        )

        _apply_107(cur, schema)

        names = {name for name, _def in _constraints(cur, schema)}
        assert "tokens_dry_run_count_check" in names, "an unrelated CHECK was dropped"
        assert names == {"tokens_action_scope_check", "tokens_dry_run_count_check"}, (
            f"catalog scan touched more than the action_scope CHECK: {names}"
        )
        _assert_converged(cur, schema)
