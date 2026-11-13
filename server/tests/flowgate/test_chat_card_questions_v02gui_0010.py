"""flowgate.v02gui.0010: a chat AI lists and answers the card questions (Q) of its own Flow.

Real SQLite (questions / question_items / answers, documents, tokens), the real Flow document guard and the real
``q_service.register_answer``, through both transports: the API chat tool (call ledger) and the CLI chat HTTP route.
The chat fixture is the 0127 one (token verify replaced by a live-row lookup; authz still reads the token row).
Q#600: the user's ``perm_document_create`` alone decides — reading mode answers too. One answer per question.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_card_document_edit_0127 import env, F, D  # noqa: F401
from test_card_document_chat_adapter_0127 import chat, error  # noqa: F401
from test_card_document_chat_0127 import RUN_ID, TOKEN_ID
from flowgate.card_document_edit import call_ledger, chat_authz, chat_questions
from modules.flow_gate.db import answers as db_answers
from modules.flow_gate.db import documents, groups
from modules.flow_gate.db import question_items as db_question_items
from modules.flow_gate.db import questions as db_questions

ASKER = "air_card_asker"
H = {"Authorization": "Bearer chat"}
LIST_URL = "/flowgate/api/v2/chat/questions"


def answer_url(doc_id, item_id):
    return f"/flowgate/api/v2/chat/questions/{doc_id}/items/{item_id}/answers"


@pytest.fixture
def q(chat, monkeypatch):
    from modules.flow_gate.rbac import permission_service
    from modules.flow_gate.services import q_service
    allowed = {"value": True}
    monkeypatch.setattr(permission_service, "has_permission", lambda *a: allowed["value"])
    events = []
    monkeypatch.setattr(q_service, "publish_event_threadsafe", lambda ev: events.append(ev))
    chat.allowed, chat.events = allowed, events
    return chat


def ask(doc_id, bodies, asker_kind="ai", asker=ASKER, options="[]"):
    if db_questions.get_container_by_doc(doc_id) is None:
        db_questions.insert_container_for_doc(doc_id, "flowgate", "Q", "user")
    container = db_questions.get_container_by_doc(doc_id)
    start = db_question_items.get_max_seq(container["id"])
    for n, body in enumerate(bodies, start=start + 1):
        db_question_items.insert(container["id"], n, body, title=f"Q{n}", asker_kind=asker_kind, options=options,
                                 asker_ai_run_id=asker if asker_kind == "ai" else None)
    return db_question_items.list_by_doc(doc_id)


def other_doc(doc_id, type_code, group_id=F, seq=7):
    if groups.get_by_id(group_id) is None:
        groups.create({"group_id": group_id, "project_id": "flowgate", "module": "v02", "title": group_id,
                       "status": "OPEN"})
    documents.create({"doc_id": doc_id, "project_id": "flowgate", "module": "v02", "group_id": group_id,
                      "type_code": type_code, "seq": seq, "title": doc_id, "status": "draft", "owner_id": "user"})
    return doc_id


_calls = {"n": 0}


def api(chat, tool, inp):
    _calls["n"] += 1
    ctx = call_ledger.accept_call(chat.run, {"id": f"provider-{tool}-{_calls['n']}", "name": tool, "input": inp})
    handler = (chat_questions.list_questions_call_handler if tool == chat_questions.LIST_TOOL
               else chat_questions.answer_question_call_handler)
    return handler(chat.run, ctx)


def as_result(response):
    return response.status_code, response.json()


def stored(item_id):
    return db_answers.list_by_question_item(item_id)


# ── descriptors and mention ──────────────────────────────────────────────────

def test_descriptors_are_call_handler_tools_and_the_mention_states_the_rules():
    tools = {t["name"]: t for t in chat_questions.descriptors()}
    assert list(tools) == ["list_card_questions", "answer_card_question"]
    for tool in tools.values():
        assert callable(tool["call_handler"]) and tool["own_validation"] is True and tool["completion"] is False
        assert tool["schema"]["additionalProperties"] is False
    assert tools["answer_card_question"]["schema"]["required"] == ["doc_id", "item_id"]
    assert "Answer before you send your reply" in tools["list_card_questions"]["prompt_hint"]
    section = tools["list_card_questions"]["cli_mention"]("http://h/flowgate/api/v1", "raw")
    for word in ("GET http://h/flowgate/api/v2/chat/questions", "/questions/{doc_id}/items/{item_id}/answers",
                 "Bearer raw", "BEFORE you submit your turn", "ask the user", "question_already_answered",
                 "card_resume", "run_not_resumable", "Reading mode may answer"):
        assert word in section
    assert "api/v1/api/v2" not in section


def test_tools_are_offered_in_reading_mode_and_reach_the_cli_mention(clean_chat_hooks):
    from flowgate.conversation import integration
    from modules.flow_gate.services import chat_run_hooks
    integration.register()
    names = [t["name"] for t in chat_run_hooks.chat_tools({"action_scope": "chat"})]
    assert {"list_card_questions", "answer_card_question"} <= set(names)
    sections = chat_run_hooks.chat_mention_sections("http://h/flowgate/api/v1", "raw", omit=("run_command",))
    assert any(s.startswith("[Card questions (Q)") for s in sections)


@pytest.fixture
def clean_chat_hooks():
    from modules.flow_gate.services import chat_run_hooks
    saved = dict(chat_run_hooks._handlers)
    chat_run_hooks.clear()
    yield
    chat_run_hooks.clear()
    for name, handler in saved.items():
        chat_run_hooks.register(name, handler)


# ── list ─────────────────────────────────────────────────────────────────────

def test_api_list_shows_the_flows_open_questions_with_item_id_seq_and_card(q):
    items = ask(D, ["Which DB?", "Which port?"], options='[{"id": "o1", "label": "sqlite"}]')
    db_question_items.increment_answer_count(items[1]["id"])
    status, body = api(q, chat_questions.LIST_TOOL, {})
    assert status == 200, body
    [doc] = body["documents"]
    assert doc["doc_id"] == D and doc["open_count"] == 1 and doc["cards"] == [{"node_key": "research",
                                                                               "slot": "instruction"}]
    [first] = doc["questions"]
    assert (first["item_id"], first["seq"], first["body"], first["answered"]) == (items[0]["id"], 1, "Which DB?", False)
    assert first["options"] == [{"id": "o1", "label": "sqlite"}] and first["asker_kind"] == "ai"
    status, body = api(q, chat_questions.LIST_TOOL, {"doc_id": D, "include_answered": True})
    assert status == 200 and [i["seq"] for i in body["documents"][0]["questions"]] == [1, 2]


def test_cli_list_and_scope_refusals(q):
    ask(D, ["Which DB?"])
    listed = as_result(q.env.client.get(LIST_URL, headers=H))
    assert listed[0] == 200 and listed[1]["documents"][0]["doc_id"] == D
    elsewhere = other_doc("flowgate.v02.0128.0002-T", "T", group_id="flowgate.v02.0128")
    out = as_result(q.env.client.get(LIST_URL, headers=H, params={"doc_id": elsewhere}))
    assert error(out, 403, "question_outside_flow") == {"doc_id": elsewhere, "flow_id": F}
    conversation = other_doc(F + ".0003-CH", "CH", seq=3)
    assert error(as_result(q.env.client.get(LIST_URL, headers=H, params={"doc_id": conversation})), 422,
                 "question_target_invalid")["reason"] == "conversation"
    assert error(as_result(q.env.client.get(LIST_URL, headers=H, params={"bogus": "1"})), 422, "invalid_request")


# ── answer ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["read", "read_write"])
def test_api_answer_is_stored_as_the_chat_runs_ai_answer_once(q, monkeypatch, kind):
    monkeypatch.setattr(chat_authz, "_kind_for_token", lambda row: (kind, None))   # Q#600: mode is no condition
    [item] = ask(D, ["Which DB?"])
    status, body = api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "body": "SQLite"})
    assert status == 200, body
    assert body["replayed"] is False and body["author_kind"] == "ai" and body["question_status"] == "done"
    assert body["card_resume"]["state"] in ("not_graph", "not_waiting", "unknown")
    [row] = stored(item["id"])
    assert (row["body"], row["author_kind"], row["author_id"], row["author_ai_run_id"]) == ("SQLite", "ai", None,
                                                                                          RUN_ID)
    assert db_question_items.get_by_pk(item["id"])["answer_count"] == 1
    assert len(q.events) == 1
    # the same chat run again: told as a replay, nothing stored twice
    again = api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "body": "SQLite"})
    assert again[0] == 200 and again[1]["replayed"] is True and len(stored(item["id"])) == 1
    # the run-end follow-up finds the chat's answer by its run
    from flowgate.card_settings import legacy_adapter
    assert legacy_adapter.askers_answered_as_author(RUN_ID) == {ASKER}
    assert [i["id"] for i in db_question_items.list_by_answer_run(RUN_ID)] == [item["id"]]


def test_an_answer_by_someone_else_refuses_a_second_answer(q):
    from modules.flow_gate.services import q_service
    [item] = ask(D, ["Which DB?"], asker_kind="human")
    q_service.register_answer(D, item["id"], "Postgres", author_kind="human", author_id="user")
    out = api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "body": "SQLite"})
    assert error(out, 409, "question_already_answered") == {"doc_id": D, "item_id": item["id"]}
    assert [r["body"] for r in stored(item["id"])] == ["Postgres"]


def test_option_answer_and_input_errors(q):
    [item] = ask(D, ["Which DB?"], options='[{"id": "o1", "label": "sqlite"}]')
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"]}), 422, "invalid_answer")
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "selected_option_ids": ["x"]}),
                 422, "invalid_answer")
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "flow_id": F, "body": "a"}),
                 422, "invalid_request")["errors"][0] == {"field": "flow_id", "reason": "unknown_field"}
    assert stored(item["id"]) == []
    ok = api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "selected_option_ids": ["o1"]})
    assert ok[0] == 200 and stored(item["id"])[0]["body"] == "sqlite"


def test_scope_permission_and_ownership_refusals_store_nothing(q):
    [item] = ask(D, ["Which DB?"])
    elsewhere = other_doc("flowgate.v02.0128.0002-T", "T", group_id="flowgate.v02.0128")
    [foreign_item] = ask(elsewhere, ["Other Flow?"])
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": elsewhere, "item_id": foreign_item["id"], "body": "x"}),
                 403, "question_outside_flow")
    # an item of another document is not this document's question
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": foreign_item["id"], "body": "x"}),
                 404, "question_not_found")
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": F + ".0099-T", "item_id": item["id"], "body": "x"}),
                 404, "document_not_found")
    q.allowed["value"] = False
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "body": "x"}),
                 403, "permission_denied")["required"] == "perm_document_create"
    assert stored(item["id"]) == [] and stored(foreign_item["id"]) == []


def test_the_asker_run_may_not_answer_its_own_question(q):
    [item] = ask(D, ["Which DB?"], asker=RUN_ID)
    out = api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "body": "x"})
    assert error(out, 403, "self_answer_forbidden") and stored(item["id"]) == []


def test_cli_answer_then_the_consumed_token_answers_nothing(q):
    first, second = ask(D, ["Which DB?", "Which port?"])
    saved = as_result(q.env.client.post(answer_url(D, first["id"]), headers=H, json={"body": "SQLite"}))
    assert saved[0] == 200, saved
    assert saved[1]["replayed"] is False and stored(first["id"])[0]["author_ai_run_id"] == RUN_ID
    q.env.db._execute("UPDATE tokens SET consumed_at = 't' WHERE token_id = ?", [TOKEN_ID])   # the turn was sent
    late = as_result(q.env.client.post(answer_url(D, second["id"]), headers=H, json={"body": "8080"}))
    assert late[0] == 401 and stored(second["id"]) == []


def test_a_finished_or_cancelling_run_answers_nothing(q):
    [item] = ask(D, ["Which DB?"])
    q.run["status"] = "finished"
    assert error(api(q, chat_questions.ANSWER_TOOL, {"doc_id": D, "item_id": item["id"], "body": "x"}),
                 409, "chat_document_run_finished")
    q.run["status"] = "running"
    from flowgate.card_document_edit import chat_gate
    chat_gate.close(RUN_ID, chat_gate.FINISHED, 0)          # the run's finalize closed its gate
    out = as_result(q.env.client.post(answer_url(D, item["id"]), headers=H, json={"body": "x"}))
    assert error(out, 409, "chat_document_run_finished") and stored(item["id"]) == []


# ── the card's resume (question_wait) ────────────────────────────────────────

def test_follow_takes_a_chat_runs_answers_and_only_a_chat_runs(monkeypatch):
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import question_wait
    answered = []
    monkeypatch.setattr(legacy, "askers_answered_by", lambda run_id: set())
    monkeypatch.setattr(legacy, "askers_answered_as_author", lambda run_id: {ASKER} if run_id == "chat_run" else set())
    monkeypatch.setattr(question_wait, "_answered", lambda flow_id, asker: answered.append((flow_id, asker)))
    monkeypatch.setattr(question_wait, "_dispatch_waiting", lambda flow_id: None)
    question_wait.follow({"run_id": "chat_run", "group_id": F, "action_scope": "edit"})
    assert answered == []                                  # an ordinary run's own answers are not followed so
    question_wait.follow({"run_id": "chat_run", "group_id": F, "action_scope": "chat"})
    assert answered == [(F, ASKER)]


def test_the_person_answer_hook_still_skips_ai_answers(monkeypatch):
    from flowgate.card_settings import question_wait
    monkeypatch.setattr(question_wait, "_answered", lambda *a: pytest.fail("an AI answer resumed at once"))
    question_wait.on_question_answered({"author_kind": "ai", "group_id": F, "asker_ai_run_id": ASKER})


@pytest.mark.parametrize("run_state, open_items, state", [
    (("running", None), [], "resumes_after_chat_run"),
    (("idle", None), [], "resumes_after_chat_run"),
    (("idle", "stopped"), [], "run_not_resumable"),
    (("paused", None), [], "run_not_resumable"),
    (None, [], "run_not_resumable"),
    (("running", None), [{"id": 2}], "waiting_other_questions"),
])
def test_chat_answer_outlook(monkeypatch, run_state, open_items, state):
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import question_wait, repository
    monkeypatch.setattr(repository, "available", lambda: True)
    monkeypatch.setattr(legacy, "graph_owned", lambda flow_id: True)
    monkeypatch.setattr(legacy, "ai_run_record", lambda run_id: {"run_id": run_id, "group_id": F})
    monkeypatch.setattr(question_wait, "_waiting", lambda flow_id, run: ("work", {"node_key": "n"}, None))
    monkeypatch.setattr(question_wait, "open_questions", lambda flow_id, run_id: list(open_items))
    monkeypatch.setattr(legacy, "graph_run_state", lambda flow_id: run_state)
    assert question_wait.chat_answer_outlook(F, ASKER)["state"] == state


def test_chat_answer_outlook_without_a_waiting_card(monkeypatch):
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import question_wait, repository
    assert question_wait.chat_answer_outlook(F, None) == {"state": "not_waiting"}
    monkeypatch.setattr(repository, "available", lambda: True)
    monkeypatch.setattr(legacy, "graph_owned", lambda flow_id: False)
    assert question_wait.chat_answer_outlook(F, ASKER) == {"state": "not_graph"}
    monkeypatch.setattr(legacy, "graph_owned", lambda flow_id: True)
    monkeypatch.setattr(legacy, "ai_run_record", lambda run_id: None)
    assert question_wait.chat_answer_outlook(F, ASKER) == {"state": "not_waiting"}
    monkeypatch.setattr(legacy, "ai_run_record", lambda run_id: 1 / 0)
    assert question_wait.chat_answer_outlook(F, ASKER) == {"state": "unknown"}
