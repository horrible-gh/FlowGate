import json
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from flowgate.ai_roles import qa_adapter as qa, role_store
from tests.flowgate.test_fg_ai_roles_l3_qa import routing, NODE  # noqa: F401
from tests.modules.flow_gate.test_q_responder_startup_0111 import env as responder_env, question, dispatch, finish, script  # noqa: F401
from tests.modules.flow_gate.test_qa_container import store as original_store, DOC
from modules.flow_gate.services import question_policy, question_search, question_policy_assistant as assistant
from modules.flow_gate.db import user_question_policies as policies


@pytest.fixture
def env(monkeypatch, tmp_path, migrated_sqlite_db):
    # Use the durable allocator paired with the fixture's identity DB. The old
    # synthetic IDs were never reserved and are now rejected before invocation.
    from modules.flow_gate.db import ai_run_identity
    from tests.modules.flow_gate import test_ai_invoke_no_output_retry_0359 as base
    generator = responder_env.__wrapped__(monkeypatch, tmp_path, migrated_sqlite_db)
    value = next(generator)
    monkeypatch.setattr(base.svc, "_next_run_id", ai_run_identity.allocate)
    try:
        yield value
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


@pytest.fixture
def store(monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_document_ports
    monkeypatch.setattr(card_document_ports, "write_gate", lambda operation, data, action: action())
    yield from original_store.__wrapped__()


def slot(pid):
    return NS(source="card_explicit", value=role_store.RoleValue(kind="assigned", provider_id=pid, provider_name=None))


def test_review_ai_first_then_independent_default_never_the_askers_provider(routing):
    # flowgate.v02test.0051 T0005 (A1): the asking ROLE no longer decides — the actual asker's provider does. A review AI
    # that is the asker's own provider is skipped for an independent enabled default; the instruction role never answers.
    routing["slots"] = {"review": slot("A"), "instruction": slot("A")}
    for role in ("execution", "review", "instruction", None):
        assert qa.candidates("f", NODE, "A", role)[0] == [("B", "default"), ("C", "default")]
    routing["slots"] = {"review": slot("B"), "instruction": slot("A")}
    assert qa.candidates("f", NODE, "A", "execution")[0] == [("B", "card_review_role"), ("C", "default")]
    routing["chain"] = [{"id": "C"}]
    assert qa.candidates("f", NODE, "A", "execution")[0] == [("C", "default")]
    routing["chain"] = [{"id": "A"}]
    assert qa.candidates("f", NODE, "A", "execution")[:2] == ([], "no_independent_responder")


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
    assert "작업 레포트(TR)" in prompt
    assert "현재 권한 안에서" in prompt
    assert "완료 기준을 임의로 완화하지" in prompt
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


def test_search_keywords_across_fields_codes_and_pagination(store):
    from modules.flow_gate.services import q_service
    ids = q_service.add_questions(DOC, [
        {"title": "빈 폴더", "body": "삭제 도구 not_tool_artifact"},
        {"title": "빈 디렉터리", "body": "not_tool_artifact 거절"},
        {"body": "notXtoolYartifact"},
    ], asker_kind="human", created_by="u1")["added_item_ids"]
    for item in ids:
        q_service.register_answer(DOC, item, "Git 추적 대상 아님", author_kind="human", author_id="u1")
    page = question_search.search("p", "Git   not_tool_artifact", 1)
    assert [r["item_id"] for r in page["items"]] == [ids[1]]
    page2 = question_search.search("p", "not_tool_artifact Git", 1, page["next_before_id"])
    assert [r["item_id"] for r in page2["items"]] == [ids[0]]
    assert page2["next_before_id"] is not None
    page3 = question_search.search("p", "Git not_tool_artifact", 1, page2["next_before_id"])
    assert [r["item_id"] for r in page3["items"]] == [ids[2]]
    assert page3["next_before_id"] is None
    assert len(question_search.search("p", "Git absent")["items"]) == 3
    assert question_search.search("other", "Git not_tool_artifact")["items"] == []
    assert question_search.search("p", "' OR 1=1 --")["items"] == []


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
    monkeypatch.setattr(question_search, "search", lambda project, *args: {"project": project, "items": []})
    assert route.search_answers(request, DOC, "term", 10, None)["project"] == "p"
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


# 0026 (0009-CH): the final turn (summary + whole draft) failed again and again while the short turns worked.
def test_assistant_reads_draft_with_raw_newlines_0009_ch():
    draft = "## AI가 스스로 결정하는 것\n- 테스트는 AI가 진행\n\n## 예외\n- 운영 배포는 하지 않는다"
    raw = '{"message": "정리했습니다", "choices": [], "policy": "' + draft + '"}'
    assert "\n" in raw  # unescaped newlines inside the JSON string
    assert assistant.parse_reply(raw)["policy"] == draft


def test_assistant_api_reply_has_room_for_the_whole_draft_0009_ch(monkeypatch):
    seen = {}
    monkeypatch.setattr(assistant.settings, "get_provider_secret", lambda scope, pid: "key")
    monkeypatch.setattr(assistant.connection, "execute_prompt", lambda subject, text, handle, timeout, **kw:
                        seen.update(kw, timeout=timeout) or NS(kind=assistant.runtime.PROBE_OK, text="reply"))
    assistant._execute({"id": "p", "name": "P", "exec_type": "api", "kind": "claude",
                        "api_base_url": "https://example.invalid", "api_model": "m"}, "prompt", "u1", "r")
    # 4000 Korean characters plus message and JSON stay well under the ceiling.
    assert seen["max_tokens"] >= 8000 and seen["timeout"] == assistant.TIMEOUT


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _remote_env(monkeypatch, states):
    """A remote Agent whose Job goes through `states`: a list of (seconds_from_enqueue, status, started)."""
    from modules.flow_gate.services import agent_jobs
    clock, calls = _Clock(), {"cancel": 0}

    class Svc:
        def enqueue_remote_cli(self, **kw):
            calls["timeout_seconds"] = kw["timeout_seconds"]

        def _state(self):
            current = states[0]
            for state in states:
                if clock.now >= state[0]:
                    current = state
            return current

        def get_ai_job(self, job, attempt):
            return {"status": self._state()[1]}

        def collect_ai_result(self, job, attempt, owner):
            _, status, started = self._state()
            return {"collected": True, "status": status, "child_exit_code": 0 if status == "DONE" else 1,
                    "started_at": "2026-11-15T00:00:00Z" if started else None, "stdout": "answer"}

        def cancel_ai_job(self, job, attempt):
            calls["cancel"] += 1

        def release_ai_retrieval(self, job, attempt, owner):
            pass

    monkeypatch.setattr(agent_jobs, "AgentJobService", Svc)
    monkeypatch.setattr(agent_jobs, "retrieval_owner_for", lambda request_id: "owner")
    monkeypatch.setattr(assistant.provider_remote, "_binding", lambda provider: {"target_agent_id": "a", "requester": "u1"})
    monkeypatch.setattr(assistant.provider_remote, "agent_failure", lambda target, requester: None)
    monkeypatch.setattr(assistant.provider_remote, "AGENT_WORKDIR_CONTRACT_VERIFIED", True)
    monkeypatch.setattr(assistant.provider_remote, "_agent_workdir", lambda target: "tmpl")
    monkeypatch.setattr(assistant.provider_remote, "job_workdir", lambda template, job: "cwd")
    monkeypatch.setattr(assistant.settings, "normalize_cli_command", lambda kind, command: "cmd")
    monkeypatch.setattr(assistant.provider_cli, "_cli_last_message", lambda kind, out, path: out)
    monkeypatch.setattr(assistant, "time", clock)
    return clock, calls


def test_assistant_remote_wait_in_queue_does_not_count_0009_ch(monkeypatch):
    provider = {"kind": "claude", "cli_command": ""}
    # Queued and starting for 200 s, then a long run just under TIMEOUT: still an answer.
    run = assistant.TIMEOUT - 10
    clock, calls = _remote_env(monkeypatch, [(0, "NEW", False), (150, "CLAIMED", False), (200, "STARTED", True),
                                             (200 + run, "DONE", True)])
    assert assistant._remote(provider, "prompt", "u1", "r") == "answer"
    assert calls["cancel"] == 0 and calls["timeout_seconds"] == assistant.TIMEOUT
    # A run past TIMEOUT from its start is a timeout and the Job is cancelled.
    clock, calls = _remote_env(monkeypatch, [(0, "NEW", False), (30, "STARTED", True)])
    with pytest.raises(ValueError, match="timeout"):
        assistant._remote(provider, "prompt", "u1", "r")
    assert calls["cancel"] == 1 and 30 + assistant.TIMEOUT <= clock.now <= 30 + assistant.TIMEOUT + 2
    # A Job the server ended because nobody claimed it is a wait (timeout), not a failed run.
    clock, calls = _remote_env(monkeypatch, [(0, "NEW", False), (120, "CANCELLED", False)])
    with pytest.raises(ValueError, match="timeout"):
        assistant._remote(provider, "prompt", "u1", "r")
    assert calls["cancel"] == 0


def test_assistant_logs_failure_cause_without_text_0009_ch(monkeypatch, caplog):
    answers = iter(["SECRET-REPLY not json", "SECRET-REPLY", "SECRET-REPLY"])
    monkeypatch.setattr(assistant, "_execute", lambda provider, text, *a: next(answers))
    monkeypatch.setattr(assistant, "_requests", {"r": {"user_id": "u1", "status": "running"}})
    with caplog.at_level("WARNING", logger=assistant.__name__):
        assistant._worker("r", "u1", {"exec_type": "api", "kind": "claude"}, "SECRET-PROMPT")
    assert assistant.result("u1", "r")["error_code"] == "reply_invalid"
    lines = [r.getMessage() for r in caplog.records if r.name == assistant.__name__]
    assert len(lines) == 3 and all("code=reply_invalid" in line for line in lines)
    assert "attempt=1/3" in lines[0] and "reply_chars=21" in lines[0]
    assert not any("SECRET" in line for line in lines)
