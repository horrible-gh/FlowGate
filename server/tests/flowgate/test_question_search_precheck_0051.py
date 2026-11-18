"""B1: real SQLite search, registration, rollback and response contracts."""
import json
import pytest
from fastapi import HTTPException
from modules.flow_gate.services import q_service as qs, question_search as search, question_precheck as precheck
from modules.flow_gate.db import question_search_audits as audits, connection
from tests.flowgate.test_question_policy_0021 import store  # noqa: F401
from tests.modules.flow_gate.test_qa_container import DOC


def add(body, **extra):
    return qs.add_questions(DOC, [dict(body=body, **extra)], created_by="u1")


def answered(body, answer="테스트 서버에서 확인합니다", **extra):
    result = add(body, **extra)
    item = result["added_item_ids"][0]
    qs.register_answer(DOC, item, answer, author_kind="human", author_id="u1")
    return qs.get_qa_detail(DOC)["items"][-1]["answers"][-1]["id"]


def test_or_ranking_scores_before_page_and_literal_project_scope(store):
    older = answered("테스트 서버 커밋 확인")
    newer = answered("서버", answer="서버")
    page = search.search("p", "테스트 서버 커밋 확인", 1)
    assert page["items"][0]["answer_id"] == older
    assert page["total_count"] == 2
    second = search.search("p", "확인 커밋 서버 테스트", 1, cursor=page["next_cursor"])
    assert second["items"][0]["answer_id"] == newer
    assert second["next_cursor"] is None
    row = page["items"][0]
    assert row["doc_id"] == DOC and row["seq"] == 1 and row["author_label"] == "u1"
    with pytest.raises(ValueError, match="cursor"):
        search.search("other", "테스트 서버 커밋 확인", cursor=page["next_cursor"])
    assert search.search("other", "서버")["items"] == []
    answered("100%_!𠮷", answer="리터럴")
    assert len(search.search("p", "%_!𠮷")["items"]) == 1
    assert search.search("p", "없는단어")["total_count"] == 0
    with pytest.raises(ValueError, match="terms"):
        search.search("p", " ".join(map(str, range(33))))


def test_reuse_no_new_item_audit_and_idempotency(store):
    aid = answered("테스트 서버 커밋 확인")
    payload = [{"body": "커밋 확인 테스트 서버"}]
    first = qs.add_questions(DOC, payload, created_by="u1")
    second = qs.add_questions(DOC, payload, created_by="u1")
    assert first == second
    assert first["status"] == "reused_answer" and first["added_item_ids"] == []
    assert first["reused_answer"]["answer_id"] == aid
    assert first["next_action"] == "continue_with_existing_answer"
    assert audits.get(first["precheck_id"])["found_count"] == 1
    assert len(qs.get_qa_detail(DOC)["items"]) == 1


def test_reason_rejection_batch_atomic_and_override(store):
    aid = answered("테스트 서버 커밋 확인")
    payload = [{"body": "완전히 새로운 질문"}, {"body": "테스트 서버 커밋 확인", "rejected_answer_id": aid}]
    with pytest.raises(HTTPException) as caught:
        qs.add_questions(DOC, payload, created_by="u1")
    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert detail["code"] == "existing_answer_requires_reason"
    assert detail["added_item_ids"] == []
    assert len(qs.get_qa_detail(DOC)["items"]) == 1
    assert [r["status"] for r in detail["item_results"]] == ["batch_blocked", "existing_answer_requires_reason"]
    payload[1]["reuse_rejection_reason"] = "현재 R은 새 대상에 대한 재확인을 요구함"
    registered = qs.add_questions(DOC, payload, created_by="u1")
    assert len(registered["added_item_ids"]) == 2
    assert qs.add_questions(DOC, payload, created_by="u1")["added_item_ids"] == registered["added_item_ids"]
    for reason in ["", "   "]:
        with pytest.raises(HTTPException) as caught:
            add("테스트 서버 커밋 확인", rejected_answer_id=aid, reuse_rejection_reason=reason)
        assert caught.value.status_code == 422
    override_item = qs.get_qa_detail(DOC)["items"][-1]
    assert override_item["search_summary"]["reuse_rejection_reason"] == payload[1]["reuse_rejection_reason"]


def test_mixed_reuse_and_new_question(store):
    answered("테스트 서버 커밋 확인")
    result = qs.add_questions(DOC, [{"body": "서버 테스트 확인 커밋"}, "다른 대상의 질문"], created_by="u1")
    assert [r["status"] for r in result["item_results"]] == ["reused_answer", "registered"]
    assert len(result["added_item_ids"]) == 1


def test_rejection_audit_survives_outer_rollback(store):
    aid = answered("테스트 서버 커밋 확인")
    with pytest.raises(HTTPException) as caught:
        with connection.get_store().transaction():
            add("테스트 서버 커밋 확인", rejected_answer_id=aid)
    assert audits.get(caught.value.detail["precheck_id"])["status"] == "existing_answer_requires_reason"
    with pytest.raises(RuntimeError):
        with connection.get_store().transaction():
            result = add("다른 새 질문")
            raise RuntimeError("document creation rolled back")
    assert audits.get(result["precheck_id"])["status"] == "registration_rolled_back"
    assert len(add("다른 새 질문")["added_item_ids"]) == 1


