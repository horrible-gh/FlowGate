"""Independently selectable scenarios; shared fixtures remain in test_qa_container.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_qa_container import (
    DOC,
    Path,
    _MockDB,
    _MockTxn,
    _QUERIES,
    _QUERIES_JSON,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _json,
    contextmanager,
    os,
    pytest,
    raw,
    sqlite3,
    store,
    sys,
    tempfile,
)

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
