"""Narrow first-send timing / replay checks, entirely in an in-memory SQLite store."""
from contextlib import contextmanager
from pathlib import Path
import logging
import sqlite3

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import connection
from modules.flow_gate.documents.routers import conversation_turns as routes
from modules.flow_gate.services import conversation_turn_service as service
from modules.flow_gate.services import chat_run_hooks, conversation_events

DOC = "p.m.0141.0001-CH"
BODY = "private-body-marker-0141"
SECRET = "secret-authorization-marker-0141"


@pytest.fixture
def isolated(monkeypatch):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE documents(doc_id TEXT PRIMARY KEY)")
    migration = Path(__file__).resolve().parents[3] / "sql/migrations/sqlite/074_conversation_turns.sql"
    db.executescript(migration.read_text(encoding="utf-8"))
    db.execute("INSERT INTO documents VALUES (?)", (DOC,))
    db.commit()

    class DB:
        db_type = 1
        def execute(self, sql, params=None):
            cursor = db.execute(sql, params or [])
            db.commit()
            return cursor
        def fetch_one(self, sql, params=None):
            return db.execute(sql, params or []).fetchone()
        def fetch_all(self, sql, params=None):
            return db.execute(sql, params or []).fetchall()
        @contextmanager
        def begin_transaction(self):
            db.execute("BEGIN")
            class Txn:
                cursor = None
                def execute(self, sql, params=None):
                    self.cursor = db.execute(sql, params or [])
                def fetchone(self):
                    return self.cursor.fetchone()
                def fetchall(self):
                    return self.cursor.fetchall()
            try:
                yield Txn()
                db.commit()
            except Exception:
                db.rollback()
                raise
    class Store(connection.FlowGateStore):
        def __init__(self):
            self._db = DB()
            self._sq = None
    monkeypatch.setattr(connection, "STORE", Store())
    monkeypatch.setattr(service, "_validate_document_for_append", lambda doc: {"doc_id": doc})
    monkeypatch.setattr(chat_run_hooks, "registered", lambda event: True)
    hooks = []
    monkeypatch.setattr(chat_run_hooks, "conversation_reply_appended", lambda *args: hooks.append("hook"))
    monkeypatch.setattr(conversation_events, "broadcast_turn_appended", lambda *args: hooks.append("broadcast"))
    yield db, hooks
    db.close()


def test_route_timing_and_lost_answer_replay_store_one(isolated, caplog):
    db, hooks = isolated
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u", "username": "User"}
    caplog.set_level(logging.DEBUG, logger=service.__name__)
    with TestClient(app) as client:
        kwargs = {"headers": {"X-Conversation-Trace": "ct-test-0141", "Authorization": SECRET},
                  "json": {"body": BODY, "idempotency_key": "key-test-0141", "based_on_seq": 0}}
        # First response may be lost by a caller; sending the same request replays it.
        first = client.post(f"/documents/{DOC}/conversation/turn", **kwargs)
        replay = client.post(f"/documents/{DOC}/conversation/turn", **kwargs)
    assert first.status_code == 201 and replay.status_code == 200
    assert first.json()["turn"] == replay.json()["turn"]
    assert replay.json()["replayed"] is True
    assert db.execute("SELECT count(*) FROM conversation_turns").fetchone()[0] == 1
    assert hooks == ["hook", "broadcast"]
    records = [r.args if isinstance(r.args, dict) else r.args[0] for r in caplog.records if r.name == service.__name__ and r.msg == "conversation_timing %s"]
    assert [r["stage"] for r in records] == ["received", "commit", "hooks_end", "broadcast_end", "response", "received", "replay", "response"]
    assert all(r["trace_id"] == "ct-test-0141" for r in records)
    assert BODY not in str(records) and SECRET not in str(records)
    print("TIMING_SERVER", records)


def test_timing_disabled_and_broken_logger_do_not_change_acceptance(isolated, monkeypatch):
    db, _ = isolated
    def broken(*args, **kwargs):
        raise RuntimeError("diagnostic sink unavailable")
    monkeypatch.setattr(service._log, "debug", broken)
    args = dict(doc_id=DOC, actor={"kind": "session", "user_id": "u"}, body_raw=BODY,
                idempotency_key="key-test-0141", based_on_seq=0)
    first = service.append_turn(**args, timing=service.TurnTiming("ct-test-0141", DOC))
    replay = service.append_turn(**args)
    assert first["turn"] == replay["turn"] and replay["replayed"]
    assert db.execute("SELECT count(*) FROM conversation_turns").fetchone()[0] == 1
    invalid = service.TurnTiming(SECRET, DOC)
    assert invalid.trace_id is None
    assert service.timing_record("ct-test", DOC, "commit", 1, 1.5, 2)["delta_ms"] == 500
    masked = service.timing_record(SECRET, "invalid\n" + BODY, SECRET, 0, 0, 1)
    assert SECRET not in str(masked) and BODY not in str(masked)
