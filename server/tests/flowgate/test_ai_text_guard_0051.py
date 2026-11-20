"""flowgate.v02test.0051: one encoding verdict for worker-written JSON text, and what reuse does with damaged rows."""
from modules.flow_gate.services import ai_text_guard as guard, encoding_probe, question_precheck as precheck

TOKEN = {"token_id": "tok_guard_0051"}
PROBE = encoding_probe.probe_for(TOKEN["token_id"])
ASKED = "야간 스테이징 서버 배포를 에이전트가 진행해도 되는지 확인"


def _row(body=ASKED, title=None, answer="스테이징은 에이전트가 진행합니다."):
    return {"title": title, "body": body, "answer_body": answer, "options": [], "doc_id": "d", "answer_id": 1}


def test_ascii_replaced_korean_is_refused_in_any_field():
    for field, value in (("questions[0].body", "?? ???? ?? ???"), ("questions[0].title", "????? ??"),
                         ("q", "???? deploy"), ("body", "ok\n??? ?? ????? ???\nok")):
        violation, _ = guard.check(TOKEN, {field: value})
        assert violation["code"] == "encoding_corrupted" and violation["field"] == field
        assert violation["signal"] == guard.SIGNAL_QUESTION_MARKS and "UTF-8" in violation["message"]


def test_replacement_char_and_mojibake_are_refused():
    assert guard.check(TOKEN, {"body": "배포 " + chr(0xFFFD) * 2 + " 확인"})[0]["signal"] == encoding_probe.SIGNAL_REPLACEMENT_CHAR
    mojibake = "배포 확인".encode("utf-8").decode("latin-1")
    assert guard.check(TOKEN, {"body": mojibake})[0]["signal"] == encoding_probe.SIGNAL_MOJIBAKE


def test_ordinary_text_passes_in_every_language():
    fields = {"a": ASKED + "?", "b": "Is this allowed? Really??", "c": "本番に反映してよいですか？", "d": None, "e": "scope?"}
    violation, cleaned = guard.check(TOKEN, fields)
    assert violation is None and cleaned == fields


def test_matched_probe_proves_the_field_and_is_removed():
    violation, cleaned = guard.check(TOKEN, {"body": f"정말 ???? 라고 표시됩니까\n{PROBE}"}, ("body",))
    assert violation is None and cleaned["body"] == "정말 ???? 라고 표시됩니까"
    # The proof covers that field only.
    assert guard.check(TOKEN, {"body": f"본문\n{PROBE}", "title": "???? ??"}, ("body",))[0]["field"] == "title"


def test_damaged_probe_always_rejects():
    broken = PROBE.replace(encoding_probe.PROBE_GLYPH, "?")
    violation, _ = guard.check(TOKEN, {"body": f"정상 본문\n{broken}"}, ("body",))
    assert violation["signal"] == guard.SIGNAL_PROBE_BROKEN and violation["field"] == "body"


def test_login_session_is_not_checked():
    assert guard.check(None, {"body": "?? ???? ??"})[0] is None


def test_message_follows_locale():
    assert "corrupted" in guard.check(TOKEN, {"body": "?? ???? ??"}, locale="en")[0]["message"]
    assert "文字化け" in guard.check(TOKEN, {"body": "?? ???? ??"}, locale="ja")[0]["message"]
    for locale in ("ko", "en", "ja"):
        assert "422" in guard.transport_rule(locale)


# ── reuse identity: a title that only shortens the body ──────────────────────

def test_title_that_shortens_the_body_reuses_the_answer():
    assert precheck.match_kind("야간 스테이징 배포 진행 여부", ASKED, [], _row()) == "identical"
    assert precheck.match_kind(None, ASKED, [], _row(title="스테이징 배포 진행 문의")) == "identical"
    assert precheck.match_kind("배포 진행 여부", ASKED, [], _row(title="야간 배포 확인")) == "identical"


def test_title_naming_another_target_is_a_new_question():
    assert precheck.match_kind("스테이징 배포 권한 확인", ASKED, [], _row()) is None
    assert precheck.match_kind("운영 DB 삭제", ASKED, [], _row()) is None
    assert precheck.match_kind(None, ASKED, [], _row(title="롤백 포함 여부")) is None


def test_no_title_is_unchanged():
    assert precheck.match_kind(None, ASKED, [], _row()) == "identical"
    assert precheck.match_kind("", ASKED + " 요청", [], _row()) == "similar"
    assert precheck.match_kind(None, ASKED + " 운영 DB 삭제", [], _row()) is None


# ── a stored row that was damaged before the gate existed ────────────────────

def test_damaged_stored_answer_or_question_is_never_reused():
    assert precheck.match_kind(None, ASKED, [], _row(answer="???? ??? ????? ?????.")) is None
    assert precheck.match_kind(None, "?? ???? ?? ???", [], _row(body="?? ???? ?? ???")) is None
    row, reason = precheck.choose("d", None, ASKED, [], [_row(answer="?? ???? ??")])
    assert row is None and reason == "candidates_only_identity_unconfirmed"


# ── the Q&A routes: a worker token's text is checked before anything is stored or searched ──

import json
from types import SimpleNamespace as NS

import pytest

