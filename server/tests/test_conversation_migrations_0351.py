"""Schema and dialect regression tests for flowgate.default.0351 T0007."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "sql" / "migrations"


def _apply(conn: sqlite3.Connection, through: str | None = None) -> None:
    for path in sorted((MIGRATIONS / "sqlite").glob("*.sql")):
        if through and path.name > through:
            break
        conn.executescript(path.read_text(encoding="utf-8"))


def test_all_migrations_create_21_column_token_and_conversation_schema():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _apply(conn)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(tokens)")]
    # 0352 T0004 §3.1 / migration 078 added continuation_auto_approve_item_seqs (21 -> 22).
    assert len(columns) == 22
    assert {
        "continuation_instruction_mode", "provider_id", "ai_run_id",
        "continuation_auto_approve_item_seqs",
    } <= set(columns)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversation_turns", "conversation_participants", "conversation_docs"} <= tables

    indexes = {row[1] for row in conn.execute("PRAGMA index_list(conversation_turns)")}
    assert {"ux_conversation_turns_doc_seq", "ux_conversation_turns_idem"} <= indexes
    participant_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversation_participants'"
    ).fetchone()[0]
    assert "last_viewed_seq <= last_read_seq" in participant_sql


def test_075_preserves_the_existing_18_token_columns_and_rows():
    conn = sqlite3.connect(":memory:")
    _apply(conn, "074_conversation_turns.sql")
    before = [row[1] for row in conn.execute("PRAGMA table_info(tokens)")]
    assert len(before) == 18
    conn.execute(
        "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('p', 'P', 1, 't', 't')"
    )
    conn.execute(
        "INSERT INTO users(user_id, username, email, password, is_active, is_admin, "
        "first_login_required, created_at, updated_at) VALUES ('u','U','u@x','p',1,0,0,'t','t')"
    )
    conn.execute(
        "INSERT INTO tokens(token_id, hash, pepper_id, project, action_scope, issued_to, "
        "created_at, expires_at, dry_run_count, merge_id) "
        "VALUES ('tok_keep','hash_keep','v1','p','edit','u','t','z',3,7)"
    )
    conn.commit()
    conn.executescript((MIGRATIONS / "sqlite" / "075_tokens_chat_scope.sql").read_text(encoding="utf-8"))
    after = [row[1] for row in conn.execute("PRAGMA table_info(tokens)")]
    assert len(after) == 21
    assert not set(before) - set(after)
    row = conn.execute("SELECT * FROM tokens WHERE token_id='tok_keep'").fetchone()
    assert row is not None
    rec = dict(zip(after, row))
    assert rec["dry_run_count"] == 3 and rec["merge_id"] == 7
    assert rec["continuation_instruction_mode"] is None
    assert rec["provider_id"] is None and rec["ai_run_id"] is None


def test_chat_scope_and_foreign_key_delete_actions_are_declared():
    conn = sqlite3.connect(":memory:")
    _apply(conn)
    token_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tokens'"
    ).fetchone()[0]
    assert "'chat'" in token_sql
    turn_fks = conn.execute("PRAGMA foreign_key_list(conversation_turns)").fetchall()
    token_fks = conn.execute("PRAGMA foreign_key_list(tokens)").fetchall()
    assert any(row[2] == "documents" and row[6] == "CASCADE" for row in turn_fks)
    assert any(row[2] == "ai_providers" and row[6] == "SET NULL" for row in token_fks)


def test_generated_dialects_stay_complete_and_turn_insert_is_fail_loud():
    counts = {
        dialect: len(list((MIGRATIONS / dialect).glob("*.sql")))
        for dialect in ("sqlite", "mysql", "postgres")
    }
    assert len(set(counts.values())) == 1
    for dialect in ("sqlite", "mysql", "postgres"):
        sql = (MIGRATIONS / dialect / "074_conversation_turns.sql").read_text(encoding="utf-8")
        assert "conversation_turns" in sql
        assert "ON DELETE CASCADE" in sql
        assert "INSERT IGNORE" not in sql
        assert "ON CONFLICT DO NOTHING" not in sql