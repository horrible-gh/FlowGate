"""Query budget for document-read responses (flowgate.default.0291 T3).

_parse_doc_workflow first queried the root (type_code=R, limit=1, falling back to B),
then fetched the same group's document list (limit=100). Both sort by updated_at DESC,
so selecting the first R, otherwise B, from the list yields the same row.
Reading the list first reduces root/list queries per response from at most three to one.

Equivalence requires an untruncated list. With more than 100 documents, the root may
fall outside the page, requiring the original narrow query. Without that fallback,
parent_root_doc_id silently disappears and leaves the workflow strip and action bar empty.

Count actual DB trips with request-scoped caching disabled. Otherwise cache hits hide
the difference between one and two caller queries, which is the behavior changed here.
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

PROJECT_ID = "t3"
GROUP_ID = "t3.default.0291"

_GROUP_LIST_SQL = "SELECT * FROM documents WHERE project_id = ? AND group_id = ? ORDER BY"
_NARROW_ROOT_SQL = "AND type_code = ? ORDER BY"


class _Backend:
    """Real SQLite with a read round-trip log."""

    db_type = 1  # dialect.SQLITE: translation is a no-op

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        for f in sorted(_SCHEMA_DIR.glob("*.sql")):
            try:
                self.conn.executescript(f.read_text(encoding="utf-8"))
            except sqlite3.Error:
                pass
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

    # ── seed helpers ────────────────────────────────────────────────────────
    def doc(self, seq: int, type_code: str, title: str, status: str, updated_at: str = "2026-07-01") -> str:
        doc_id = f"{GROUP_ID}.{seq:04d}-{type_code}"
        self.conn.execute(
            "INSERT INTO documents (doc_id, project_id, type_code, seq, title, group_id, "
            "module, doc_review_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [doc_id, PROJECT_ID, type_code, seq, title, GROUP_ID, "default", status,
             "2026-07-01", updated_at],
        )
        self.conn.commit()
        return doc_id

    def doc_reads(self) -> list[str]:
        return [s for s in self.reads if "FROM documents" in s]


@pytest.fixture
def app_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.db import connection as conn_mod

    be = _Backend()

    class _Patched(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = be
            self._sq = None

    original = conn_mod.STORE
    conn_mod.STORE = _Patched()

    # Disable request-scoped caching to measure caller query counts.
    monkeypatch.setenv("FLOWGATE_REQUEST_CACHE", "0")

    be.conn.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?,?,'now','now')",
        [PROJECT_ID, "T3"],
    )
    be.conn.execute(
        "INSERT INTO groups (group_id, project_id, module, title, created_at, updated_at) "
        "VALUES (?,?,?,?,'now','now')",
        [GROUP_ID, PROJECT_ID, "default", "G"],
    )
    be.conn.commit()

    from modules.flow_gate.auth import get_current_user
    from modules.flow_gate.documents.routers.documents import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u1", "is_admin": 1, "is_active": 1,
    }
    try:
        yield TestClient(app), be
    finally:
        conn_mod.STORE = original


class TestGroupListIsReadOnce:
    def test_root_lookup_reuses_the_group_listing(self, app_client):
        """The list absorbs root lookup: two document-table trips remain, body and list."""
        client, be = app_client
        root = be.doc(1, "R", "Root", "wf_in_progress")
        target = be.doc(5, "TR", "Report", "pending_review")

        be.reads.clear()
        resp = client.get(f"/documents/{target}")
        assert resp.status_code == 200

        reads = be.doc_reads()
        assert not [s for s in reads if _NARROW_ROOT_SQL in s], \
            f"좁은 루트 쿼리가 아직 돈다: {reads}"
        assert len([s for s in reads if _GROUP_LIST_SQL in s]) == 1, \
            f"그룹 목록을 두 번 이상 읽는다: {reads}"
        assert len(reads) == 2, f"documents 왕복 {len(reads)}회: {reads}"

        body = resp.json()
        assert body["parent_root_doc_id"] == root
        assert body["parent_r_doc_id"] == root
        assert body["workflow_root_type"] == "R"

    def test_b_root_is_found_in_the_listing_too(self, app_client):
        """Without R, fall back to B, preserving the original two-stage lookup."""
        client, be = app_client
        root = be.doc(1, "B", "Bug root", "wf_in_progress")
        target = be.doc(5, "TR", "Report", "pending_review")

        be.reads.clear()
        body = client.get(f"/documents/{target}").json()

        assert not [s for s in be.doc_reads() if _NARROW_ROOT_SQL in s]
        assert body["parent_root_doc_id"] == root
        assert body["workflow_root_type"] == "B"

    def test_r_wins_over_b_when_both_exist(self, app_client):
        """Prefer R over B, preserving fallback order when filtering the list."""
        client, be = app_client
        r_root = be.doc(1, "R", "Root", "wf_in_progress")
        be.doc(2, "B", "Bug root", "wf_in_progress")
        target = be.doc(5, "TR", "Report", "pending_review")

        body = client.get(f"/documents/{target}").json()
        assert body["parent_root_doc_id"] == r_root
        assert body["workflow_root_type"] == "R"

    def test_group_without_a_root_asks_once_and_gives_up(self, app_client):
        """A rootless group with an untruncated list needs no narrow confirmation query."""
        client, be = app_client
        target = be.doc(5, "TR", "Report", "pending_review")

        be.reads.clear()
        body = client.get(f"/documents/{target}").json()

        assert not [s for s in be.doc_reads() if _NARROW_ROOT_SQL in s], \
            "없다는 것이 이미 확정인데 좁은 쿼리를 또 돌렸다"
        assert body.get("parent_root_doc_id") is None

    def test_root_document_itself_does_not_look_for_a_parent(self, app_client):
        """Reading the R document itself requires no root search."""
        client, be = app_client
        root = be.doc(1, "R", "Root", "wf_in_progress")

        be.reads.clear()
        body = client.get(f"/documents/{root}").json()

        assert body["workflow_root_type"] == "R"
        assert len(be.doc_reads()) == 2


class TestTruncatedListingFallsBack:
    """A list truncated at 100 rows may exclude the root.

    Without fallback, parent_root_doc_id silently disappears in large groups,
    leaving the workflow strip and action bar empty with no error or log.
    """

    def _seed_big_group(self, be) -> tuple[str, str]:
        # The list returns only 100 rows ordered by updated_at DESC. Make the root oldest
        # so it falls outside that page.
        root = be.doc(1, "R", "Root", "wf_in_progress", updated_at="2020-01-01")
        for i in range(120):
            be.doc(100 + i, "M", f"Memo {i}", "approved", updated_at=f"2026-07-{(i % 28) + 1:02d}")
        target = be.doc(5, "TR", "Report", "pending_review", updated_at="2026-12-31")
        return root, target

    def test_root_outside_the_first_page_is_still_found(self, app_client):
        client, be = app_client
        root, target = self._seed_big_group(be)

        be.reads.clear()
        body = client.get(f"/documents/{target}").json()

        assert [s for s in be.doc_reads() if _NARROW_ROOT_SQL in s], \
            "목록이 잘렸는데 좁은 쿼리로 확인하지 않았다"
        assert body["parent_root_doc_id"] == root, "큰 그룹에서 루트를 잃었다"
        assert body["workflow_root_type"] == "R"

    def test_root_inside_the_first_page_still_costs_one_query(self, app_client):
        """Even a truncated list needs no extra query when its first page contains the root."""
        client, be = app_client
        root = be.doc(1, "R", "Root", "wf_in_progress", updated_at="2026-12-30")
        for i in range(120):
            be.doc(100 + i, "M", f"Memo {i}", "approved", updated_at="2020-01-01")
        target = be.doc(5, "TR", "Report", "pending_review", updated_at="2026-12-31")

        be.reads.clear()
        body = client.get(f"/documents/{target}").json()

        assert not [s for s in be.doc_reads() if _NARROW_ROOT_SQL in s]
        assert body["parent_root_doc_id"] == root


class TestHeadResolutionUnchanged:
    """Moving the list query earlier must preserve head selection."""

    def test_earliest_unapproved_step_is_the_head(self, app_client):
        client, be = app_client
        be.doc(1, "R", "Root", "wf_in_progress")
        be.doc(4, "D", "Design", "approved")
        head = be.doc(6, "P", "Protocol", "pending_review")
        be.doc(7, "T", "Task", "pending_review")
        target = be.doc(5, "TR", "Report", "approved")

        body = client.get(f"/documents/{target}").json()
        assert body["workflow_head_doc_id"] == head
        assert body["workflow_head_status"] == "in_progress"

    def test_head_resolution_survives_a_failing_listing(self, app_client, monkeypatch):
        """Return a response even if list lookup fails.

        NON_HEAD_TYPES and APPROVED_STATUSES used to be defined inside the lookup try
        block. An exception then caused NameError during head selection and a 500.
        Move both constants outside that block.
        """
        client, be = app_client
        be.doc(1, "R", "Root", "wf_in_progress")
        target = be.doc(5, "TR", "Report", "pending_review")

        from modules.flow_gate.db import documents as _db_docs

        original = _db_docs.list_documents

        def _boom(*a, **kw):
            raise RuntimeError("listing unavailable")

        monkeypatch.setattr(_db_docs, "list_documents", _boom)
        try:
            resp = client.get(f"/documents/{target}")
        finally:
            monkeypatch.setattr(_db_docs, "list_documents", original)

        assert resp.status_code == 200, resp.text
        assert resp.json()["doc_id"] == target
