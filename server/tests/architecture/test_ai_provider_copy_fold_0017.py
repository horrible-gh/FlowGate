"""Migration 131 folds a project's copy of a system AI into that system AI (flowgate.v02.0017
CH0006 #46/#48) instead of promoting it as "<name> · <project>" into the shared system list."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "sql" / "migrations" / "sqlite"
T = "2026-01-01T00:00:00"


def _db_before_131() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name[:3] >= "131":
            break
        db.executescript(path.read_text(encoding="utf-8"))
    return db


def _apply_from_131(db: sqlite3.Connection) -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if "131" <= path.name[:3] <= "135":
            db.executescript(path.read_text(encoding="utf-8"))


def _provider(db, provider_id, project_id, name, kind, command, exec_type="cli", order=0):
    db.execute(
        "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, cli_command, api_base_url,"
        " sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (provider_id, project_id, name, exec_type, kind, command,
         "http://api" if exec_type == "api" else None, order, T, T))


def test_project_copy_of_system_ai_is_folded_not_promoted():
    db = _db_before_131()
    for project in ("test2", "agent"):
        db.execute("INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                   (project, project, T, T))
        db.execute("INSERT INTO project_settings (project_id, updated_at, ai_mode, ai_default_provider_id)"
                   " VALUES (?, ?, 'custom', ?)", (project, T, "c_" + project))
    _provider(db, "s1", None, "Claude Opus 5", "claude", "claude -p ")
    _provider(db, "s2", None, "Codex", "codex", "codex exec -", order=1)
    _provider(db, "c_test2", "test2", "Claude Opus 5", "claude", "claude -p")   # copy of s1
    _provider(db, "c_agent", "agent", "Codex", "codex", "codex exec -")         # copy of s2
    _provider(db, "n_agent", "agent", "Claude Opus 5", "claude", "claude -p --model x", order=1)
    _provider(db, "a_agent", "agent", "API", "openai", None, exec_type="api", order=2)
    db.execute("INSERT INTO ai_provider_doctype_map (project_id, doc_type, provider_id, created_at, updated_at)"
               " VALUES ('agent', 'R', 'c_agent', ?, ?)", (T, T))
    key = hashlib.sha256(b"g").hexdigest()
    db.execute("INSERT INTO ai_groups (ai_group_id, project_id, name, name_key, name_key_hash, created_at, updated_at)"
               " VALUES ('g1', 'agent', 'G', 'g', ?, ?, ?)", (key, T, T))
    db.execute("INSERT INTO ai_group_members VALUES ('m1', 'g1', 'c_agent', ?)", (T,))
    db.execute("INSERT INTO ai_group_runtime_bindings VALUES ('g1', 'work_executor', 'c_agent', ?)", (T,))
    db.commit()

    _apply_from_131(db)

    rows = {r[0]: r[1:] for r in db.execute(
        "SELECT provider_id, project_id, name, enabled FROM ai_providers")}
    # Copies stay as disabled project rows that no system list reads; history keeps their ids.
    assert rows["c_test2"] == ("test2", "Claude Opus 5 · test2", 0)
    assert rows["c_agent"] == ("agent", "Codex · agent", 0)
    # A different command under the same name is a different AI: promoted as before.
    assert rows["n_agent"] == (None, "Claude Opus 5 · agent", 1)
    # API rows are never folded (the key cannot be compared).
    assert rows["a_agent"][0] is None
    system_names = sorted(r[0] for r in db.execute(
        "SELECT name FROM ai_providers WHERE project_id IS NULL AND owner_scope = 'system'"))
    assert system_names == ["API", "Claude Opus 5", "Claude Opus 5 · agent", "Codex"]
    # AI-management references follow the system AI.
    assert db.execute("SELECT provider_id FROM ai_provider_doctype_map").fetchall() == [("s2",)]
    assert db.execute("SELECT provider_id FROM ai_group_members").fetchall() == [("s2",)]
    assert db.execute("SELECT provider_id FROM ai_group_runtime_bindings").fetchall() == [("s2",)]
    assert dict(db.execute("SELECT project_id, ai_default_provider_id FROM project_settings")) == {
        "test2": "s1", "agent": "s2"}
