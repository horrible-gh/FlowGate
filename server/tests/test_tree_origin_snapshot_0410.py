"""flowgate.default.0410 T0008 — backend tests for the origin_provider_name /
origin_ai_run_id snapshot fields on get_group_tree() document nodes.

Covers T0008 완료 기준: a document with a provider/run snapshot and a legacy
document with neither must both carry the two keys (explicit None for the
legacy row, never an omitted key) in the same group's tree nodes.
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


@pytest.fixture(scope="module")
def origin_conn():
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
        "VALUES ('t410proj', 'T410 Test Project', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO groups "
        "(group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES ('t410proj.default.0001', 't410proj', 'default', 'Group A', 'OPEN', ?, ?)",
        (now, now),
    )
    # Legacy document: no snapshot at all (pre-0410 rows / anything created before
    # this feature existed).
    conn.execute(
        "INSERT INTO documents "
        "(doc_id, project_id, module, group_id, type_code, seq, title, status, created_at, updated_at) "
        "VALUES ('t410proj.default.0001.0001-R', 't410proj', 'default', 't410proj.default.0001', "
        " 'R', 1, 'Legacy root', 'open', ?, ?)",
        (now, now),
    )
    # AI-authored document: both snapshot fields populated.
    conn.execute(
        "INSERT INTO documents "
        "(doc_id, project_id, module, group_id, type_code, seq, title, status, created_at, updated_at, "
        " origin_provider_name, origin_ai_run_id) "
        "VALUES ('t410proj.default.0001.0002-T', 't410proj', 'default', 't410proj.default.0001', "
        " 'T', 2, 'AI-authored step', 'open', ?, ?, 'Claude Sonnet 5', 'run_tree_001')",
        (now, now),
    )
    conn.commit()
    yield conn
    conn.close()
    os.unlink(db_path)


def _doc_nodes(origin_conn):
    from unittest.mock import patch

    from modules.flow_gate.db import connection as _conn
    from modules.flow_gate.process_service import get_group_tree

    with patch.object(_conn, "STORE", _Store(origin_conn)):
        result = get_group_tree("t410proj")
    return {n["id"]: n for n in result["nodes"] if n["node_type"] == "document"}


class TestOriginSnapshotTreeNodes:
    def test_snapshot_document_carries_provider_and_run_id(self, origin_conn):
        nodes = _doc_nodes(origin_conn)
        node = nodes["t410proj.default.0001.0002-T"]
        assert node["origin_provider_name"] == "Claude Sonnet 5"
        assert node["origin_ai_run_id"] == "run_tree_001"

    def test_legacy_document_has_explicit_none_not_omitted(self, origin_conn):
        nodes = _doc_nodes(origin_conn)
        node = nodes["t410proj.default.0001.0001-R"]
        assert "origin_provider_name" in node
        assert node["origin_provider_name"] is None
        assert "origin_ai_run_id" in node
        assert node["origin_ai_run_id"] is None

    def test_both_states_render_in_the_same_group_tree(self, origin_conn):
        """The two documents share a group — this is the T0008 완료 기준 scenario
        of a snapshot document and a legacy document appearing side by side."""
        nodes = _doc_nodes(origin_conn)
        assert len(nodes) == 2
        for node in nodes.values():
            assert "origin_provider_name" in node
            assert "origin_ai_run_id" in node
