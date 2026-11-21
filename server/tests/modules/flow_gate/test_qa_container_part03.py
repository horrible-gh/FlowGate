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

def test_options_stored_with_server_assigned_ids_and_returned_parsed(store):
    """Assign stored options IDs o1..oN and return them as parsed arrays."""
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
    # Stored JSON does not escape non-ASCII text; preserve the label bytes.
    assert "무중단 배포" in row["options"]

    item = q_service.get_qa_detail(DOC)["items"][0]
    assert item["options"] == [
        {"id": "o1", "label": "무중단 배포"},
        {"id": "o2", "label": "점검창 배포"},
    ]
def test_answer_by_selection_only_fills_body_with_label(store):
    """Selection-only answers copy the original label into body, preserving NOT NULL."""
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
    # Existing body-only consumers (mention assembly) continue to work.
    assert q_service.qa_bundle_by_doc(DOC)[0]["answer_body"] == "B안"
def test_answer_with_body_and_selection_keeps_body_as_written(store):
    """With selection and prose, prose is the body and selections remain in selected_options."""
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
    """Questions without options preserve all pre-extension behavior (DB0007 §5 compatibility)."""
    from modules.flow_gate.services import q_service
    # Include string-form legacy worker payloads
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
    """Reject validation failures with 400 before saving; without DB CHECK this is the only enforcement."""
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service
    with pytest.raises(HTTPException) as exc:
        q_service.add_questions(DOC, [{"body": "q?", "options": options}],
                                asker_kind="human", created_by="u1")
    assert exc.value.status_code == 400
    assert detail_fragment in exc.value.detail
    assert store.fetch_all("SELECT id FROM question_items", []) == []   # Nothing was stored
