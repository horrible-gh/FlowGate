"""0444 TS0011 — NR0003 §4-3/§5 provider FK regression coordinates.

MySQL cannot be started on the FlowGate test host, so the MySQL half fixes the exact
migration contract statically.  The SQLite half executes the user-visible invariant
against a real database with every migration applied: deleting a provider clears the
paused chain pin without deleting the paused chain.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path


_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
_MIGRATION = "086_ai_invoke_paused_provider_fk.sql"


def _executable_sql(path: Path) -> str:
    body = path.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.DOTALL)
    body = "\n".join(re.sub(r"--.*$", "", line) for line in body.splitlines())
    return " ".join(body.split())


def test_mysql_086_adds_the_table_level_provider_fk_with_set_null():
    """076a's inline REFERENCES must never become the only MySQL FK again."""
    sql = _executable_sql(_MIGRATIONS / "mysql" / _MIGRATION)
    assert re.fullmatch(
        r"ALTER TABLE ai_invoke_paused_chains "
        r"ADD CONSTRAINT fk_aipc_provider "
        r"FOREIGN KEY \(continuation_base_provider_id\) "
        r"REFERENCES ai_providers\(provider_id\) "
        r"ON DELETE SET NULL;",
        sql,
        flags=re.IGNORECASE,
    ), sql


def test_non_mysql_086_counterparts_remain_explicit_no_ops():
    """The dialect filename set stays aligned without re-adding an existing FK."""
    for dialect in ("sqlite", "postgres"):
        path = _MIGRATIONS / dialect / _MIGRATION
        assert path.is_file(), f"missing {dialect}/{_MIGRATION}"
        assert _executable_sql(path) == "", f"{dialect}/{_MIGRATION} must stay a no-op"


def test_deleting_a_provider_degrades_the_paused_pin_not_the_chain(migrated_sqlite_db):
    """NR0003 §5 runtime oracle, exercised through the real SQLite migration set."""
    seed_sql = """
    INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at)
      VALUES('flowgate', 'FlowGate', 1, datetime('now'), datetime('now'));
    INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
      VALUES('flowgate.default.0444', 'flowgate', 'default', 'provider FK', 'OPEN',
             datetime('now'), datetime('now'));
    INSERT INTO documents(
      doc_id, project_id, module, group_id, type_code, seq, title, status,
      doc_review_status, created_at, updated_at
    ) VALUES(
      'flowgate.default.0444.0001-B', 'flowgate', 'default', 'flowgate.default.0444',
      'B', 1, 'provider FK', 'open', NULL, datetime('now'), datetime('now')
    );
    INSERT INTO users(
      user_id, username, email, password, is_active, is_admin,
      first_login_required, created_at, updated_at
    ) VALUES(
      'usr_0444', 'worker-0444', 'worker-0444@example.invalid', 'hashed', 1, 0,
      0, datetime('now'), datetime('now')
    );
    INSERT INTO ai_providers(
      provider_id, project_id, name, exec_type, kind, enabled, sort_order,
      created_at, updated_at
    ) VALUES(
      'provider-0444', 'flowgate', 'Provider 0444', 'cli', 'codex', 1, 0,
      datetime('now'), datetime('now')
    );
    INSERT INTO ai_invoke_paused_chains(
      group_id, doc_ref, mode, paused_by, paused_at, docs_reached,
      continuation_base_provider_id, created_at, updated_at
    ) VALUES(
      'flowgate.default.0444', 'flowgate.default.0444.0001-B', 'continuous',
      'usr_0444', datetime('now'), 0, 'provider-0444', datetime('now'), datetime('now')
    );
    """
    db_path = migrated_sqlite_db("paused_provider_fk_0444.db", seed_sql=seed_sql)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        fk_rows = conn.execute("PRAGMA foreign_key_list(ai_invoke_paused_chains)").fetchall()
        assert any(
            row["table"] == "ai_providers"
            and row["from"] == "continuation_base_provider_id"
            and row["to"] == "provider_id"
            and row["on_delete"].upper() == "SET NULL"
            for row in fk_rows
        )

        conn.execute("DELETE FROM ai_providers WHERE provider_id = ?", ("provider-0444",))
        conn.commit()
        paused = conn.execute(
            "SELECT continuation_base_provider_id FROM ai_invoke_paused_chains "
            "WHERE group_id = ?",
            ("flowgate.default.0444",),
        ).fetchone()
        assert paused is not None
        assert paused["continuation_base_provider_id"] is None
    finally:
        conn.close()