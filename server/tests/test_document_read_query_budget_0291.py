"""문서 조회 응답의 쿼리 예산 (flowgate.default.0291 T3).

`_parse_doc_workflow` 는 워크플로 루트를 좁은 쿼리로 따로 읽은 뒤(``type_code=R,
limit=1``, 없으면 ``type_code=B, limit=1``), 곧바로 **같은 그룹의 문서 목록을 통째로**
다시 읽었다(``limit=100``). 뒤엣것이 앞엣것을 완전히 포함한다 — 정렬(``updated_at
DESC``)까지 같은 쿼리라, 목록에서 첫 R(없으면 첫 B)을 고르면 좁은 쿼리와 **같은 행**이다.

그래서 목록을 먼저 읽고 루트를 거기서 뽑는다. 문서 응답당 최대 3 → 1.

전제가 하나 있고, 이 스위트의 절반이 그것을 지킨다: **목록이 잘리지 않았을 때만 같다.**
그룹 문서가 100건을 넘으면 루트가 그 100건 밖에 있을 수 있으므로 예전의 좁은 쿼리로
떨어져야 한다. 이 폴백이 빠지면 큰 그룹에서 ``parent_root_doc_id`` 가 조용히 사라지고,
그러면 워크플로 스트립과 액션바가 통째로 비는데 예외도 로그도 남지 않는다.

계측은 **실제 DB 왕복**을 센다. 요청 스코프 캐시(0291 P3-1)를 끄고 잰다 — 캐시가 켜져
있으면 중복 조회가 흡수돼서 "호출부가 두 번 부른다" 와 "한 번 부른다" 가 같은 수치로
보인다. 이 작업이 고친 것은 호출부이므로 캐시 없이 재야 한다.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

PROJECT_ID = "t3"
GROUP_ID = "t3.default.0291"

_GROUP_LIST_SQL = "SELECT * FROM documents WHERE project_id = ? AND group_id = ? ORDER BY"
_NARROW_ROOT_SQL = "AND type_code = ? ORDER BY"


class _Backend:
    """진짜 SQLite + 읽기 왕복 로그."""

    db_type = 1  # dialect.SQLITE — translate 는 no-op

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

    # 요청 스코프 캐시를 끈다 — 이 스위트가 재는 것은 호출부의 조회 횟수다.
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
        """루트 조회가 목록에 흡수된다 — documents 테이블 왕복은 2회(본문 + 목록)."""
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
        """R 이 없으면 B 로 떨어진다 — 예전 2단 폴백과 같은 결과."""
        client, be = app_client
        root = be.doc(1, "B", "Bug root", "wf_in_progress")
        target = be.doc(5, "TR", "Report", "pending_review")

        be.reads.clear()
        body = client.get(f"/documents/{target}").json()

        assert not [s for s in be.doc_reads() if _NARROW_ROOT_SQL in s]
        assert body["parent_root_doc_id"] == root
        assert body["workflow_root_type"] == "B"

    def test_r_wins_over_b_when_both_exist(self, app_client):
        """R 이 있으면 B 는 보지 않는다 — 예전 폴백 순서를 목록 필터에서도 지킨다."""
        client, be = app_client
        r_root = be.doc(1, "R", "Root", "wf_in_progress")
        be.doc(2, "B", "Bug root", "wf_in_progress")
        target = be.doc(5, "TR", "Report", "pending_review")

        body = client.get(f"/documents/{target}").json()
        assert body["parent_root_doc_id"] == r_root
        assert body["workflow_root_type"] == "R"

    def test_group_without_a_root_asks_once_and_gives_up(self, app_client):
        """루트가 없는 그룹: 목록이 잘리지 않았으므로 좁은 쿼리로 확인할 필요가 없다."""
        client, be = app_client
        target = be.doc(5, "TR", "Report", "pending_review")

        be.reads.clear()
        body = client.get(f"/documents/{target}").json()

        assert not [s for s in be.doc_reads() if _NARROW_ROOT_SQL in s], \
            "없다는 것이 이미 확정인데 좁은 쿼리를 또 돌렸다"
        assert body.get("parent_root_doc_id") is None

    def test_root_document_itself_does_not_look_for_a_parent(self, app_client):
        """R 문서 자신을 조회할 때는 루트 탐색 자체가 없다."""
        client, be = app_client
        root = be.doc(1, "R", "Root", "wf_in_progress")

        be.reads.clear()
        body = client.get(f"/documents/{root}").json()

        assert body["workflow_root_type"] == "R"
        assert len(be.doc_reads()) == 2


class TestTruncatedListingFallsBack:
    """목록이 100건에서 잘리면 루트가 그 밖에 있을 수 있다.

    폴백이 없으면 큰 그룹에서 ``parent_root_doc_id`` 가 조용히 사라진다 — 예외도 로그도
    없이 워크플로 스트립과 액션바만 비는, 알아채기 어려운 회귀다.
    """

    def _seed_big_group(self, be) -> tuple[str, str]:
        # 목록은 updated_at DESC 로 100건만 가져간다. 루트를 가장 오래된 것으로 만들어
        # 그 100건 밖으로 밀어낸다.
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
        """잘렸더라도 루트가 첫 페이지 안에 있으면 추가 조회는 없다."""
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
    """목록을 위로 옮기면서 head 판정이 달라지지 않았는지."""

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
        """목록 조회가 실패해도 응답은 나와야 한다.

        예전에는 ``NON_HEAD_TYPES`` / ``APPROVED_STATUSES`` 가 조회 try 블록 **안**에서
        정의됐다. 조회가 예외로 끝나면 아래 head 판정이 NameError 로 터져서, 목록을
        못 읽은 것이 500 이 됐다. 두 상수를 블록 밖으로 뺐다.
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
