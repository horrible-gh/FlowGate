"""Conversation turn store invariants (flowgate.default.0351)."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import conversation_turns as turns


class _Txn:
    def __init__(self, conn):
        self.conn = conn
        self.cursor = None

    def execute(self, sql, params=None):
        self.cursor = self.conn.execute(sql, params or [])

    def fetchone(self):
        return self.cursor.fetchone() if self.cursor else None

    def fetchall(self):
        return self.cursor.fetchall() if self.cursor else []


class _DB:
    db_type = 1

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        cur = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cur

    def fetch_one(self, sql, params=None):
        return self.conn.execute(sql, params or []).fetchone()

    def fetch_all(self, sql, params=None):
        return self.conn.execute(sql, params or []).fetchall()

    @contextmanager
    def begin_transaction(self):
        self.conn.execute("BEGIN")
        try:
            yield _Txn(self.conn)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


@pytest.fixture
def store(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents(doc_id TEXT PRIMARY KEY)")
    sql = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite/074_conversation_turns.sql"
    conn.executescript(sql.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO documents(doc_id) VALUES ('chat')")
    conn.commit()

    class Store(connection.FlowGateStore):
        def __init__(self):
            self._db = _DB(conn)
            self._sq = None

    old = connection.STORE
    connection.STORE = Store()
    yield conn
    connection.STORE = old
    conn.close()


def _insert(doc: str, key: str, participant: str, based: int = 0):
    digest = hashlib.sha256(key.encode()).hexdigest()
    body_hash = hashlib.sha256(key.upper().encode()).hexdigest()
    return turns.insert_turn_with_next_seq(
        doc_id=doc, speaker="user", participant_key=participant,
        display_name=participant, locale="ko", body=key.upper(), body_hash=body_hash,
        based_on_seq=based, source_run_id=None, idempotency_key=key,
        idempotency_hash=digest, created_at="2026-01-01T00:00:00+09:00",
    )


def test_sequence_idempotency_stale_and_participant_cursors(store):
    with connection.get_store().transaction():
        first = _insert("chat", "sess_0001", "user:a")
        turns.touch_participant(
            doc_id="chat", participant_key="user:a", kind="user", display_name="A",
            written_seq=1, read_upto=0,
        )
    with connection.get_store().transaction():
        second = _insert("chat", "sess_0002", "user:b", based=0)
        stale = turns.compute_stale_since("chat", 0, int(second["seq"]), "user:b")
        turns.set_stale_since("chat", int(second["seq"]), stale)
        turns.touch_participant(
            doc_id="chat", participant_key="user:b", kind="user", display_name="B",
            written_seq=2, read_upto=0,
        )
    assert (first["seq"], second["seq"]) == (1, 2)
    assert stale == 1
    assert turns.current_head_seq("chat") == 2
    assert turns.get_turn_by_idempotency_hash("chat", hashlib.sha256(b"sess_0001").hexdigest())["seq"] == 1

    turns.touch_participant(
        doc_id="chat", participant_key="user:a", kind="user", display_name="A", read_upto=9
    )
    turns.touch_participant(
        doc_id="chat", participant_key="user:a", kind="user", display_name="A", viewed_upto=5
    )
    row = turns.get_participant("chat", "user:a")
    assert row["last_read_seq"] == 9
    assert row["last_viewed_seq"] == 5


def test_participant_key_is_deterministic_and_bounded():
    value = "한" * 300
    one = turns.compose_participant_key("provider", value)
    two = turns.compose_participant_key("provider", value)
    assert one == two
    assert len(one) == 160
    assert "~" in one[-13:]


def test_migration_lock_and_failed_cleanup_are_document_atomic(store):
    owner = "worker-a"
    assert turns.acquire_migration_lock("chat", owner)
    assert not turns.acquire_migration_lock("chat", "worker-b")
    with connection.get_store().transaction():
        _insert("chat", "migrate:chat:1", "user:a")
        turns.touch_participant(
            doc_id="chat", participant_key="user:a", kind="user", display_name="A",
            written_seq=1,
        )
    turns.mark_failed("chat", owner, "boom")
    assert turns.migration_state("chat") == "failed"
    assert turns.current_head_seq("chat") == 0
    assert turns.list_participants("chat") == []