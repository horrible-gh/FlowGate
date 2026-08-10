"""Document-bound query/answer container — integration tests (group 0022 Q/A/V revamp).

Covers DB0006 §3/§4 + L0007 §3 against a real (in-memory file) sqlite with all migrations:
  - ensure_container lazy create + idempotency (keyed by doc_id, q_id := doc_id)
  - add_questions human / ai (asker_kind, title), done→pending re-open
  - register_answer atomicity + author_kind/author_id (AI → author_id NULL)
  - status pending→done when all items answered
  - qa_bundle_by_doc / list_open_items
  - migration 040 retires Q/A/V doc types (is_active=0)
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

import json as _json

_QUERIES: dict = {}
raw = _json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
for section, entries in raw.items():
    if isinstance(entries, dict):
        for key, sql in entries.items():
            if isinstance(sql, str):
                _QUERIES[f"{section}.{key}"] = sql.replace("%s", "?")


class _MockDB:
    def __init__(self, path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])

    def fetchone(self):
        if self._cur is None:
            return None
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        if self._cur is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]


@pytest.fixture()
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    mock_db = _MockDB(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()

    from modules.flow_gate.db import connection as conn_mod
    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key):
            return _QUERIES[key]

    conn_mod.STORE = _PatchedStore()

    # minimal fixtures: project, user, group, doc
    from modules.flow_gate.db import projects, users, groups, documents as db_docs
    projects.create({"project_id": "p", "project_name": "P"})
    users.create({"user_id": "u1", "username": "u1", "email": "u1@e", "password": "x"})
    groups.create({"group_id": "p.none.0001", "project_id": "p", "module": "none", "title": "G"})
    md = Path(path).with_suffix(".doc.md")
    md.write_text("# d", encoding="utf-8")
    store_obj = conn_mod.STORE
    now = "2026-06-13T00:00:00Z"
    store_obj._execute(
        "INSERT OR IGNORE INTO document_types (project_id,type_code,type_name,series,is_system,is_active,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "D", "Design", "design", 1, 1, 0, now, now],
    )
    db_docs.create({
        "doc_id": "p.none.0001.0001-D", "project_id": "p", "type_code": "D", "seq": 1,
        "title": "Doc One", "group_id": "p.none.0001", "module": "none",
        "owner_id": "u1", "file_path": str(md), "status": "open",
    })
    yield mock_db
    conn_mod.STORE = original
    mock_db.close()
    os.unlink(path)


DOC = "p.none.0001.0001-D"


def test_ensure_container_lazy_and_idempotent(store):
    from modules.flow_gate.services import q_service
    c1 = q_service.ensure_container(DOC, created_by="u1")
    assert c1["doc_id"] == DOC
    assert c1["q_id"] == DOC          # DB0006 §3.2: q_id := doc_id
    c2 = q_service.ensure_container(DOC, created_by="u1")
    assert c2["id"] == c1["id"]       # same row, no duplicate
    rows = store.fetch_all("SELECT id FROM questions WHERE doc_id=?", [DOC])
    assert len(rows) == 1


def test_add_questions_human_then_answer_transitions_done(store):
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"title": "팔레트", "body": "A or B?"}],
                                  asker_kind="human", created_by="u1")
    item_id = res["added_item_ids"][0]
    bundle = q_service.qa_bundle_by_doc(DOC)
    assert bundle[0]["asker_kind"] == "human"
    assert bundle[0]["answer_body"] is None     # awaiting answer

    ans = q_service.register_answer(DOC, item_id, "B로 진행", author_kind="human", author_id="u1")
    assert ans["status"] == "done"              # answering the single item → done
    bundle2 = q_service.qa_bundle_by_doc(DOC)
    assert bundle2[0]["answer_body"] == "B로 진행"
    assert bundle2[0]["author_kind"] == "human"


def test_ai_question_uses_system_user_and_ai_answer_nulls_author(store):
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"title": "범위", "body": "scope?"}], asker_kind="ai")
    container = store.fetch_one("SELECT created_by FROM questions WHERE doc_id=?", [DOC])
    assert container["created_by"] == "u-system"     # §3.1 reserved system user
    item_id = res["added_item_ids"][0]

    q_service.register_answer(DOC, item_id, "AI says", author_kind="ai", author_id="ignored")
    row = store.fetch_one(
        "SELECT author_kind, author_id FROM answers WHERE question_item_id=?", [item_id])
    assert row["author_kind"] == "ai"
    assert row["author_id"] is None                  # AI → author_id NULL


def test_open_q_doc_ids_uses_real_unanswered_container_items(store):
    from modules.flow_gate.services import ai_invoke_service, q_service

    added = q_service.add_questions(
        DOC,
        [{"body": "first?"}, {"body": "second?"}],
        asker_kind="human",
        created_by="u1",
    )
    assert ai_invoke_service._open_q_doc_ids("p.none.0001") == [DOC]

    q_service.register_answer(
        DOC, added["added_item_ids"][0], "first answer",
        author_kind="human", author_id="u1",
    )
    assert ai_invoke_service._open_q_doc_ids("p.none.0001") == [DOC]

    q_service.register_answer(
        DOC, added["added_item_ids"][1], "second answer",
        author_kind="human", author_id="u1",
    )
    assert ai_invoke_service._open_q_doc_ids("p.none.0001") == []


def test_requestion_reopens_done_container(store):
    from modules.flow_gate.services import q_service
    r1 = q_service.add_questions(DOC, [{"body": "q1"}], asker_kind="human", created_by="u1")
    q_service.register_answer(DOC, r1["added_item_ids"][0], "a1", author_kind="human", author_id="u1")
    assert store.fetch_one("SELECT status FROM questions WHERE doc_id=?", [DOC])["status"] == "done"
    # re-question = new item → done → pending
    q_service.add_questions(DOC, [{"body": "q2"}], asker_kind="human", created_by="u1")
    assert store.fetch_one("SELECT status FROM questions WHERE doc_id=?", [DOC])["status"] == "pending"


def test_list_open_items(store):
    from modules.flow_gate.services import q_service
    q_service.add_questions(DOC, [{"title": "열린질의", "body": "open?"}],
                            asker_kind="human", created_by="u1")
    items = q_service.list_open_items(project_id="p")
    assert any(it["doc_id"] == DOC and it["title"] == "열린질의" for it in items)
    # TR0005 rework: each row carries the host document's type_code so the dashboard
    # opens the real document (not a Q-tree viewer). DOC is a 'D' document.
    row = next(it for it in items if it["doc_id"] == DOC)
    assert row["type_code"] == "D"
    # once answered, it drops off the list
    bundle = q_service.get_qa_detail(DOC)
    item_id = bundle["items"][0]["id"]
    q_service.register_answer(DOC, item_id, "answered", author_kind="human", author_id="u1")
    items2 = q_service.list_open_items(project_id="p")
    assert not any(it["doc_id"] == DOC for it in items2)


def test_list_open_items_excludes_completed_or_inactive_groups_but_keeps_history(store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import q_service

    def create_doc(doc_id, group_id, title, type_code="D", seq=1) -> None:
        db_docs.create({
            "doc_id": doc_id, "project_id": "p", "type_code": type_code, "seq": seq,
            "title": title, "group_id": group_id, "module": "none",
            "owner_id": "u1", "status": "open",
        })

    # (A) Question on the wf_done document itself → excluded (per-document guard).
    wf_done_doc = "p.none.0001.0002-D"
    create_doc(wf_done_doc, "p.none.0001", "Workflow Done", type_code="D", seq=2)
    q_service.add_questions(wf_done_doc, [{"title": "완료문서질의", "body": "done?"}],
                            asker_kind="human", created_by="u1")
    store.execute("UPDATE documents SET doc_review_status='wf_done' WHERE doc_id=?", [wf_done_doc])

    # (B) Manually closed group → excluded.
    db_groups.create({
        "group_id": "p.none.0002", "project_id": "p", "module": "none",
        "title": "Closed", "status": "CLOSED", "closed_at": "2026-06-13T00:00:00Z",
    })
    closed_doc = "p.none.0002.0001-D"
    create_doc(closed_doc, "p.none.0002", "Closed Group")
    q_service.add_questions(closed_doc, [{"title": "종료그룹질의", "body": "closed?"}],
                            asker_kind="human", created_by="u1")

    # (C) Soft-deleted group → excluded.
    db_groups.create({
        "group_id": "p.none.0003", "project_id": "p", "module": "none",
        "title": "Deleted",
    })
    deleted_doc = "p.none.0003.0001-D"
    create_doc(deleted_doc, "p.none.0003", "Deleted Group")
    q_service.add_questions(deleted_doc, [{"title": "삭제그룹질의", "body": "deleted?"}],
                            asker_kind="human", created_by="u1")
    store.execute("UPDATE groups SET deleted_at='2026-06-13T00:00:00Z' WHERE group_id='p.none.0003'")

    # (D) B0001 core case: a COMPLETED workflow group whose root R is wf_done, but
    #     whose still-open question lives on a NON-root child doc (T, merely
    #     'approved'). The group was never manually closed (status stays 'draft').
    #     Keying the exclusion off the group root — not the child doc — is what
    #     drops this. A per-document wf_done check (rev1) let it leak through.
    db_groups.create({
        "group_id": "p.none.0004", "project_id": "p", "module": "none",
        "title": "Completed Workflow",
    })
    create_doc("p.none.0004.0001-R", "p.none.0004", "Root", type_code="R", seq=1)
    store.execute("UPDATE documents SET doc_review_status='wf_done' WHERE doc_id=?",
                  ["p.none.0004.0001-R"])
    completed_child_doc = "p.none.0004.0002-T"
    create_doc(completed_child_doc, "p.none.0004", "Child Task", type_code="T", seq=2)
    store.execute("UPDATE documents SET doc_review_status='approved' WHERE doc_id=?",
                  [completed_child_doc])
    q_service.add_questions(completed_child_doc, [{"title": "완료그룹자식질의", "body": "child?"}],
                            asker_kind="human", created_by="u1")

    # (E) Active-group guard: identical shape to (D) but the root R is still
    #     wf_in_progress, so the group is NOT complete and its child question must
    #     REMAIN visible. Prevents the group-level filter from over-suppressing.
    db_groups.create({
        "group_id": "p.none.0005", "project_id": "p", "module": "none",
        "title": "Active Workflow",
    })
    create_doc("p.none.0005.0001-R", "p.none.0005", "Root", type_code="R", seq=1)
    store.execute("UPDATE documents SET doc_review_status='wf_in_progress' WHERE doc_id=?",
                  ["p.none.0005.0001-R"])
    active_child_doc = "p.none.0005.0002-T"
    create_doc(active_child_doc, "p.none.0005", "Child Task", type_code="T", seq=2)
    store.execute("UPDATE documents SET doc_review_status='approved' WHERE doc_id=?",
                  [active_child_doc])
    q_service.add_questions(active_child_doc, [{"title": "활성그룹자식질의", "body": "child?"}],
                            asker_kind="human", created_by="u1")

    # (F) Plain active document/group → kept.
    active_doc = "p.none.0001.0003-D"
    create_doc(active_doc, "p.none.0001", "Active", type_code="D", seq=3)
    q_service.add_questions(active_doc, [{"title": "활성질의", "body": "active?"}],
                            asker_kind="human", created_by="u1")

    items = q_service.list_open_items(project_id="p")
    returned_doc_ids = {it["doc_id"] for it in items}
    # kept: genuinely active work
    assert active_doc in returned_doc_ids
    assert active_child_doc in returned_doc_ids
    # excluded: completed / inactive work (incl. the B0001 child-doc leak)
    assert wf_done_doc not in returned_doc_ids
    assert closed_doc not in returned_doc_ids
    assert deleted_doc not in returned_doc_ids
    assert completed_child_doc not in returned_doc_ids

    # History is preserved: the document-scoped Q&A detail still returns the past
    # questions for both the wf_done doc and the completed group's child doc.
    assert q_service.get_qa_detail(wf_done_doc)["items"][0]["title"] == "완료문서질의"
    assert q_service.get_qa_detail(completed_child_doc)["items"][0]["title"] == "완료그룹자식질의"


def test_migration_040_retires_qav(store):
    rows = store.fetch_all(
        "SELECT type_code, is_active FROM document_types WHERE type_code IN ('Q','A','V')")
    assert rows  # rows preserved
    assert all(r["is_active"] == 0 for r in rows)


# ── Q 선택지 (group 0243 R0001 / D0006 / DB0007 / L0008) ────────────────────────

def test_options_stored_with_server_assigned_ids_and_returned_parsed(store):
    """등록된 선택지는 o1..oN id를 부여받아 저장되고, 조회 시 파싱된 배열로 나온다 (L0008 §2.3/§2.1)."""
    from modules.flow_gate.services import q_service
    q_service.add_questions(
        DOC, [{"title": "배포", "body": "어느 쪽?", "options": ["무중단 배포", "점검창 배포"]}],
        asker_kind="human", created_by="u1",
    )
    row = store.fetch_one("SELECT options FROM question_items WHERE question_id IS NOT NULL", [])
    assert _json.loads(row["options"]) == [
        {"id": "o1", "label": "무중단 배포"},
        {"id": "o2", "label": "점검창 배포"},
    ]
    # 저장본은 비ASCII를 이스케이프하지 않는다 — label 원문과 바이트 단위로 일치
    assert "무중단 배포" in row["options"]

    item = q_service.get_qa_detail(DOC)["items"][0]
    assert item["options"] == [
        {"id": "o1", "label": "무중단 배포"},
        {"id": "o2", "label": "점검창 배포"},
    ]


def test_answer_by_selection_only_fills_body_with_label(store):
    """선택만으로 답하면 label 원문이 body에 채워진다 — body NOT NULL 유지 (L0008 §2.4)."""
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(
        DOC, [{"body": "어느 쪽?", "options": ["A안", "B안"]}],
        asker_kind="human", created_by="u1",
    )
    item_id = res["added_item_ids"][0]

    q_service.register_answer(DOC, item_id, "", author_kind="human", author_id="u1",
                              selected_option_ids=["o2"])

    detail = q_service.get_qa_detail(DOC)["items"][0]
    assert detail["answers"][0]["body"] == "B안"
    assert detail["answers"][0]["selected_options"] == ["o2"]
    # body만 읽는 기존 독자(멘트 조립)도 그대로 동작한다
    assert q_service.qa_bundle_by_doc(DOC)[0]["answer_body"] == "B안"


def test_answer_with_body_and_selection_keeps_body_as_written(store):
    """선택+서술 병행 시 서술이 본문이고 선택은 selected_options에만 남는다 (L0008 §4)."""
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(
        DOC, [{"body": "어느 쪽?", "options": ["A안", "B안"]}],
        asker_kind="human", created_by="u1",
    )
    item_id = res["added_item_ids"][0]

    q_service.register_answer(DOC, item_id, "A안 기반이되 롤백 절차 추가", author_kind="human",
                              author_id="u1", selected_option_ids=["o1"])

    answer = q_service.get_qa_detail(DOC)["items"][0]["answers"][0]
    assert answer["body"] == "A안 기반이되 롤백 절차 추가"
    assert answer["selected_options"] == ["o1"]


def test_question_without_options_is_unchanged(store):
    """선택지 없는 질의는 확장 이전과 완전히 동일하게 동작한다 (DB0007 §5 하위 호환)."""
    from modules.flow_gate.services import q_service
    # 문자열형(레거시 워커 페이로드) 포함
    res = q_service.add_questions(DOC, ["레거시 문자열 질의"], asker_kind="ai")
    item_id = res["added_item_ids"][0]

    item = q_service.get_qa_detail(DOC)["items"][0]
    assert item["options"] == []
    q_service.register_answer(DOC, item_id, "자유 서술", author_kind="human", author_id="u1")
    assert q_service.get_qa_detail(DOC)["items"][0]["answers"][0]["selected_options"] == []


@pytest.mark.parametrize(
    "options, detail_fragment",
    [
        (["A"] * 11, "at most 10"),
        (["   "], "must not be empty"),
        (["가" * 201], "200 characters or fewer"),
        (["같은 보기", "같은 보기"], "duplicate option label"),
        ([{"id": "o1", "label": "객체는 거절"}], "string label"),
        ("배열이 아님", "array of strings"),
    ],
)
def test_invalid_options_rejected_400(store, options, detail_fragment):
    """검증 실패는 저장 전에 400으로 거절된다 — DB CHECK가 없으므로 이 검증이 유일한 강제 수단 (L0008 §5)."""
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service
    with pytest.raises(HTTPException) as exc:
        q_service.add_questions(DOC, [{"body": "q?", "options": options}],
                                asker_kind="human", created_by="u1")
    assert exc.value.status_code == 400
    assert detail_fragment in exc.value.detail
    assert store.fetch_all("SELECT id FROM question_items", []) == []   # 저장되지 않았다


def test_strip_applied_to_labels(store):
    """label은 strip 후 저장된다 (L0008 §2.2)."""
    from modules.flow_gate.services import q_service
    q_service.add_questions(DOC, [{"body": "q?", "options": ["  여백 있는 보기  "]}],
                            asker_kind="human", created_by="u1")
    assert q_service.get_qa_detail(DOC)["items"][0]["options"] == [
        {"id": "o1", "label": "여백 있는 보기"},
    ]


def test_unknown_option_id_rejected_400(store):
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"body": "q?", "options": ["A안"]}],
                                  asker_kind="human", created_by="u1")
    item_id = res["added_item_ids"][0]
    with pytest.raises(HTTPException) as exc:
        q_service.register_answer(DOC, item_id, "", author_kind="human", author_id="u1",
                                  selected_option_ids=["o9"])
    assert exc.value.status_code == 400
    assert "unknown option id" in exc.value.detail
    assert store.fetch_all("SELECT id FROM answers", []) == []


def test_selection_on_option_less_item_rejected_400(store):
    """선택지 없는 항목에는 어떤 id도 선택할 수 없다 (L0008 §5)."""
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"body": "선택지 없음"}], asker_kind="human", created_by="u1")
    item_id = res["added_item_ids"][0]
    with pytest.raises(HTTPException) as exc:
        q_service.register_answer(DOC, item_id, "", author_kind="human", author_id="u1",
                                  selected_option_ids=["o1"])
    assert exc.value.status_code == 400


def test_multi_select_rejected_400(store):
    """v1은 단일선택 — 스키마는 배열이므로 검증만 완화하면 확장된다 (L0008 §1 MAX_SELECTED)."""
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"body": "q?", "options": ["A", "B"]}],
                                  asker_kind="human", created_by="u1")
    item_id = res["added_item_ids"][0]
    with pytest.raises(HTTPException) as exc:
        q_service.register_answer(DOC, item_id, "", author_kind="human", author_id="u1",
                                  selected_option_ids=["o1", "o2"])
    assert exc.value.status_code == 400
    assert "only one option" in exc.value.detail


def test_blank_body_without_selection_still_rejected_400(store):
    """빈 제출은 현행 규칙 그대로 거절된다 (L0008 §4 기본값)."""
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"body": "q?", "options": ["A"]}],
                                  asker_kind="human", created_by="u1")
    item_id = res["added_item_ids"][0]
    with pytest.raises(HTTPException) as exc:
        q_service.register_answer(DOC, item_id, "   ", author_kind="human", author_id="u1")
    assert exc.value.status_code == 400
    assert "body must not be empty" in exc.value.detail
