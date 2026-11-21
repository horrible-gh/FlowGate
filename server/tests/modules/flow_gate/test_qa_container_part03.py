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
