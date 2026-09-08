"""PostgreSQL [pg-fk-rebuild] must key on the original table, not the rename-to-backup name
(flowgate.default.0542).

NR0003 traced migration 108's boot failure to `tools/regen_dialect_migrations.py`'s FK
snapshot/restore generator. The SQLite "rebuild a table to alter a CHECK" idiom has two shapes
in this migration set:

  * _new+RENAME (023, 027, 033, ...): ``DROP TABLE x; ... ALTER TABLE x_new RENAME TO x;`` --
    the DROP still targets the original name, so a snapshot taken right before that DROP is
    correct as-is.
  * rename-to-backup (036, 042a, 052, 062a, 064, 073, 075a, 086b, 107, 108, ...):
    ``ALTER TABLE x RENAME TO x_before_...; CREATE TABLE x (...); ...; DROP TABLE x_before_...;``
    -- here the DROP targets the *backup* name, and by the time it runs a brand-new `x` already
    exists. Snapshotting there made ``pg_get_constraintdef`` render "REFERENCES x_before_...",
    a relation the following DROP TABLE then deletes -- "relation ... does not exist" on the
    restore. The fix snapshots *before* the RENAME, keyed on the original name `x`.

T0004 requires this fixed generically (no hardcoded backup-name string) and re-verified across
every migration using either idiom, not just 108. The offline tests below are dialect-independent
static checks over the regenerated `server/sql/migrations/postgres/` set; the live class proves
the actual runtime FK-restore semantics against real PostgreSQL and follows the same opt-in
SAVEPOINT-per-schema convention as test_migration_107_postgres_schema_drift_0539.py (skipped
unless FLOWGATE_PG_TEST_DSN is set).
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
_TOOLS_DIR = _SERVER_DIR / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import regen_dialect_migrations as R  # noqa: E402

PG_DIR = _SERVER_DIR / "sql" / "migrations" / "postgres"

# (migration filename, original table name, backup table name) for every migration in the set
# that (a) uses the rename-to-backup idiom on its postgres file and (b) is regenerated
# generically from the SQLite source -- i.e. excluding R.regen_dialect_migrations._PG_HAND_AUTHORED_IDS
# (064, 074a, 075, 092, 107), which rewrite the CHECK/FK in place by hand instead and are never
# regenerated. Not just 108 -- T0004 requires the generator fix (and its regression coverage) to
# cover every sibling, since the bug is in shared generator code.
RENAME_TO_BACKUP_MIGRATIONS = [
    ("036_tokens_workflow_decide_scope.sql", "tokens", "tokens_before_workflow_decide_scope"),
    ("042a_tokens_review_scope.sql", "tokens", "tokens_before_review_scope"),
    ("052_test_runs.sql", "tokens", "tokens_before_test_run_scope"),
    ("062a_tokens_workflow_sequence_edit_scope.sql", "tokens",
     "tokens_before_workflow_sequence_edit_scope"),
    ("075a_tokens_chat_scope.sql", "tokens", "tokens_before_chat_scope"),
    ("108_tokens_resolve_base_dirty_scope.sql", "tokens", "tokens_before_base_dirty_scope"),
    ("086b_ai_invoke_restart_max_attempts.sql", "ai_invoke_runs",
     "ai_invoke_runs_before_unlimited_attempts"),
    ("073_remote_tool_op_log_patch_stat.sql", "remote_tool_op_log",
     "remote_tool_op_log_before_patch_stat"),
]


def test_hand_authored_ids_are_excluded_from_the_generic_idiom_list():
    """064/107 (and siblings) must never appear above -- they use a structurally different,
    hand-authored fix and are excluded from generic regen by R._PG_HAND_AUTHORED_IDS."""
    listed_ids = {R._migration_id(fname) for fname, _, _ in RENAME_TO_BACKUP_MIGRATIONS}
    assert not (listed_ids & R._PG_HAND_AUTHORED_IDS)

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

live_postgres = pytest.mark.skipif(_SKIP_LIVE is not None, reason=_SKIP_LIVE or "")


# ── offline: unit tests of the generator functions ─────────────────────────────────


def test_rename_idiom_snapshot_is_keyed_and_positioned_on_the_original_name():
    """Synthetic 108-shaped snippet: the fence must key `to_regclass('x')`, not the backup
    name, and must be inserted before the RENAME (not before the later DROP TABLE)."""
    snippet = (
        "ALTER TABLE x RENAME TO x_before_thing;\n"
        "CREATE TABLE x (id TEXT PRIMARY KEY);\n"
        "INSERT INTO x SELECT id FROM x_before_thing;\n"
        "DROP TABLE x_before_thing;\n"
    )
    out = R.fix_pg_table_rebuild(snippet)

    rename_pos = out.index("ALTER TABLE x RENAME TO x_before_thing;")
    snapshot_pos = out.index("preserve inbound FOREIGN KEYs")
    drop_pos = out.index("DROP TABLE x_before_thing;")

    assert snapshot_pos < rename_pos < drop_pos, (
        "the FK snapshot fence must run before the RENAME, not between the RENAME and the DROP"
    )
    assert "to_regclass('x')" in out
    assert "to_regclass('x_before_thing')" not in out, (
        "the snapshot/restore must key on the original name, never the backup name"
    )
    assert 'restore inbound FOREIGN KEYs for "x"' in out


def test_classic_new_rename_idiom_is_unaffected():
    """Synthetic 023-shaped snippet (DROP the original, then rename `_new` onto it): the fence
    must stay right before the DROP, keyed on the original name -- this idiom never had the bug."""
    snippet = (
        "CREATE TABLE x_new (id TEXT PRIMARY KEY);\n"
        "INSERT INTO x_new SELECT id FROM x;\n"
        "DROP TABLE x;\n"
        "ALTER TABLE x_new RENAME TO x;\n"
    )
    out = R.fix_pg_table_rebuild(snippet)

    drop_pos = out.index("DROP TABLE x;")
    snapshot_pos = out.index("preserve inbound FOREIGN KEYs")
    rename_pos = out.index("ALTER TABLE x_new RENAME TO x;")

    assert snapshot_pos < drop_pos < rename_pos
    assert "to_regclass('x')" in out


def test_pg_unfenced_drop_table_recognizes_the_rename_idiom_fence():
    """The R7-style regression guard must not false-positive on a correctly-fenced
    rename-to-backup idiom (it looks for the sentinel under the *original* name)."""
    snippet = (
        "ALTER TABLE x RENAME TO x_before_thing;\n"
        "CREATE TABLE x (id TEXT PRIMARY KEY);\n"
        "DROP TABLE x_before_thing;\n"
    )
    fenced = R.fix_pg_table_rebuild(snippet)
    assert R.pg_unfenced_drop_table(fenced) is None


def test_pg_unfenced_drop_table_still_catches_a_missing_fence():
    unfenced = "DROP TABLE x_before_thing;\n"
    assert R.pg_unfenced_drop_table(unfenced) == "x_before_thing"


# ── offline: the committed postgres/ set itself (R7 + the 108/sibling shape) ───────


def test_no_committed_migration_references_a_dropped_backup_table():
    """R7: scan every regenerated postgres file for a FK restore DDL that still targets a
    `*_before_*` backup name -- the exact bug: that relation no longer exists once its own
    DROP TABLE has run."""
    import re
    pattern = re.compile(r"REFERENCES\s+(\w+_before_\w+)\s*\(")
    offenders = []
    for path in sorted(PG_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            offenders.append(f"{path.name}: REFERENCES {m.group(1)}(...)")
    assert not offenders, "dangling backup-table FK reference(s):\n" + "\n".join(offenders)


@pytest.mark.parametrize("fname,original,backup", RENAME_TO_BACKUP_MIGRATIONS)
def test_committed_rename_idiom_migrations_fence_before_the_rename(fname, original, backup):
    """Every migration using the rename-to-backup idiom (not just 108) must have its
    [pg-fk-rebuild] snapshot positioned before the RENAME and keyed on the original name."""
    text = (PG_DIR / fname).read_text(encoding="utf-8")

    rename_stmt = f"ALTER TABLE {original} RENAME TO {backup};"
    assert rename_stmt in text, f"{fname}: expected rename statement not found"

    snapshot_marker = f'preserve inbound FOREIGN KEYs across the drop+recreate of "{original}"'
    restore_marker = f'restore inbound FOREIGN KEYs for "{original}"'
    assert snapshot_marker in text, f"{fname}: missing snapshot fence keyed on '{original}'"
    assert restore_marker in text, f"{fname}: missing restore fence keyed on '{original}'"

    assert text.index(snapshot_marker) < text.index(rename_stmt), (
        f"{fname}: the snapshot fence must run before the RENAME, else pg_get_constraintdef "
        f"renders the soon-to-be-dropped backup name '{backup}'"
    )
    assert f"to_regclass('{backup}')" not in text, (
        f"{fname}: snapshot/restore must never key on the backup name '{backup}'"
    )


# ── live: proves the actual runtime FK-restore semantics on real PostgreSQL ────────


def _fk_target(cur, schema, child_table, fk_column):
    cur.execute(
        """
        SELECT confrelid::regclass::text, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE n.nspname = %s AND t.relname = %s AND c.contype = 'f' AND a.attname = %s
        """,
        (schema, child_table, fk_column),
    )
    rows = cur.fetchall()
    assert len(rows) == 1, f"expected exactly one FK on {child_table}.{fk_column}, got {rows}"
    return rows[0]  # (referenced_table_name, full_constraintdef)


def _rebuild_snippet(table: str, backup: str, column: str = "id TEXT PRIMARY KEY") -> str:
    """Run the real generator (R.fix_pg_table_rebuild) over a minimal rename-to-backup skeleton.

    Deliberately decoupled from the real migration files' full production column lists (which
    evolve independently of this bug): what T0004 fixes is the FK snapshot/restore *fence*
    itself, not any particular table's columns, so a minimal skeleton run through the actual
    fixed function is a more robust, maintainable proof than replaying a specific migration
    file's exact DDL (which would need to stay byte-for-byte in sync with production schema).
    """
    skeleton = (
        f"ALTER TABLE {table} RENAME TO {backup};\n"
        f"CREATE TABLE {table} ({column});\n"
        f"INSERT INTO {table} SELECT * FROM {backup};\n"
        f"DROP TABLE {backup};\n"
    )
    return R.fix_pg_table_rebuild(skeleton)


@pytest.mark.postgres
@live_postgres
class TestLiveRenameIdiomFkRestore:
    """Everything here needs FLOWGATE_PG_TEST_DSN; see the module docstring."""

    @pytest.fixture(scope="class")
    def pg_conn(self):
        conn = psycopg2.connect(DSN)
        conn.autocommit = False
        yield conn
        conn.rollback()
        conn.close()

    @pytest.fixture
    def pg_case(self, pg_conn):
        """A throwaway schema, isolated by search_path, rolled back unconditionally."""
        cur = pg_conn.cursor()
        schema = f"fg_test_0542_{uuid.uuid4().hex[:10]}"
        cur.execute("SAVEPOINT fg_case")
        try:
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"SET LOCAL search_path TO {schema}")
            yield cur, schema
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT fg_case")
            cur.close()

    # R1 — no inbound FK: snapshot/restore must be a harmless no-op.
    def test_r1_no_inbound_fk_is_a_noop(self, pg_case):
        cur, schema = pg_case
        cur.execute("CREATE TABLE tokens (token_id TEXT PRIMARY KEY)")
        cur.execute(f"SET LOCAL search_path TO {schema}")
        cur.execute(_rebuild_snippet("tokens", "tokens_before_thing", "token_id TEXT PRIMARY KEY"))
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = 'tokens'",
            (schema,),
        )
        assert cur.fetchone() is not None

    # R2 — one inbound FK (the group 0542 real-world case: user_chat_source_access).
    def test_r2_single_inbound_fk_is_restored_against_the_new_table(self, pg_case):
        cur, schema = pg_case
        cur.execute("CREATE TABLE tokens (token_id TEXT PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE user_chat_source_access ("
            "user_id TEXT PRIMARY KEY, "
            "one_shot_token_id TEXT REFERENCES tokens(token_id) ON DELETE RESTRICT)"
        )
        cur.execute(f"SET LOCAL search_path TO {schema}")

        cur.execute(
            _rebuild_snippet("tokens", "tokens_before_thing", "token_id TEXT PRIMARY KEY")
        )  # must not raise "relation ... does not exist"

        target, condef = _fk_target(cur, schema, "user_chat_source_access", "one_shot_token_id")
        assert target == "tokens", f"FK still points at a stale relation: {condef}"
        assert "ON DELETE RESTRICT" in condef

        cur.execute("SELECT oid FROM pg_class WHERE relname = 'tokens' AND relnamespace = "
                    "(SELECT oid FROM pg_namespace WHERE nspname = %s)", (schema,))
        new_oid = cur.fetchone()[0]
        cur.execute(
            "SELECT confrelid FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE t.relname = 'user_chat_source_access' AND c.contype = 'f'"
        )
        assert cur.fetchone()[0] == new_oid, "FK targets the old (pre-rebuild) table oid"

    # R3 — inbound FKs from more than one child table, all restored to the new table.
    def test_r3_multiple_inbound_fks_are_all_restored(self, pg_case):
        cur, schema = pg_case
        cur.execute("CREATE TABLE tokens (token_id TEXT PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child_a (id TEXT PRIMARY KEY, "
            "tok TEXT REFERENCES tokens(token_id) ON DELETE CASCADE)"
        )
        cur.execute(
            "CREATE TABLE child_b (id TEXT PRIMARY KEY, "
            "tok TEXT REFERENCES tokens(token_id) ON DELETE SET NULL)"
        )
        cur.execute(f"SET LOCAL search_path TO {schema}")
        cur.execute(_rebuild_snippet("tokens", "tokens_before_thing", "token_id TEXT PRIMARY KEY"))

        target_a, def_a = _fk_target(cur, schema, "child_a", "tok")
        target_b, def_b = _fk_target(cur, schema, "child_b", "tok")
        assert target_a == "tokens" and "ON DELETE CASCADE" in def_a
        assert target_b == "tokens" and "ON DELETE SET NULL" in def_b

    # R4 — every ON DELETE action is preserved, parametrized.
    @pytest.mark.parametrize("action", ["RESTRICT", "CASCADE", "SET NULL"])
    def test_r4_on_delete_action_is_preserved(self, pg_case, action):
        cur, schema = pg_case
        cur.execute("CREATE TABLE tokens (token_id TEXT PRIMARY KEY)")
        cur.execute(
            f"CREATE TABLE child (id TEXT PRIMARY KEY, "
            f"tok TEXT REFERENCES tokens(token_id) ON DELETE {action})"
        )
        cur.execute(f"SET LOCAL search_path TO {schema}")
        cur.execute(_rebuild_snippet("tokens", "tokens_before_thing", "token_id TEXT PRIMARY KEY"))

        target, condef = _fk_target(cur, schema, "child", "tok")
        assert target == "tokens"
        assert f"ON DELETE {action}" in condef

    # R5 — schema-qualified child + quoted identifiers must not produce a wrong target.
    def test_r5_schema_qualified_child_and_quoted_identifiers(self, pg_case):
        cur, schema = pg_case
        cur.execute('CREATE TABLE tokens ("token_id" TEXT PRIMARY KEY)')
        cur.execute(
            f'CREATE TABLE {schema}."Child_Table" (id TEXT PRIMARY KEY, '
            f'"tok" TEXT REFERENCES tokens("token_id") ON DELETE CASCADE)'
        )
        cur.execute(f"SET LOCAL search_path TO {schema}")
        cur.execute(_rebuild_snippet("tokens", "tokens_before_thing", '"token_id" TEXT PRIMARY KEY'))

        target, condef = _fk_target(cur, schema, "Child_Table", "tok")
        assert target == "tokens"
        assert "ON DELETE CASCADE" in condef

    # R6 — the real group 0542 regression: user_chat_source_access.one_shot_token_id_fkey
    # must end up REFERENCES tokens(token_id), matching T0004 section 6's expected DDL exactly.
    def test_r6_108_real_world_constraint_name_converges(self, pg_case):
        cur, schema = pg_case
        cur.execute("CREATE TABLE tokens (token_id TEXT PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE user_chat_source_access ("
            "user_id TEXT PRIMARY KEY, "
            "one_shot_token_id TEXT "
            "CONSTRAINT user_chat_source_access_one_shot_token_id_fkey "
            "REFERENCES tokens(token_id) ON DELETE RESTRICT)"
        )
        cur.execute(f"SET LOCAL search_path TO {schema}")
        # The real 108 file's CREATE TABLE/INSERT column lists are production-shaped and would
        # need a matching pre-existing `tokens` table; exercise the same fence mechanism (the
        # actual backup name 108 renames to) against this test's own minimal schema instead.
        cur.execute(_rebuild_snippet("tokens", "tokens_before_base_dirty_scope",
                                      "token_id TEXT PRIMARY KEY"))

        cur.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'user_chat_source_access_one_shot_token_id_fkey'"
        )
        row = cur.fetchone()
        assert row is not None, "the named constraint did not survive the rebuild"
        assert "REFERENCES tokens(token_id)" in row[0]

    # Generalization proof: a *different* table (ai_invoke_runs, migration 086b's idiom) is
    # fixed the same way -- T0004 explicitly requires this not be 108-only.
    def test_generalizes_to_a_non_tokens_table(self, pg_case):
        cur, schema = pg_case
        cur.execute("CREATE TABLE ai_invoke_runs (run_id TEXT PRIMARY KEY)")
        cur.execute(
            "CREATE TABLE child (id TEXT PRIMARY KEY, "
            "run_id TEXT REFERENCES ai_invoke_runs(run_id) ON DELETE CASCADE)"
        )
        cur.execute(f"SET LOCAL search_path TO {schema}")
        cur.execute(_rebuild_snippet(
            "ai_invoke_runs", "ai_invoke_runs_before_unlimited_attempts", "run_id TEXT PRIMARY KEY"
        ))

        target, condef = _fk_target(cur, schema, "child", "run_id")
        assert target == "ai_invoke_runs"
        assert "ON DELETE CASCADE" in condef
