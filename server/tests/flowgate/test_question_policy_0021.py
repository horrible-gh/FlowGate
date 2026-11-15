import json
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from flowgate.ai_roles import qa_adapter as qa, role_store
from tests.flowgate.test_fg_ai_roles_l3_qa import routing, NODE  # noqa: F401
from tests.modules.flow_gate.test_q_responder_startup_0111 import env, question, dispatch, finish, script  # noqa: F401
from tests.modules.flow_gate.test_qa_container import store as original_store, DOC
from modules.flow_gate.services import question_policy, question_search, question_policy_assistant as assistant
from modules.flow_gate.db import user_question_policies as policies


@pytest.fixture
def store(monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_document_ports
    monkeypatch.setattr(card_document_ports, "write_gate", lambda operation, data, action: action())
    yield from original_store.__wrapped__()


def slot(pid):
    return NS(source="card_explicit", value=role_store.RoleValue(kind="assigned", provider_id=pid, provider_name=None))


def test_same_model_different_roles_and_asking_role_excluded(routing):
    routing["slots"] = {"review": slot("A"), "instruction": slot("A")}
    assert qa.candidates("f", NODE, "A", "execution")[0] == [("A", "card_review_role"), ("A", "card_instruction_role")]
    assert qa.candidates("f", NODE, "A", "review")[0] == [("A", "card_instruction_role")]
    assert qa.candidates("f", NODE, "A", "instruction")[0] == [("A", "card_review_role")]
    routing["chain"] = [{"id": "C"}]
    assert qa.candidates("f", NODE, "A", "execution")[0] == []


@pytest.mark.parametrize("failure", ["fast_fail", "content_failure", "zero_output"])
@pytest.mark.parametrize("same_model", [False, True])
def test_exactly_one_role_retry_then_answer(env, question, monkeypatch, failure, same_model):
    fake, row = question
    launches = script(monkeypatch, row, failure)
    second = "aip_1" if same_model else "aip_2"
    run = finish(dispatch(default_tail=False, responder_candidates=[(second, "card_instruction_role")]), row)
    assert launches == ["aip_1", second]
    assert row["answer_count"] == 1 and row["responder_state"] == "answered"
    assert len(fake.claims) == 1


@pytest.mark.parametrize("action,expected", [("handoff", "user_decision"), ("human", "answered"),
                                           ("cancelled", "failed"), ("timeout", "failed"), ("pause", "failed")])
def test_no_retry_after_decision_or_stop(env, question, monkeypatch, action, expected):
    _, row = question
    launches = script(monkeypatch, row, action)
    finish(dispatch(default_tail=False, responder_candidates=[("aip_2", "card_instruction_role")]), row)
    assert launches == ["aip_1"] and row["responder_state"] == expected


def test_two_failures_do_not_use_defaults_or_retry_on_replay(env, question, monkeypatch):
    from flowgate.card_settings import question_wait
    _, row = question
    launches = script(monkeypatch, row, "content_failure", second="fast_fail")
    run = finish(dispatch(default_tail=False, responder_candidates=[("aip_2", "card_instruction_role")]), row)
    assert row["responder_state"] == "failed"
    for _ in range(3):
        assert not question_wait._dispatch("f", NODE, run, [row], False)
    assert launches == ["aip_1", "aip_2"]


def test_policy_roundtrip_unicode_clear_and_limit(store):
    assert policies.get("u1")["body"] == ""
    text = "화면만 질문. 𠮷\n구현은 알아서."
    assert policies.save("u1", text)["body"] == text
    assert policies.get("someone_else")["body"] == ""
    with pytest.raises(ValueError):
        policies.save("u1", "a" * 4001)
    assert policies.get("u1")["body"] == text
    assert policies.save("u1", "")["body"] == ""


def test_policy_owner_card_starter_vs_chat_user(store, monkeypatch):
    from modules.flow_gate.graph_workflow.store import run_store
    monkeypatch.setattr(run_store, "active_run", lambda _: {"started_by": "starter"})
    assert question_policy.owner("recipient", "f", card=True) == "starter"
    assert question_policy.owner("chat-user", "f") == "chat-user"


def test_answer_prompt_policy_replaces_default_and_includes_search(store, monkeypatch):
    from modules.flow_gate.services import q_answer_invoke_service as answer
    monkeypatch.setattr(answer, "_source_tool_block", lambda *a: [])
    monkeypatch.setattr(answer, "_document_lookup_block", lambda *a: [])
    policies.save("u1", "DB 구조는 나에게 질문")
    prompt = answer.build_answer_mention(doc={"doc_id": DOC}, item={"id": 1}, raw_token="fake",
        scratch_dir=".", api_base_url="http://test/api/v1", issued_to="u1")
    assert "DB 구조는 나에게 질문" in prompt
    assert "절대 이관하지" not in prompt
    assert f"/q/{DOC}/search" in prompt
    policies.save("u1", "")
    prompt = answer.build_answer_mention(doc={"doc_id": DOC}, item={"id": 1}, raw_token="fake",
        scratch_dir=".", api_base_url="http://test/api/v1", issued_to="u1")
    assert "절대 이관하지" in prompt


def test_search_only_answered_project_scoped_literal_and_paged(store):
    from modules.flow_gate.services import q_service
    ids = q_service.add_questions(DOC, [{"body": "100%_ answer?"}, {"body": "100%_ second?"},
                                     {"body": "unanswered"}], asker_kind="human", created_by="u1")["added_item_ids"]
    for item in ids[:2]:
        q_service.register_answer(DOC, item, "use 100%_", author_kind="human", author_id="u1")
    page = question_search.search("p", "%_", 1)
    assert len(page["items"]) == 1 and page["next_before_id"]
    assert page["items"][0]["doc_id"] == DOC
    assert len(question_search.search("p", "%_", 1, page["next_before_id"])["items"]) == 1
    assert question_search.search("other", "%_")["items"] == []
    assert question_search.search("p", "unanswered")["items"] == []


def test_assistant_validates_draft_and_keeps_existing_policy_in_context():
    reply = assistant.parse_reply(json.dumps({"message": "이렇게 이해했습니다", "choices": [], "policy": "UI만 질문"}))
    assert reply["policy"] == "UI만 질문"
    assert assistant.parse_reply('{"message":"ok","policy":""}')["policy"] is None
    with pytest.raises(ValueError, match="reply_invalid"):
        assistant.parse_reply('{"message":"","policy":"  "}')
    assert "existing" in assistant.prompt("existing", [{"role": "user", "content": "change UI"}], "en")


def test_assistant_reads_imperfect_replies_0004_ch():
    # A sentence and a code fence around the object, an unknown key, plain-string and extra choices.
    text = ('다음 질문입니다.\n```json\n{"message": "어디까지 맡길까요?", "note": "x", '
            '"choices": ["A", {"label": "B", "recommended": true}, "C", "D", "E"], "policy": null}\n```')
    reply = assistant.parse_reply(text)
    assert reply["message"] == "어디까지 맡길까요?" and reply["policy"] is None
    assert [c["label"] for c in reply["choices"]] == ["A", "B", "C", "D"]
    assert [c["recommended"] for c in reply["choices"]] == [False, True, False, False]
    with pytest.raises(ValueError, match="reply_invalid"):
        assistant.parse_reply("I could not do that.")
    with pytest.raises(ValueError, match="policy_too_long"):
        assistant.parse_reply(json.dumps({"message": "ok", "policy": "x" * 4001}))


def test_assistant_asks_once_more_before_failing_0004_ch(monkeypatch):
    prompts = []
    answers = iter(["not json", '{"message":"ok","choices":[],"policy":null}'])
    monkeypatch.setattr(assistant, "_execute", lambda provider, text, *a: prompts.append(text) or next(answers))
    monkeypatch.setattr(assistant, "_requests", {"r": {"user_id": "u1", "status": "running"}})
    assistant._worker("r", "u1", {}, "prompt")
    assert assistant.result("u1", "r")["reply"]["message"] == "ok"
    assert prompts[0] == "prompt" and assistant.RETRY_NOTES["reply_invalid"] in prompts[1]
    # 0026 (0004-CH): three attempts in all before a failure reaches the screen.
    answers = iter(["not json", "still not json", "again not json"])
    assistant._requests["r"] = {"user_id": "u1", "status": "running"}
    assistant._worker("r", "u1", {}, "prompt")
    assert assistant.result("u1", "r") == {"status": "failed", "error_code": "reply_invalid"}
    calls = []
    monkeypatch.setattr(assistant, "_execute", lambda *a: calls.append(1) or (_ for _ in ()).throw(ValueError("timeout")))
    assistant._requests["r"] = {"user_id": "u1", "status": "running"}
    assistant._worker("r", "u1", {}, "prompt")
    assert assistant.result("u1", "r")["error_code"] == "timeout" and len(calls) == 2


def _failing_then(codes, final):
    """An _execute that raises each code in turn, then answers `final`; it records every prompt."""
    seen, pending = [], list(codes)

    def run(provider, text, *a):
        seen.append(text)
        if pending:
            raise ValueError(pending.pop(0))
        return final
    return run, seen


def test_assistant_retries_transient_failures_silently_0026(monkeypatch):
    ok = '{"message":"ok","choices":[],"policy":null}'
    for codes in (["execution_failed", "remote_execution_failed"], ["timeout", "transport_failed"],
                  ["reply_invalid", "execution_failed"]):
        run, seen = _failing_then(codes, ok)
        monkeypatch.setattr(assistant, "_execute", run)
        monkeypatch.setattr(assistant, "_requests", {"r": {"user_id": "u1", "status": "running"}})
        assistant._worker("r", "u1", {}, "prompt")
        assert assistant.result("u1", "r")["reply"]["message"] == "ok" and len(seen) == 3
    # A format note, once given, stays on the later attempts.
    assert assistant.RETRY_NOTES["reply_invalid"] in seen[1] and assistant.RETRY_NOTES["reply_invalid"] in seen[2]
    # An AI that is off or misconfigured is not asked again.
    for code in ("provider_unavailable", "remote_agent_offline", "provider_launch_failed"):
        run, seen = _failing_then([code], ok)
        monkeypatch.setattr(assistant, "_execute", run)
        monkeypatch.setattr(assistant, "_requests", {"r": {"user_id": "u1", "status": "running"}})
        assistant._worker("r", "u1", {}, "prompt")
        assert assistant.result("u1", "r") == {"status": "failed", "error_code": code} and len(seen) == 1


def test_assistant_prompt_assesses_the_user_0026():
    text = assistant.prompt("", [], "ko")
    assert "sample questions" in text and "assessment" in text
    assert "run the assessment again" in text and "Reply in Korean" in text


def test_assistant_explicit_selection_owner_and_no_auto_save(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        assistant.start("u1", "", "", [], "ko")
    assert exc.value.status_code == 422
    monkeypatch.setattr(assistant, "_requests", {"r": {"user_id": "u1", "status": "running"}})
    with pytest.raises(HTTPException) as exc:
        assistant.result("u2", "r")
    assert exc.value.status_code == 404
    monkeypatch.setattr(assistant, "_execute", lambda *a: '{"message":"ok","policy":"UI only"}')
    assistant._worker("r", "u1", {}, "prompt")
    assert assistant.result("u1", "r")["reply"]["policy"] == "UI only"


def test_search_http_token_binding_and_read_permission(monkeypatch):
    from modules.flow_gate.api.v1 import q_tapi_routes as route
    from modules.flow_gate.services.ai_invoke.worker import _BearerOnlyRequest
    monkeypatch.setattr(route.token_service, "verify", lambda token: {"doc_ref": DOC, "issued_to": "u1"})
    monkeypatch.setattr(route.db_documents, "get_by_id", lambda doc: {"project_id": "p"})
    monkeypatch.setattr(route, "has_permission", lambda *args: False)
    request = _BearerOnlyRequest("fake")
    assert route.search_answers(request, DOC, "term", 10, None).status_code == 403
    monkeypatch.setattr(route, "has_permission", lambda user, project, perm: (user, project, perm) == ("u1", "p", "perm_document_read"))
    monkeypatch.setattr(question_search, "search", lambda project, *args: {"project": project})
    assert route.search_answers(request, DOC, "term", 10, None) == {"project": "p"}
    assert route.search_answers(request, "p.none.0002.0001-D", "term", 10, None).status_code == 403


@pytest.mark.parametrize("operation,data", [("answer_question", {"body": "answer"}),
                                           ("escalate_question", {"reason": "Policy requires user choice"})])
def test_api_responder_completion_uses_bound_question(monkeypatch, operation, data):
    from modules.flow_gate.services import ai_invoke_service as svc, api_server_tools, question_tools
    from modules.flow_gate.services.ai_invoke import worker
    from tests.modules.flow_gate.test_ai_api_chat_turn_0489 import _run
    run = _run(); run.update(action_scope="edit", docs_target=1)
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *args: "test-key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda run: 60)
    monkeypatch.setattr(question_tools, "responder_item", lambda run: 201)
    monkeypatch.setattr(api_server_tools, "definitions_for_run", lambda run: question_tools.definitions(201))
    monkeypatch.setattr(svc, "_call_openai", lambda *args: ("", {"id": "c1", "name": operation, "input": data}, {"role": "assistant"}))
    calls = []
    monkeypatch.setattr(worker, "_api_bound_request", lambda *args: calls.append(args) or (201, {"ok": True}))
    assert svc._api_execute({"id": "p", "kind": "openai", "api_base_url": "https://example.invalid", "api_model": "test"}, "prompt", run) == ("started_ok", None)
    suffix = "answers" if operation == "answer_question" else "escalate"
    assert calls[0][1:] == ("raw-token", f"/q/{run['doc_ref']}/items/201/{suffix}", "POST", data)


def test_assistant_uses_selected_personal_provider(monkeypatch):
    seen = []
    monkeypatch.setattr(assistant.settings, "get_provider_secret", lambda scope, pid: "private-key" if pid == "personal" else None)
    monkeypatch.setattr(assistant.connection, "execute_prompt", lambda subject, *args, **kwargs:
                        seen.append(subject) or NS(kind=assistant.runtime.PROBE_OK, text="reply"))
    assert assistant._execute({"id": "personal", "name": "Mine", "exec_type": "api", "kind": "openai", "api_base_url": "https://example.invalid/", "api_model": "test"}, "prompt", "u1", "r") == "reply"
    assert seen[0].provider_id == "personal" and seen[0].credential == "private-key"
