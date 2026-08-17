"""이관 완료 대화(CH)의 전체 본문 교체 차단 — 세션 계열 저장 경로 (flowgate.default.0432).

0344 TR0008 후속. 대화의 정본은 ``conversation_turns`` 표로 옮겨졌고(0432.0003-NR §3)
``GET /documents/{doc_id}/content`` 는 이관된 대화에 대해 턴에서 만든 projection 을 준다.
그런데 짝인 ``PATCH /documents/{doc_id}/content`` 에는 대화 판정이 없어, 그 글을 고쳐 저장하면
아무도 읽지 않는 껍데기 파일이 바뀌고 사용자는 "저장했다"는 응답을 받은 채 대화는 하나도
바뀌지 않은 화면을 보게 된다(NR §5·§7-1). 0344.0008-TR 이 이 마무리를 시도했다가 반려된 뒤
후속이 없어 방치돼 있었다(NR §4).

판정 조건은 0344.0005-L §2-16 원문 그대로 — 대화 타입이고 ``migration_state == "migrated"``
일 때만. 이관되지 않은 대화(pending / in_progress / failed)는 아직 파일이 정본이라 걸지 않는다.
봉투는 세션 계열의 현행 형식인 ``HTTPException(detail=...)`` 이다(0344.0004-P §0-5) — 워커 계열의
``ok``/``error_message`` 봉투를 여기에 섞으면 이 라우터를 쓰는 화면의 오류 처리가 통째로 깨진다.

판정 함수는 실제 DB(전체 마이그레이션이 적용된 sqlite)를 읽는다. ``migration_state`` 를 목으로
바꾸면 진짜 게이트가 아니라 목을 시험하게 된다.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

# 0344.0005-L §2-16 원문. 상수를 import 하지 않고 글자 그대로 적는다 — 상수와 시험이 같은
# 곳을 보면 문구가 틀려도 둘이 나란히 틀린다. 이것은 화면이 받는 전선 위의 계약이다.
_MESSAGE = (
    "This conversation no longer accepts a full-body edit. "
    "Append one turn: POST /api/v1/conversation/{doc_id}/turn"
)

_PROJECT = "testfbe"
_GROUP = "testfbe-__ALL__-0001"
_OWNER = "usr_fbe_001"


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
def store(migrated_sqlite_db):
    """전체 마이그레이션이 적용된 sqlite 를 라이브 STORE 자리에 끼운다."""
    from modules.flow_gate.db import connection

    db_path = migrated_sqlite_db("conversation_full_body_edit_0432.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    class _Store(connection.FlowGateStore):
        def __init__(self):
            self._db = _DB(conn)
            self._sq = None

    old = connection.STORE
    connection.STORE = _Store()
    try:
        from modules.flow_gate.db import groups as db_groups, projects, users
        if projects.get_by_id(_PROJECT) is None:
            projects.create({"project_id": _PROJECT, "project_name": "Full-body edit"})
        if users.get_by_id(_OWNER) is None:
            users.create({
                "user_id": _OWNER,
                "username": "fbe_user",
                "email": "fbe@example.com",
                "password": "hashed_pw",
            })
        if db_groups.get_by_id(_GROUP) is None:
            db_groups.create({
                "group_id": _GROUP,
                "project_id": _PROJECT,
                "module": "__ALL__",
                "title": "Full-body edit group",
            })
        yield conn
    finally:
        connection.STORE = old
        conn.close()


def _seed_doc(doc_id: str, stored_path: Path, *, type_code: str = "CH") -> dict:
    from modules.flow_gate.db import documents as db_docs

    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_text("# Original body", encoding="utf-8")
    db_docs.create({
        "doc_id": doc_id,
        "project_id": _PROJECT,
        "type_code": type_code,
        "seq": 1,
        "title": doc_id,
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": _OWNER,
        "file_path": str(stored_path),
        "revision_no": 0,
    })
    return {
        "doc_id": doc_id,
        "project_id": _PROJECT,
        "group_id": _GROUP,
        "type_code": type_code,
        "status": "closed",
        "doc_review_status": "wf_in_progress",
        "file_path": str(stored_path),
    }


def _mark_migrated(doc_id: str) -> None:
    from modules.flow_gate.db import conversation_turns

    assert conversation_turns.acquire_migration_lock(doc_id, "test-owner")
    conversation_turns.mark_migrated(doc_id, "test-owner", "intro", 0)
    assert conversation_turns.migration_state(doc_id) == "migrated"


def _save(monkeypatch, doc: dict, content: str = "wholesale rewrite"):
    """라우터 함수를 직접 부른다 — 이 파일의 이웃(test_document_content_editability.py)이
    쓰는 방식과 같다. 대화 판정만은 목이 아니라 위 store 픽스처의 진짜 DB 를 읽는다."""
    from modules.flow_gate.documents.routers import documents as routes

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: doc)
    monkeypatch.setattr(
        routes.document_service, "update_document",
        lambda _doc_id, _updates, actor_user_id=None: doc,
    )
    monkeypatch.setattr(routes, "_document_file_path", lambda _doc: Path(doc["file_path"]))
    return routes.update_document_content(
        doc["doc_id"],
        routes.DocumentContentUpdate(content=content),
        {"user_id": _OWNER},
    )


def test_migrated_conversation_rejects_a_full_body_save(store, monkeypatch, tmp_path):
    doc = _seed_doc("testfbe-__ALL__-0001-CH0001", tmp_path / "ch1.md")
    _mark_migrated(doc["doc_id"])

    with pytest.raises(HTTPException) as exc_info:
        _save(monkeypatch, doc)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == _MESSAGE.format(doc_id=doc["doc_id"])
    # 껍데기 파일조차 건드리지 않는다.
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "# Original body"


def test_the_rpc_wrapper_takes_the_same_block(store, monkeypatch, tmp_path):
    """PATCH /documents/content (RPC 형태)도 같은 함수를 거치므로 같은 409 다 —
    화면 단추를 지워도 옛 즐겨찾기와 직접 호출이 이 문으로 들어온다."""
    from modules.flow_gate.documents.routers import documents as routes

    doc = _seed_doc("testfbe-__ALL__-0001-CH0002", tmp_path / "ch2.md")
    _mark_migrated(doc["doc_id"])

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: doc)
    monkeypatch.setattr(routes, "_document_file_path", lambda _doc: Path(doc["file_path"]))

    with pytest.raises(HTTPException) as exc_info:
        routes.update_document_content_rpc(
            routes.DocumentContentUpdateRpc(doc_id=doc["doc_id"], content="rewrite"),
            {"user_id": _OWNER},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == _MESSAGE.format(doc_id=doc["doc_id"])
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "# Original body"


def test_legacy_conversation_still_saves(store, monkeypatch, tmp_path):
    """conversation_docs 행이 없는 대화는 pending — 아직 파일이 정본이라 그대로 저장된다."""
    from modules.flow_gate.db import conversation_turns

    doc = _seed_doc("testfbe-__ALL__-0001-CH0003", tmp_path / "ch3.md")
    assert conversation_turns.migration_state(doc["doc_id"]) == "pending"

    result = _save(monkeypatch, doc, content="legacy edit lands")

    assert result["content"] == "legacy edit lands"
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "legacy edit lands"


def test_failed_migration_conversation_still_saves(store, monkeypatch, tmp_path):
    """이관 실패(failed)도 migrated 가 아니다 — 파일이 정본으로 남아 있으므로 막지 않는다."""
    from modules.flow_gate.db import conversation_turns

    doc = _seed_doc("testfbe-__ALL__-0001-CH0004", tmp_path / "ch4.md")
    assert conversation_turns.acquire_migration_lock(doc["doc_id"], "test-owner")
    conversation_turns.mark_failed(doc["doc_id"], "test-owner", "boom")
    assert conversation_turns.migration_state(doc["doc_id"]) == "failed"

    result = _save(monkeypatch, doc, content="failed-migration edit lands")

    assert result["content"] == "failed-migration edit lands"


def test_non_conversation_document_still_saves(store, monkeypatch, tmp_path):
    """대화가 아닌 문서에는 판정이 끼어들지 않는다(회귀)."""
    doc = _seed_doc("testfbe-__ALL__-0001-R0001", tmp_path / "r1.md", type_code="R")

    result = _save(monkeypatch, doc, content="ordinary edit lands")

    assert result["content"] == "ordinary edit lands"
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "ordinary edit lands"
