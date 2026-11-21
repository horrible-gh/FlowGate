"""Combine four return-point queries into one.

Each document response repeated these four reads, observed four times in the the discussion dump:

    SELECT * FROM workflow_return_points WHERE group_id = ?
    SELECT doc_id, seq, type_code, title FROM documents WHERE group_id = ? AND seq = ?
    SELECT MIN(d.seq) ... WHERE s.return_point_id = ? AND d.doc_review_status='pending_review'
    SELECT COUNT(*) FROM workflow_return_point_docs WHERE return_point_id = ?

workflow_return_points.summary() combines them. Verify both actual database round trips
and equivalence with the original four-call composition, which remains available for
standalone callers and serves as the reference behavior.

Cover correlated-subquery/LEFT JOIN boundaries: no return point, empty snapshot,
no pending document (MIN is NULL), and a deleted front document (unmatched LEFT JOIN).
The last two previously yielded None and no label respectively.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as _connection  # noqa: E402
from modules.flow_gate.db import workflow_return_points as db_rp  # noqa: E402
from modules.flow_gate.db.connection import FlowGateStore  # noqa: E402

GROUP_ID = "t2.default.0291"


class _CountingBackend:
    """Real SQLite with a read round-trip counter."""

    db_type = 1  # dialect.SQLITE: translation is a no-op

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
            try:
                self.conn.executescript(sql_file.read_text(encoding="utf-8"))
            except sqlite3.Error:
                # Some migrations concern unused tables and conflict when replayed in certain orders.
                # Only the three tables used here are required.
                pass
        # Verify SQL shape (correlated subqueries, LEFT JOIN, round trips),
        # not referential integrity. Seeding projects/groups/users would exceed
        # the test cases in size and obscure what they verify.
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.commit()
        self.reads: list[str] = []

    def fetch_one(self, sql, params=None):
        self.reads.append(sql)
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        self.reads.append(sql)
        return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    def execute(self, sql, params=None):
        cur = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cur

    def commit(self):
        self.conn.commit()


@pytest.fixture
def backend(monkeypatch):
    be = _CountingBackend()
    store = FlowGateStore.__new__(FlowGateStore)
    store._db = be
    store._sq = None
    # DB modules import get_store into their own namespaces.
    # Patching connection alone leaves those copies pointing at the original store.
    monkeypatch.setattr(_connection, "get_store", lambda: store)
    monkeypatch.setattr(db_rp, "get_store", lambda: store)
    return be


def _doc(be, seq: int, type_code: str, title: str, status: str) -> str:
    doc_id = f"{GROUP_ID}.{seq:04d}-{type_code}"
    be.conn.execute(
        "INSERT INTO documents (doc_id, project_id, type_code, seq, title, group_id, "
        "module, doc_review_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'now','now')",
        [doc_id, "t2", type_code, seq, title, GROUP_ID, "default", status],
    )
    return doc_id


def _return_point(be, front_seq: int) -> int:
    be.conn.execute(
        "INSERT INTO workflow_return_points (group_id, front_seq, created_at, updated_at) "
        "VALUES (?,?,'now','now')",
        [GROUP_ID, front_seq],
    )
    return int(be.conn.execute(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [GROUP_ID]
    ).fetchone()["id"])


def _snapshot(be, rp_id: int, doc_id: str, seq: int) -> None:
    be.conn.execute(
        "INSERT INTO workflow_return_point_docs (return_point_id, doc_id, seq, prev_status, fingerprint) "
        "VALUES (?,?,?,'approved','fp')",
        [rp_id, doc_id, seq],
    )


def _legacy(group_id: str) -> dict | None:
    """Original four-call composition, used as the equivalence reference."""
    rp = db_rp.get_by_group(group_id)
    if rp is None:
        return None
    front = db_rp.get_front_doc(group_id, int(rp["front_seq"]))
    return {
        "id": rp["id"],
        "front_seq": rp["front_seq"],
        "front_title": (front or {}).get("title"),
        "front_type_code": (front or {}).get("type_code"),
        "restorable_count": db_rp.count_docs(rp["id"]),
        "current_min_seq": db_rp.current_pending_min_seq(rp["id"]),
    }


def _assert_matches_legacy(be):
    """Check equal results and the reduction from four round trips to one."""
    be.reads.clear()
    merged = db_rp.summary(GROUP_ID)
    merged_reads = len(be.reads)

    be.reads.clear()
    legacy = _legacy(GROUP_ID)
    legacy_reads = len(be.reads)

    assert merged == legacy, f"합침이 값을 바꿨다\n new={merged}\n old={legacy}"
    assert merged_reads == 1, f"합침 쿼리가 {merged_reads}회 갔다"
    assert legacy_reads > merged_reads, "예전 경로가 더 적게 갔다 — 카운터가 잘못됐다"
    return merged


class TestReturnPointSummary:
    def test_full_payload_in_one_query(self, backend):
        """Normal case: return point, front document, snapshot, and pending document."""
        front = _doc(backend, 8, "T", "Task", "approved")
        pending = _doc(backend, 5, "L", "Logic", "pending_review")
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)
        _snapshot(backend, rp_id, pending, 5)

        merged = _assert_matches_legacy(backend)
        assert merged["front_seq"] == 8
        assert merged["front_title"] == "Task"
        assert merged["restorable_count"] == 2
        assert merged["current_min_seq"] == 5

    def test_absent_return_point(self, backend):
        """No return point yields None without any derived queries."""
        _doc(backend, 8, "T", "Task", "approved")
        backend.reads.clear()
        assert db_rp.summary(GROUP_ID) is None
        assert len(backend.reads) == 1

    def test_no_pending_docs_gives_null_min_seq(self, backend):
        """With all snapshot documents approved, MIN is NULL and current_min_seq is None.

        Completion uses current_pending_min_seq() is None; returning zero or omitting
        the value would prevent clearing the return point.
        """
        front = _doc(backend, 8, "T", "Task", "approved")
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)

        merged = _assert_matches_legacy(backend)
        assert merged["current_min_seq"] is None
        assert merged["restorable_count"] == 1

    def test_empty_snapshot_counts_zero(self, backend):
        """The COUNT subquery must not remove the row when the snapshot is empty."""
        _doc(backend, 8, "T", "Task", "approved")
        _return_point(backend, 8)

        merged = _assert_matches_legacy(backend)
        assert merged["restorable_count"] == 0
        assert merged["current_min_seq"] is None
        assert merged["front_title"] == "Task"

    def test_missing_front_doc_keeps_the_return_point(self, backend):
        """Keep the return point even when its front_seq document no longer exists.

        INNER JOIN would silently drop it, making the UI claim there is nothing to restore.
        """
        rp_id = _return_point(backend, 8)   # Do not create the document at seq=8
        other = _doc(backend, 5, "L", "Logic", "pending_review")
        _snapshot(backend, rp_id, other, 5)

        merged = _assert_matches_legacy(backend)
        assert merged["front_seq"] == 8
        assert merged["front_title"] is None
        assert merged["front_type_code"] is None
        assert merged["current_min_seq"] == 5

    def test_other_groups_pending_docs_do_not_leak_in(self, backend):
        """Restrict MIN to this return point's snapshot documents (0142 rework).

        An unrelated pending document must not lower current_min_seq and change the
        restoration range. Preserve that restriction in the correlated subquery.
        """
        front = _doc(backend, 8, "T", "Task", "approved")
        _doc(backend, 2, "D", "Phantom", "pending_review")   # Pending document outside the snapshot
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)
        snap_pending = _doc(backend, 6, "P", "Protocol", "pending_review")
        _snapshot(backend, rp_id, snap_pending, 6)

        merged = _assert_matches_legacy(backend)
        assert merged["current_min_seq"] == 6, "스냅샷 밖의 pending 문서가 새어 들어왔다"


class TestPayloadWiring:
    def test_router_payload_uses_the_merged_query(self, backend):
        """The router response fragment also takes exactly one round trip, as required by T2."""
        from modules.flow_gate.documents.routers.documents import _return_point_payload

        front = _doc(backend, 8, "T", "Task", "approved")
        pending = _doc(backend, 5, "L", "Logic", "pending_review")
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)
        _snapshot(backend, rp_id, pending, 5)

        backend.reads.clear()
        payload = _return_point_payload(GROUP_ID)
        assert len(backend.reads) == 1, f"{len(backend.reads)}회 갔다: {backend.reads}"
        assert payload == {
            "exists": True,
            "front_seq": 8,
            "front_label": "Task",
            "restorable_count": 2,
            "current_min_seq": 5,
            "destination_default": 8,
            "destination_min": 5,
        }

    def test_absent_return_point_payload_unchanged(self, backend):
        from modules.flow_gate.documents.routers.documents import _return_point_payload

        assert _return_point_payload(GROUP_ID) == {
            "exists": False,
            "front_seq": None,
            "front_label": None,
            "restorable_count": 0,
            "current_min_seq": None,
            "destination_default": None,
            "destination_min": None,
        }

    def test_front_label_falls_back_to_type_code(self, backend):
        """An empty title falls back to type_code, matching the original or chain."""
        from modules.flow_gate.documents.routers.documents import _return_point_payload

        _doc(backend, 8, "AC", "", "approved")
        _return_point(backend, 8)
        assert _return_point_payload(GROUP_ID)["front_label"] == "AC"