from tests.flowgate.test_question_policy_0021 import store  # noqa: E402,F401
from tests.modules.flow_gate.test_qa_container import DOC  # noqa: E402


def _request():
    return NS(headers={"Authorization": "Bearer raw"}, state=NS())


@pytest.fixture
def routes(store, monkeypatch):
    """The real handlers and the real token resolution; only the stores behind them are recorded."""
    from modules.flow_gate.api.v1 import q_tapi_routes as module
    calls = []
    token = dict(TOKEN, issued_to="u1", doc_ref=DOC, project="p", ai_run_id="run_guard_0051")
    monkeypatch.setattr(module.token_service, "verify", lambda raw: dict(token))
    monkeypatch.setattr(module, "has_permission", lambda *a, **kw: True)
    monkeypatch.setattr(module.q_service, "is_question_responder_run", lambda run_id: False)
    monkeypatch.setattr(module, "_add_questions_response",
                        lambda doc_id, body, user_id, ai_run_id=None: calls.append(("question", [q.body for q in body.questions])))
    monkeypatch.setattr(module, "_register_answer_response",
                        lambda doc_id, item_id, body, user_id, request, ai_run_id=None: calls.append(("answer", body.body)))
    monkeypatch.setattr(module.q_service, "escalate_to_user",
                        lambda doc_id, item_id, reason, **kw: calls.append(("escalate", reason)) or {})
    module.calls = calls
    return module


def _error(response):
    assert response.status_code == 422, response.body
    return json.loads(response.body)["error"]


def test_worker_question_with_damaged_text_is_refused_before_storing(routes):
    for question in ({"body": "?? ???? ?? ??? ?????"}, {"title": "???? ??", "body": "배포 진행 확인"},
                     {"body": "배포 진행 확인", "options": ["?? ????", "진행"]},
                     {"body": "배포 진행 확인", "rejected_answer_id": 1, "reuse_rejection_reason": "??? ???? ??"}):
        body = routes.AddQuestionsRequest(questions=[question])
        error = _error(routes.post_add_questions(DOC, body, _request()))
        assert error["code"] == "encoding_corrupted" and error["signal"] == "question_marks"
        assert error["field"].startswith("questions[0].")
    assert routes.calls == []


def test_worker_question_with_probe_reaches_the_store_without_the_probe_line(routes):
    body = routes.AddQuestionsRequest(questions=[{"body": f"화면에 ???? 로 보이는 항목은 그대로 둡니까\n{PROBE}\n"}])
    routes.post_add_questions(DOC, body, _request())
    assert routes.calls == [("question", ["화면에 ???? 로 보이는 항목은 그대로 둡니까"])]
    broken = routes.AddQuestionsRequest(questions=[{"body": "정상으로 보이는 본문\n" + PROBE[:-1] + "?"}])
    assert _error(routes.post_add_questions(DOC, broken, _request()))["signal"] == "probe_broken"
    only_probe = routes.AddQuestionsRequest(questions=[{"body": PROBE}])
    assert routes.post_add_questions(DOC, only_probe, _request()).status_code == 422
    assert len(routes.calls) == 1


def test_worker_answer_escalation_and_search_refuse_damaged_text(routes):
    answer = routes.RegisterAnswerRequest(body="???? ??? ????? ?????.")
    assert _error(routes.post_register_answer(DOC, 1, answer, _request()))["field"] == "body"
    assert _error(routes.post_register_answer_by_path("p", "none", "0001", "0001-D", 1, answer, _request()))["field"] == "body"
    assert _error(routes.post_escalate_to_user(DOC, 1, routes.EscalateRequest(reason="??? ??? ?????"), _request()))["field"] == "reason"
    assert _error(routes.search_answers(_request(), DOC, "???? ??", 10, None, None))["field"] == "q"
    assert routes.calls == []
    routes.post_register_answer(DOC, 1, routes.RegisterAnswerRequest(body="스테이징은 에이전트가 진행합니다."), _request())
    routes.post_escalate_to_user(DOC, 1, routes.EscalateRequest(reason="운영 배포 권한은 사용자가 정해야 합니다"), _request())
    assert routes.calls == [("answer", "스테이징은 에이전트가 진행합니다."), ("escalate", "운영 배포 권한은 사용자가 정해야 합니다")]
    assert routes.search_answers(_request(), DOC, "배포 확인", 10, None, None)["view"] == "list"


def test_login_session_on_the_route_is_not_checked(routes, monkeypatch):
    from fastapi import HTTPException

    def no_worker_token(raw):
        raise HTTPException(401, "not a worker token")
    monkeypatch.setattr(routes.token_service, "verify", no_worker_token)
    monkeypatch.setattr(routes, "verify_token", lambda raw: {"sub": "u1"})
    monkeypatch.setattr(routes, "get_current_user", lambda payload: {"user_id": "u1"})
    body = routes.AddQuestionsRequest(questions=[{"body": "왜 ???? 로 보입니까"}])
    routes.post_add_questions(DOC, body, _request())
    assert routes.calls == [("question", ["왜 ???? 로 보입니까"])]


def test_question_guides_carry_the_transport_rule(store):
    from modules.flow_gate.services import mention_service
    for locale in ("ko", "ja", "en"):
        assert guard.transport_rule(locale) in mention_service._question_precheck("http://test/api/v1", DOC, locale)
