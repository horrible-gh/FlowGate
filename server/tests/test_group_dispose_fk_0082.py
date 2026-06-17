"""Regression: group dispose / close 500 — events FK constraint failure (B0001, group 0082).

NR0003 root cause: group-level terminal events (group_disposed / group_closed) were written
into the document-scoped `events` table with doc_id = group_id, but `events.doc_id` is
`NOT NULL REFERENCES documents(doc_id)`. A group_id can never equal a documents.doc_id, so
with FK enforcement ON (connection.transaction() forces `PRAGMA foreign_keys = ON`) the write
raised `sqlite3.IntegrityError: FOREIGN KEY constraint failed` → 500 on POST
/groups/{group_id}/dispose (and the identical defect in close_group).

These tests run against a REAL temp SQLite DB with all migrations applied and foreign_keys ON,
so they reproduce the exact failure: with the pre-fix code dispose_group()/close_group() raise
IntegrityError; with the fix (dedicated group_events table, migration 048) they succeed.

Environment mirrors test_mention_copies_0015.py but uses the real _SqliteDbAdapter so the
transactional rollback (NR0003 §6 atomicity) is genuinely exercised.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


def _migrations() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


@pytest.fixture()
def real_store():
    """A FlowGateStore backed by a fresh temp SQLite DB with all migrations + FK ON."""
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import connection as conn_mod

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    for mig in _migrations():
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    conn.close()

    original = conn_mod.STORE
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(db_path)
    conn_mod.STORE = store
    try:
        yield store, db_path
    finally:
        conn_mod.STORE = original
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _seed_group(group_id: str, project_id: str = "flowgate", module: str = "default") -> None:
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    db_projects.create({"project_id": project_id, "project_name": project_id})
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": module,
        "title": "Dispose target",
        "status": "OPEN",
    })


def _query(db_path: str, sql: str, params=None) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
    finally:
        conn.close()


def test_dispose_group_succeeds_under_fk_enforcement(real_store):
    """The pre-fix code raised FOREIGN KEY constraint failed here; it must now succeed."""
    from modules.flow_gate import process_service

    _store, db_path = real_store
    gid = "flowgate.default.0082"
    _seed_group(gid)

    result = process_service.dispose_group(gid, reason_option="", reason_detail="no longer needed")

    assert result["status"] == "success", result
    # A file-less DC carrier document was created.
    dc_docs = _query(db_path, "SELECT * FROM documents WHERE group_id=? AND type_code='DC'", [gid])
    assert len(dc_docs) == 1

    # The group-level event lives in group_events (FK → groups), NOT the events table.
    ge = _query(db_path, "SELECT * FROM group_events WHERE group_id=?", [gid])
    assert len(ge) == 1
    assert ge[0]["event_type"] == "group_disposed"
    # Nothing was written into the document-scoped events table for the group id (the bug).
    leaked = _query(db_path, "SELECT * FROM events WHERE doc_id=?", [gid])
    assert leaked == []


def test_dispose_event_is_visible_in_group_detail(real_store):
    """get_group_detail reads group events from the new channel."""
    from modules.flow_gate import process_service

    _store, _db_path = real_store
    gid = "flowgate.default.0083"
    _seed_group(gid)

    process_service.dispose_group(gid, reason_option="", reason_detail="dup")
    detail = process_service.get_group_detail(gid, locale="en")

    assert detail is not None
    types = {e.get("event_type") for e in detail["group_events"]}
    assert "group_disposed" in types


def test_dispose_is_idempotent(real_store):
    """A second dispose is blocked by the existing-DC guard, not by a crash."""
    from modules.flow_gate import process_service

    _store, _db_path = real_store
    gid = "flowgate.default.0084"
    _seed_group(gid)

    assert process_service.dispose_group(gid, reason_detail="x")["status"] == "success"
    again = process_service.dispose_group(gid, reason_detail="x")
    assert again["status"] == "error"
    assert "already disposed" in again["message"]


def test_close_group_succeeds_under_fk_enforcement(real_store):
    """close_group shared the same FK defect (no carrier doc to anchor to); must now succeed."""
    from modules.flow_gate import process_service

    _store, db_path = real_store
    gid = "flowgate.default.0085"
    _seed_group(gid)

    result = process_service.close_group(gid, reason_option="", reason_detail="done")

    assert result["status"] == "success", result
    ge = _query(db_path, "SELECT * FROM group_events WHERE group_id=?", [gid])
    assert len(ge) == 1
    assert ge[0]["event_type"] == "group_closed"
    leaked = _query(db_path, "SELECT * FROM events WHERE doc_id=?", [gid])
    assert leaked == []


def test_dispose_is_atomic_no_zombie_dc_on_event_failure(real_store, monkeypatch):
    """NR0003 §6: if the event write fails mid-dispose, the DC document must roll back.

    Pre-fix the DC doc committed independently and a later failure left a 'zombie' half-
    disposed group that the idempotency guard then refused to retry. The fix wraps the whole
    discard in one transaction, so a forced event failure leaves no DC behind.
    """
    from modules.flow_gate import process_service

    _store, db_path = real_store
    gid = "flowgate.default.0086"
    _seed_group(gid)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated event-write failure")

    monkeypatch.setattr(process_service.db, "insert_group_event", _boom)

    with pytest.raises(RuntimeError):
        process_service.dispose_group(gid, reason_detail="x")

    # The transaction rolled back: no zombie DC document remains, so a retry is possible.
    dc_docs = _query(db_path, "SELECT * FROM documents WHERE group_id=? AND type_code='DC'", [gid])
    assert dc_docs == []
    ge = _query(db_path, "SELECT * FROM group_events WHERE group_id=?", [gid])
    assert ge == []
