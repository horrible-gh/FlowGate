"""0596 TR0005 rev2 — 116 work-executor provider FK regression coordinates.

Mirrors test_ai_invoke_paused_provider_fk_0444.py: MySQL cannot be started on the
FlowGate test host, so the MySQL half fixes the exact migration contract statically.
The SQLite half executes the user-visible invariant against a real database with
every migration applied: deleting a provider clears the work-executor pin without
deleting the paused chain.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path


_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
_MIGRATION = "116_ai_invoke_paused_work_executor_provider.sql"


def _executable_statements(path: Path) -> list[str]:
    body = path.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.DOTALL)
    body = "\n".join(re.sub(r"--.*$", "", line) for line in body.splitlines())
    body = " ".join(body.split())
    return [stmt.strip() for stmt in body.split(";") if stmt.strip()]


def test_mysql_116_adds_a_table_level_fk_with_set_null():
    """A column-level inline REFERENCES on ADD COLUMN is silently discarded by InnoDB
    (086a_ai_invoke_paused_provider_fk.sql repairs the same mistake for
    continuation_base_provider_id). 116 must not repeat it: the FK has to come from an
    explicit table-level ADD CONSTRAINT ... FOREIGN KEY ... ON DELETE SET NULL."""
    statements = _executable_statements(_MIGRATIONS / "mysql" / _MIGRATION)
    assert re.fullmatch(
        r"ALTER TABLE ai_invoke_paused_chains "
        r"ADD COLUMN continuation_work_executor_provider_id VARCHAR\(191\)",
        statements[0],
        flags=re.IGNORECASE,
    ), statements
    assert re.fullmatch(
        r"ALTER TABLE ai_invoke_paused_chains "
        r"ADD CONSTRAINT fk_aipc_work_executor_provider "
        r"FOREIGN KEY \(continuation_work_executor_provider_id\) "
        r"REFERENCES ai_providers\(provider_id\) "
        r"ON DELETE SET NULL",
        statements[1],
        flags=re.IGNORECASE,
    ), statements
    assert len(statements) == 2, statements


def test_deleting_a_provider_degrades_the_work_executor_pin_not_the_chain(
    migrated_sqlite_db,
):
    """NR0003 rev3 runtime oracle, exercised through the real SQLite migration set."""
    seed_sql = """
    INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at)
      VALUES('flowgate', 'FlowGate', 1, datetime('now'), datetime('now'));
    INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
      VALUES('flowgate.default.0596', 'flowgate', 'default', 'work executor FK', 'OPEN',
             datetime('now'), datetime('now'));
    INSERT INTO documents(
      doc_id, project_id, module, group_id, type_code, seq, title, status,
      doc_review_status, created_at, updated_at
    ) VALUES(
      'flowgate.default.0596.0001-B', 'flowgate', 'default', 'flowgate.default.0596',
      'B', 1, 'work executor FK', 'open', NULL, datetime('now'), datetime('now')
    );
    INSERT INTO users(
      user_id, username, email, password, is_active, is_admin,
      first_login_required, created_at, updated_at
    ) VALUES(
      'usr_0596', 'worker-0596', 'worker-0596@example.invalid', 'hashed', 1, 0,
      0, datetime('now'), datetime('now')
    );
    INSERT INTO ai_providers(
      provider_id, project_id, name, exec_type, kind, enabled, sort_order,
      created_at, updated_at
    ) VALUES(
      'provider-0596', 'flowgate', 'Provider 0596', 'cli', 'codex', 1, 0,
      datetime('now'), datetime('now')
    );
    INSERT INTO ai_invoke_paused_chains(
      group_id, doc_ref, mode, paused_by, paused_at, docs_reached,
      continuation_work_executor_provider_id, created_at, updated_at
    ) VALUES(
      'flowgate.default.0596', 'flowgate.default.0596.0001-B', 'continuous',
      'usr_0596', datetime('now'), 0, 'provider-0596', datetime('now'), datetime('now')
    );
    """
    db_path = migrated_sqlite_db("paused_work_executor_provider_fk_0596.db", seed_sql=seed_sql)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        fk_rows = conn.execute("PRAGMA foreign_key_list(ai_invoke_paused_chains)").fetchall()
        assert any(
            row["table"] == "ai_providers"
            and row["from"] == "continuation_work_executor_provider_id"
            and row["to"] == "provider_id"
            and row["on_delete"].upper() == "SET NULL"
            for row in fk_rows
        )

        conn.execute("DELETE FROM ai_providers WHERE provider_id = ?", ("provider-0596",))
        conn.commit()
        paused = conn.execute(
            "SELECT continuation_work_executor_provider_id FROM ai_invoke_paused_chains "
            "WHERE group_id = ?",
            ("flowgate.default.0596",),
        ).fetchone()
        assert paused is not None
        assert paused["continuation_work_executor_provider_id"] is None
    finally:
        conn.close()