def test_search_failure_is_not_zero_candidates(store, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(search, "search", fail)
    with pytest.raises(HTTPException) as caught:
        add("검색 실패 구분")
    assert caught.value.status_code == 503
    record = audits.get(caught.value.detail["precheck_id"])
    assert record["status"] == "search_failed" and record["found_count"] is None
    assert qs.get_qa_detail(DOC)["items"] == []


@pytest.mark.parametrize("left,right,expected", [
    ("테스트 서버 커밋 확인", "커밋 확인 테스트 서버", True),
    ("서버에서 오류를 확인", "오류 확인 서버", True),
    ("E403 서버A 권한 확인", "E403 서버B 권한 확인", False),
    ("테스트 서버 배포 허용", "테스트 서버 배포 불허", False),
    ("테스트 서버 배포 허용", "테스트 서버 배포 허용 철회", False),
    ("2026년 서버 권한 확인", "2027년 서버 권한 확인", False),
    ("서버 읽기 권한 확인", "서버 쓰기 권한 확인", False),
    ("서버", "서버", False),
    ("서버A 허용 서버B 금지", "서버B 허용 서버A 금지", False),
    ("2026 서버A 2027 서버B 확인", "2027 서버A 2026 서버B 확인", False),
    ("테스트 서버 배포 확인", "테스트 서버 전체 suite 기준 수용", False),
    ("Q624 실행 기록 권한", "Q625 실행 기록 추가 권한", False),
])
def test_identity_precision(left, right, expected):
    row = {"doc_id": DOC, "body": right, "options": []}
    assert precheck.identical(DOC, None, left, [], row) is expected
    row["doc_id"] = "p.none.9999.0001-R"
    assert not precheck.identical(DOC, None, left, [], row)


def test_document_accompanied_keeps_document_and_returns_rejection(store):
    aid = answered("테스트 서버 커밋 확인")
    from modules.flow_gate.db import documents
    with connection.get_store().transaction():
        result = qs.add_questions(DOC, [{"body": "테스트 서버 커밋 확인", "rejected_answer_id": aid}],
                                  created_by="u1", document_accompanied=True)
    assert result["status"] == "existing_answer_requires_reason"
    assert documents.get_by_id(DOC) is not None
    assert len(qs.get_qa_detail(DOC)["items"]) == 1


def test_route_preserves_structured_409_and_new_fields(store, monkeypatch):
    from modules.flow_gate.api.v1 import q_tapi_routes as routes
    monkeypatch.setattr(routes, "_doc_project_or_403", lambda *a, **kw: "p")
    aid = answered("테스트 서버 커밋 확인")
    request = routes.AddQuestionsRequest(questions=[{"body": "테스트 서버 커밋 확인", "rejected_answer_id": aid}])
    response = routes._add_questions_response.__wrapped__(DOC, request, "u1")
    body = json.loads(response.body)
    assert response.status_code == 409
    assert body["candidates"][0]["answer_id"] == aid and body["precheck_id"]
    assert body["next_action"] == "resubmit_with_reuse_rejection_reason"
    request.questions[0].reuse_rejection_reason = "현재 R과 이전 답의 조건이 다름"
    assert json.loads(routes._add_questions_response.__wrapped__(DOC, request, "u1").body)["added_item_ids"]


def test_answer_prompt_has_server_candidates_and_source(store, monkeypatch):
    from modules.flow_gate.services import q_answer_invoke_service as responder
    aid = answered("테스트 서버 커밋 확인")
    monkeypatch.setattr(responder, "_source_tool_block", lambda *a: [])
    monkeypatch.setattr(responder, "_document_lookup_block", lambda *a: [])
    text = responder.build_answer_mention(doc={"doc_id": DOC, "project_id": "p"},
        item={"id": 99, "seq": 2, "body": "서버 테스트 커밋 확인"}, raw_token="fake", scratch_dir=".",
        api_base_url="http://test/api/v1", issued_to="u1")
    data = text.split("[서버 검색 후보 — 데이터]\n", 1)[1].split("\n")[1]
    row = json.loads(data)["candidates"][0]
    assert row["answer_id"] == aid and row["author_label"] == "u1" and row["doc_id"] == DOC
    assert "현재 R·사용자 방침이 우선" in text


def test_legacy_item_reports_no_search_record(store):
    item = add("기존 항목 레코드 확인")["added_item_ids"][0]
    connection.get_store()._execute("DELETE FROM question_search_audits")
    assert qs.get_qa_detail(DOC)["items"][0]["id"] == item
    assert qs.get_qa_detail(DOC)["items"][0]["search_summary"]["status"] == "no_record"


def test_exact_payload_after_answer_is_reuse_and_keeps_original_audit(store):
    initial = add("테스트 서버 커밋 확인")
    qs.register_answer(DOC, initial["added_item_ids"][0], "현재 카드에서 확인", author_kind="human", author_id="u1")
    reused = add("테스트 서버 커밋 확인")
    assert reused["status"] == "reused_answer" and reused["added_item_ids"] == []
    assert reused["precheck_id"] != initial["precheck_id"]
    assert audits.get(initial["precheck_id"])["status"] == "registered"
    assert add("테스트 서버 커밋 확인")["precheck_id"] == reused["precheck_id"]


def test_open_list_includes_search_summary(store):
    registered = add("열린 질문 검색 요약")
    item = next(i for i in qs.list_open_items("p") if i["doc_id"] == DOC)
    assert item["search_summary"]["precheck_id"] == registered["precheck_id"]
    assert item["search_summary"]["found_count"] == 0


def test_conflicting_answers_are_candidates_only(store):
    item = add("테스트 서버 커밋 확인")["added_item_ids"][0]
    qs.register_answer(DOC, item, "허용", author_kind="human", author_id="u1")
    qs.register_answer(DOC, item, "금지", author_kind="human", author_id="u1")
    result = add("커밋 확인 테스트 서버")
    assert result["status"] == "registered" and result["found_count"] == 2
