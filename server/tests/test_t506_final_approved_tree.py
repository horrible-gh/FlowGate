"""T506 — backend tests for the final-approved group flag in get_group_tree.

Covers D0002 §2 / §3 / §9:
  1. A group whose owning R doc is wf_done  → is_final_approved: True
  2. A group whose R doc is wf_in_progress  → is_final_approved: False
  3. A group with no R doc                  → is_final_approved: False
  4. approved / closed are NOT treated as final approval
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _Store:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    def _execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()


def _insert_group(conn, group_id, module, title, now):
    conn.execute(
        "INSERT OR IGNORE INTO groups "
        "(group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES (?, 't506proj', ?, ?, 'OPEN', ?, ?)",
        (group_id, module, title, now, now),
    )


_seq_counter = [0]


def _insert_doc(conn, doc_id, group_id, type_code, review_status, status, now):
    _seq_counter[0] += 1
    conn.execute(
        "INSERT INTO documents "
        "(doc_id, project_id, module, group_id, type_code, seq, title, status, "
        " doc_review_status, created_at, updated_at) "
        "VALUES (?, 't506proj', 'default', ?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, group_id, type_code, _seq_counter[0], f"{type_code} doc", status, review_status, now, now),
    )


@pytest.fixture(scope="module")
def t506_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    for migration_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            conn.executescript(migration_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES ('t506proj', 'T506 Test Project', ?, ?)",
        (now, now),
    )

    # Group A: R is wf_done → final approved
    _insert_group(conn, "t506proj.default.0001", "default", "Group A (done)", now)
    _insert_doc(conn, "t506proj.default.0001.0001-R", "t506proj.default.0001", "R", "wf_done", "closed", now)
    _insert_doc(conn, "t506proj.default.0001.0002-D", "t506proj.default.0001", "D", "approved", "draft", now)

    # Group B: R still in progress → not final approved
    _insert_group(conn, "t506proj.default.0002", "default", "Group B (in progress)", now)
    _insert_doc(conn, "t506proj.default.0002.0001-R", "t506proj.default.0002", "R", "wf_in_progress", "open", now)

    # Group C: no R doc at all → not final approved
    _insert_group(conn, "t506proj.default.0003", "default", "Group C (no R)", now)
    _insert_doc(conn, "t506proj.default.0003.0001-D", "t506proj.default.0003", "D", "approved", "draft", now)

    # Group D: an individual approved/closed D should NOT count as final approval
    _insert_group(conn, "t506proj.default.0004", "default", "Group D (approved D only)", now)
    _insert_doc(conn, "t506proj.default.0004.0001-T", "t506proj.default.0004", "T", "approved", "closed", now)

    conn.commit()
    yield conn
    conn.close()
    os.unlink(db_path)


def _tree(t506_conn):
    from unittest.mock import patch

    from modules.flow_gate.db import connection as _conn
    from modules.flow_gate.process_service import get_group_tree

    with patch.object(_conn, "STORE", _Store(t506_conn)):
        result = get_group_tree("t506proj")
    return {n["id"]: n for n in result["nodes"] if n["node_type"] == "group"}


class TestFinalApprovedFlag:
    def test_wf_done_group_is_final_approved(self, t506_conn):
        groups = _tree(t506_conn)
        assert groups["t506proj.default.0001"]["is_final_approved"] is True

    def test_in_progress_group_not_final_approved(self, t506_conn):
        groups = _tree(t506_conn)
        assert groups["t506proj.default.0002"]["is_final_approved"] is False

    def test_group_without_r_not_final_approved(self, t506_conn):
        groups = _tree(t506_conn)
        assert groups["t506proj.default.0003"]["is_final_approved"] is False

    def test_individual_approved_does_not_count(self, t506_conn):
        groups = _tree(t506_conn)
        assert groups["t506proj.default.0004"]["is_final_approved"] is False

    def test_flag_present_on_every_group_node(self, t506_conn):
        groups = _tree(t506_conn)
        assert groups, "no group nodes returned"
        for node in groups.values():
            assert "is_final_approved" in node
            assert isinstance(node["is_final_approved"], bool)
