"""flowgate.v02.0135 CH0006 -- a personal AI's turns carry its name, not its provider id.

The name lookup of a token-bound AI read project and system rows only, so a personal AI
(owner_scope='user') was stored as "aip_..." on both the turn and the participant row.
Covers the lookup fix and migration 175, which restores the name on rows already stored.
A real migrated SQLite store is used: the subject is what is stored.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.flow_gate.db import conversation_turns as turn_store  # noqa: E402
from modules.flow_gate.services import conversation_events  # noqa: E402
from modules.flow_gate.services import conversation_turn_service as service  # noqa: E402

from tests.store_transaction_support import build_live_sqlite_db, install_live_sqlite_store  # noqa: E402

DOC = "flowgate.v02.0135.0006-CH"
PERSONAL_ID = "aip_gy06m5"
PERSONAL_NAME = "Codex GPT 6 Luna(AG)"
SYSTEM_ID = "aip_zclzvc"
SYSTEM_NAME = "Claude Haiku 5.5"
MIGRATION = (Path(__file__).resolve().parents[3]
             / "sql/migrations/sqlite/175_conversation_ai_display_name.sql")


def _provider(conn, provider_id: str, name: str, owner_scope: str, owner_user_id) -> None:
    conn.execute(
        "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, enabled, "
        "sort_order, created_at, updated_at, owner_scope, owner_user_id) "
        "VALUES (?, NULL, ?, 'cli', 'codex', 1, 0, 't', 't', ?, ?)",
        [provider_id, name, owner_scope, owner_user_id],
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    live = install_live_sqlite_store(monkeypatch, build_live_sqlite_db(tmp_path / "ai_name_0135.db"))
    live.conn.execute("PRAGMA foreign_keys = OFF")
    live.conn.execute(
        "INSERT INTO documents (doc_id, project_id, type_code, seq, title, created_at, updated_at) "
        "VALUES (?, 'flowgate', 'CH', 6, 'chat', 't', 't')", [DOC],
    )
    _provider(live.conn, PERSONAL_ID, PERSONAL_NAME, "user", "u1")
    _provider(live.conn, SYSTEM_ID, SYSTEM_NAME, "system", None)
    live.conn.commit()
    monkeypatch.setattr(service, "_validate_document_for_append",
                        lambda doc_id: {"doc_id": doc_id, "project_id": "flowgate", "type_code": "CH"})
    monkeypatch.setattr(conversation_events, "broadcast_turn_appended", lambda doc, result: None)
    return live


def _worker(provider_id: str, n: int) -> dict:
    return {"kind": "worker", "token": {
        "token_id": f"tok_{provider_id}_{n}", "provider_id": provider_id, "project": "flowgate",
    }}


def _append(provider_id: str, n: int) -> dict:
    return service.append_turn(doc_id=DOC, actor=_worker(provider_id, n), body_raw=f"reply {n}",
                               idempotency_key=f"idem-{provider_id}-{n}", based_on_seq=0)


def test_personal_ai_turn_and_participant_carry_the_provider_name(db):
    result = _append(PERSONAL_ID, 1)
    assert result["turn"]["display_name"] == PERSONAL_NAME
    assert result["me"]["display_name"] == PERSONAL_NAME
    stored = turn_store.get_turn(DOC, result["turn"]["seq"])
    assert stored["display_name"] == PERSONAL_NAME
    participant = turn_store.get_participant(DOC, f"provider:{PERSONAL_ID}")
    assert participant["display_name"] == PERSONAL_NAME


def test_system_ai_name_is_unchanged(db):
    result = _append(SYSTEM_ID, 1)
    assert result["turn"]["display_name"] == SYSTEM_NAME
    assert turn_store.get_participant(DOC, f"provider:{SYSTEM_ID}")["display_name"] == SYSTEM_NAME


def test_unknown_provider_still_falls_back_to_its_id(db):
    result = _append("aip_gone", 1)
    assert result["turn"]["display_name"] == "aip_gone"


def _seed_turn(seq: int, speaker: str, key: str, name) -> None:
    db_conn = turn_store.get_store()
    db_conn._execute(
        "INSERT INTO conversation_turns (doc_id, seq, speaker, participant_key, display_name, "
        "body, body_hash, based_on_seq, idempotency_key, idempotency_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'b', ?, 0, ?, ?, 't')",
        [DOC, seq, speaker, key, name, "0" * 64, f"k{seq}", f"{seq:064d}"],
    )


def _seed_participant(key: str, kind: str, name) -> None:
    turn_store.get_store()._execute(
        "INSERT INTO conversation_participants (doc_id, participant_key, kind, display_name, "
        "last_seen_at) VALUES (?, ?, ?, ?, 't')", [DOC, key, kind, name],
    )


def test_migration_175_restores_names_stored_as_the_provider_id(db):
    key = f"provider:{PERSONAL_ID}"
    _seed_turn(1, "ai", key, PERSONAL_ID)                       # the defect: id as name
    _seed_turn(2, "ai", key, PERSONAL_ID)
    _seed_turn(3, "ai", f"provider:{SYSTEM_ID}", SYSTEM_NAME)   # already a name: untouched
    _seed_turn(4, "ai", "provider:aip_gone", "aip_gone")        # no provider row: untouched
    _seed_turn(5, "ai", f"provider:{SYSTEM_ID}", PERSONAL_ID)   # another AI's id: untouched
    _seed_turn(6, "user", "user:u1", PERSONAL_ID)               # a human: untouched
    _seed_participant(key, "ai", PERSONAL_ID)
    _seed_participant("provider:aip_gone", "ai", "aip_gone")
    _seed_participant("user:u1", "user", PERSONAL_ID)

    db.conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    db.conn.commit()

    names = {t["seq"]: t["display_name"] for t in turn_store.list_turns(DOC)}
    assert names == {1: PERSONAL_NAME, 2: PERSONAL_NAME, 3: SYSTEM_NAME, 4: "aip_gone",
                     5: PERSONAL_ID, 6: PERSONAL_ID}
    assert turn_store.get_participant(DOC, key)["display_name"] == PERSONAL_NAME
    assert turn_store.get_participant(DOC, "provider:aip_gone")["display_name"] == "aip_gone"
    assert turn_store.get_participant(DOC, "user:u1")["display_name"] == PERSONAL_ID

    # Re-running is a no-op.
    db.conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    assert {t["seq"]: t["display_name"] for t in turn_store.list_turns(DOC)} == names
