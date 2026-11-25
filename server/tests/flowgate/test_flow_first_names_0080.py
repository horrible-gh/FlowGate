"""0080: per-target naming (flow / start card / each chat), triggers and report projection."""
import pytest
from concurrent.futures import Future
from test_flow_first_names_0071 import (naming, env, db_path, legacy_cards, FA, USER, TEXT, TITLE, run, titles,
                                        FLOW_T, CARD_T, CHAT_T, ALL, DEFAULTS, ROOT_TEXT)
from test_fg_card_settings import _doc
from modules.flow_gate.db import documents, groups
from modules.flow_gate.graph_workflow.services import first_names_store as ledger
from modules.flow_gate.graph_workflow.services.flow_default_names import default_title, is_default_title
from modules.flow_gate.graph_workflow.store import title_store
from flowgate.conversation import auto_title as a, first_names_ports as p, first_names_triggers as t, legacy_adapter

pytestmark = pytest.mark.usefixtures("legacy_cards")


def inline(monkeypatch):
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: fn(*args) or True)


def no_context_runs(monkeypatch):
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: None)
    monkeypatch.setattr(legacy_adapter, "persisted_naming_run", lambda _: None)


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
@pytest.mark.parametrize("kind, doc_type", [("flow", None), ("chat", None), ("root", "R"), ("root", "B")])
def test_all_localized_defaults(locale, kind, doc_type):
    assert is_default_title(default_title(kind, locale, doc_type=doc_type), kind, doc_type=doc_type)
    assert not is_default_title("my title", kind, doc_type=doc_type)


@pytest.mark.parametrize("locale, message", [
    ("ko", "로그인 오류를 고쳐 주세요"), ("ja", "ログイン障害を修正してください"), ("en", "Fix the login error")])
def test_localized_targets_and_prompts(naming, monkeypatch, locale, message):
    groups.update(FA, {"title": default_title("flow", locale)})
    documents.update(naming["chat"], {"title": default_title("chat", locale), "meta": "{}"})
    naming["message"] = message
    assert a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"])["status"] == "done"
    assert titles(naming) == ALL
    prompts = dict(zip(naming["kinds"], naming["prompts"]))
    assert message in prompts["chat"] and message in prompts["flow"]
    assert all("same language" in x for x in prompts.values())


def test_prompts_name_different_subjects():
    assert "conversation" in a.build_name_prompt("chat", TEXT)
    assert "workflow" in a.build_name_prompt("flow", TEXT) and "whole task" in a.build_name_prompt("flow", TEXT)
    assert "document below" in a.build_name_prompt("root", ROOT_TEXT)
    assert all("NO_TITLE" in a.build_name_prompt(k, TEXT) for k in a.NAME_KINDS)


def test_root_lookup_failure_does_not_block_chat_and_recovers(naming, monkeypatch):
    original = p.title_views
    def fail(*args):
        raise RuntimeError("root unavailable")
    monkeypatch.setattr(p, "title_views", fail)
    result = run(naming)
    assert result["targets"]["chat"] == "done" and "root" not in result["targets"]
    assert documents.get_by_id(naming["chat"])["title"] == CHAT_T
    assert title_store.state(FA, naming["key"]) is None
    monkeypatch.setattr(p, "title_views", original)
    assert run(naming)["targets"]["root"] == "done"
    assert titles(naming) == ALL and naming["calls"] == 3


def test_existing_flow_name_does_not_rename_new_chat_with_it(naming):
    assert a.generate_flow_names(FA, USER, "p", TEXT)["status"] == "done"
    assert a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"])["targets"]["chat"] == "done"
    assert titles(naming) == ALL


def test_worker_token_context_without_live_or_persisted_run(naming, monkeypatch):
    queued = []
    class Pool:
        def submit(self, fn, *args):
            queued.append((fn, args))
            future = Future()
            future.set_result(None)
            return future
    monkeypatch.setattr(a, "_first_names_pool", Pool())
    no_context_runs(monkeypatch)
    turn = {"speaker": "ai", "body": "done", "source_run_id": "worker-run",
            "_naming_context": {"ai_run_id": "worker-run", "doc_ref": naming["chat"],
                                "issued_to": USER["user_id"], "provider_id": "worker-provider"}}
    assert t.schedule_reply(naming["chat"], turn)
    fn, args = queued[0]
    assert args == (naming["chat"], USER["user_id"], "worker-provider")
    fn(*args)
    assert titles(naming) == ALL
    assert not t.schedule_reply("other-chat", turn)


def test_completed_card_uses_actual_provider_and_shared_queue(monkeypatch):
    from modules.flow_gate.services import ai_run_end_hooks
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    from flowgate.conversation import integration
    calls = []
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: calls.append((fn, args)) or True)
    integration.register()
    try:
        ai_run_end_hooks.run_ended({"group_id": FA, "issued_to": "u", "provider_id": "actual", "doc_ref": "card"})
        assert calls == [(t._completed, (FA, "u", "actual", "card"))]
    finally:
        integration.unregister()
    assert first_names_hooks._completion_handler is None


def test_refresh_reports_only_changed_targets(naming, monkeypatch):
    events = []
    monkeypatch.setattr(t, "notify", lambda *args: events.append(args))
    run(naming)
    run(naming)
    assert events == [(FA, ["chat", "flow", "card"], naming["chat"])]


@pytest.mark.parametrize("report_type", ["TR", "TSR", "NR"])
def test_report_never_supplies_card_title(report_type):
    from modules.flow_gate.graph_workflow.projection.graph_projector import graph_node
    row = {"node_key": "card", "node_type": "document", "doc_type": "T", "label": "작업 카드"}
    report = {"doc_id": "report", "title": "작업 · 레포트", "type_code": report_type}
    instruction = {"doc_id": "instruction", "title": "서버 수정", "type_code": "T"}
    lines = [{"slot": "instruction", "author": "instruction_ai_or_human", "doc_id": "instruction"}]
    node = graph_node(row, "graph_node", "graph", report, {}, False, lines=lines,
                      by_id={"instruction": instruction})
    assert node["title"] == "서버 수정"
    assert graph_node(row, "graph_node", "graph", report, {}, False)["title"] == "작업 카드"
    assert graph_node(row, "graph_node", "graph", report, {}, False,
                      title_state={"title": "사용자 제목", "title_source": "human"})["title"] == "사용자 제목"


def test_custom_card_keeps_label_with_report_and_instruction():
    from modules.flow_gate.graph_workflow.projection.graph_projector import graph_node
    row = {"node_key": "custom", "node_type": "custom", "doc_type": "T", "label": "내 카드"}
    lines = [{"author": "instruction_ai_or_human", "doc_id": "instruction"}]
    report = {"doc_id": "report", "title": "작업 · 레포트", "type_code": "TR"}
    node = graph_node(row, "graph_node", "graph", report, {}, False, lines=lines,
                      by_id={"instruction": {"title": "지시서"}})
    assert node["title"] == "내 카드"


def test_completed_card_names_start_card_from_its_document_without_model(naming):
    t._completed(FA, USER["user_id"], "actual-provider", f"{FA}.0001-R")
    assert naming["calls"] == 0
    assert titles(naming) == ("새 플로우", "로그인 장애 요구사항", "새 대화")


def test_live_provider_wins_over_token_fallback(naming, monkeypatch):
    queued = []
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: queued.append(args) or True)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {
        "doc_ref": naming["chat"], "issued_to": "u", "provider_id": "actual"})
    assert t.schedule_reply(naming["chat"], {"speaker": "ai", "body": "done", "source_run_id": "run",
        "_naming_context": {"ai_run_id": "run", "doc_ref": naming["chat"], "issued_to": "u", "provider_id": "old"}})
    assert queued == [(naming["chat"], "u", "actual")]


def test_remote_chat_reply_names_chat_and_flow_separately_without_ai(naming, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {"provider_id": "remote"})
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("remote call"))
    inline(monkeypatch)
    token = {"group_id": FA, "issued_to": USER["user_id"], "ai_run_id": "remote-run"}
    guidance = t.title_request(FA, "ko", "chat", naming["chat"])
    assert "`title`" in guidance and "`flow_title`" in guidance
    t.submitted_title(token, f'"{CHAT_T}"', naming["chat"], flow_title=FLOW_T)
    # The start card is named from its own document's first line: no model, no shared name.
    assert titles(naming) == (FLOW_T, "로그인 장애 요구사항", CHAT_T)
    assert t.title_request(FA, "ko", "chat", naming["chat"]) == ""
    t.submitted_title(token, "ignored later title", naming["chat"], flow_title="ignored later flow")
    assert titles(naming) == (FLOW_T, "로그인 장애 요구사항", CHAT_T)


def test_chat_title_never_names_the_flow_or_card(naming, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {"provider_id": "remote"})
    inline(monkeypatch)
    naming["root_text"] = ""
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"]}, CHAT_T, naming["chat"])
    assert titles(naming) == ("새 플로우", None, CHAT_T)
    assert "`flow_title`" in t.title_request(FA, "ko", "chat", naming["chat"])


def test_card_submission_names_only_the_flow(naming, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    no_context_runs(monkeypatch)
    inline(monkeypatch)
    naming["root_text"] = ""
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "remote"},
                      "chat title ignored", f"{FA}.0001-R", flow_title=FLOW_T)
    assert titles(naming) == (FLOW_T, None, "새 대화")


def test_remote_missing_title_logs_reason_and_next_reply_recovers(naming, monkeypatch, caplog):
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    naming["root_text"] = ""
    assert run(naming)["status"] == "unchanged"
    assert "reason=title_missing" in caplog.text
    assert naming["calls"] == 0
    assert a.generate_flow_names(FA, USER, "remote", chat_doc_id=naming["chat"], title=CHAT_T,
                                 flow_title=FLOW_T)["status"] == "done"
    assert titles(naming) == (FLOW_T, None, CHAT_T)


def test_local_failure_only_one_call_per_target_and_safe_reason(naming, monkeypatch, caplog):
    naming["answers"] = {"flow": None, "root": None, "chat": None}
    naming["root_text"] = "..."
    assert run(naming)["status"] == "unchanged"
    assert run(naming)["status"] == "unchanged"
    assert sorted(naming["kinds"]) == ["chat", "flow"]  # one supplementary request per target
    assert "reason=timeout" in caplog.text
    assert TEXT not in caplog.text
    assert run(naming, title=CHAT_T, flow_title=FLOW_T)["targets"] == {
        "chat": "done", "flow": "done", "root": "unchanged"}
    assert naming["calls"] == 2


def test_remote_partial_save_reuses_candidate(naming, monkeypatch):
    original = p.apply
    def fail(flow, kind, *args):
        if kind == "chat":
            raise RuntimeError("temporary")
        return original(flow, kind, *args)
    monkeypatch.setattr(p, "apply", fail)
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    assert a.generate_flow_names(FA, USER, "remote", chat_doc_id=naming["chat"], title=CHAT_T,
                                 flow_title=FLOW_T)["status"] == "pending"
    monkeypatch.setattr(p, "apply", original)
    assert a.generate_flow_names(FA, USER, "remote", chat_doc_id=naming["chat"])["status"] == "done"
    assert titles(naming) == (FLOW_T, "로그인 장애 요구사항", CHAT_T)
    assert naming["calls"] == 0


@pytest.mark.parametrize("action", ["new", "edit"])
@pytest.mark.parametrize("dry_run, status, changed", [(False, 200, True), (True, 200, False), (False, 422, False)])
def test_card_submission_only_names_after_success(naming, monkeypatch, dry_run, status, changed, action):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    from starlette.responses import JSONResponse
    token = {"group_id": FA, "issued_to": USER["user_id"], "provider_id": "remote"}
    monkeypatch.setattr(inbox_routes.token_service, "inspect_for_replay", lambda _: token)
    monkeypatch.setattr(first_names_hooks, "_title_handler", t.submitted_title)
    monkeypatch.setattr(t, "_schedule", lambda *args: True)
    no_context_runs(monkeypatch)
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("extra call"))
    response = inbox_routes._in_write_session(
        lambda *args: JSONResponse({"ok": status == 200}, status_code=status), None, "raw",
        {"action": action, "doc_id": f"{FA}.0001-R", "group_id": "forged",
         "title": FLOW_T if action == "edit" else "서버 수정 · 레포트",
         "flow_title": FLOW_T, "dry_run": dry_run})
    assert response.status_code == status
    assert titles(naming) == ((FLOW_T, None, "새 대화") if changed else DEFAULTS)


def test_handled_worker_reply_does_not_schedule_second_fallback(monkeypatch):
    from flowgate.conversation import integration, activity
    monkeypatch.setattr(activity, "on_turn_appended", lambda *args: None)
    monkeypatch.setattr(t, "schedule_reply", lambda *args: pytest.fail("duplicate fallback"))
    integration.on_conversation_reply_appended("chat", {"_naming_handled": True})


def test_remote_provider_detection_uses_effective_metadata(monkeypatch):
    from modules.flow_gate.services import ai_connection_test_service as connection
    monkeypatch.setattr(connection, "_effective", lambda _: {"providers": [
        {"id": "remote", "exec_type": "cli", "exec_location": "remote"},
        {"id": "local", "exec_type": "cli", "exec_location": "local"},
        {"id": "api", "exec_type": "api"}]})
    assert legacy_adapter.naming_remote_provider("p", "remote")
    assert not legacy_adapter.naming_remote_provider("p", "local")
    assert not legacy_adapter.naming_remote_provider("p", "api")


@pytest.mark.parametrize("model_answer", ["늦은 보충 제목", None])
def test_submitted_seed_preempts_live_fallback_lease(naming, monkeypatch, model_answer):
    accepted = []
    def complete(*args, **kwargs):
        if a.build_name_prompt("chat", TEXT) == args[2]:
            assert ledger.load(FA)["names"]["chat:" + naming["chat"]]["lease_until"] > 0
            accepted.append(a.generate_flow_names(FA, USER, "p", TEXT, naming["chat"], title=CHAT_T,
                                                  kinds=("chat",)))
            assert titles(naming)[2] == CHAT_T
            return model_answer, "timeout" if model_answer is None else None
        return FLOW_T if "workflow" in args[2] else CARD_T, None
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete)
    run(naming)
    assert accepted[0]["status"] == "done"
    entry = ledger.load(FA)["names"]["chat:" + naming["chat"]]
    assert entry["candidate"] == CHAT_T and entry["lease_until"] == 0
    assert titles(naming) == ALL
    assert run(naming)["status"] == "done"


@pytest.mark.parametrize("locale, required", [("ko", "반드시"), ("en", "must"), ("ja", "必ず"), ("fr", "반드시")])
def test_new_title_guidance_requires_flow_title(naming, locale, required):
    text = t.title_request(FA, locale, "new")
    assert required in text and "`flow_title`" in text and "`title`" in text
    assert not any(word in text for word in ("선택", "optional", "任意"))


def test_card_submission_without_flow_title_reads_root_document(naming, monkeypatch):
    no_context_runs(monkeypatch)
    inline(monkeypatch)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"}, None,
                      f"{FA}.0001-R")
    prompts = dict(zip(naming["kinds"], naming["prompts"]))
    # No chat is involved: the flow is named from the first request (the root body) and the
    # card from its own document; no chat is renamed.
    assert "chat" not in prompts
    assert "로그인 실패 원인을 찾아 고친다" in prompts["flow"] and "로그인 실패 원인을 찾아 고친다" in prompts["root"]
    assert titles(naming) == (FLOW_T, CARD_T, "새 대화")


@pytest.mark.parametrize("chat_message", [None, "사용자 대화 우선"])
def test_flow_uses_requirement_only_when_chat_is_empty(naming, monkeypatch, chat_message):
    reads = []
    def turns(doc_id, *args):
        reads.append(doc_id)
        return [{"speaker": "user", "body": chat_message}] if chat_message else []
    monkeypatch.setattr(legacy_adapter, "first_turns", turns)
    result = a.generate_flow_names(FA, USER, "provider", "요구사항 대체 내용", naming["chat"])
    prompts = dict(zip(naming["kinds"], naming["prompts"]))
    assert set(reads) == {naming["chat"]}
    assert (chat_message or "요구사항 대체 내용") in prompts["flow"]
    assert ("chat" in prompts) == bool(chat_message)
    assert result["targets"]["flow"] == "done" and result["targets"]["root"] == "done"


@pytest.mark.parametrize("locale, word", [("ko", "실제 작업"), ("en", "actual task facts"), ("ja", "実際の作業")])
def test_title_guidance_localized_and_action_scoped(naming, locale, word):
    from modules.flow_gate.services import mention_service as m
    from modules.flow_gate.graph_workflow.services import first_names_hooks as hooks
    from unittest.mock import patch
    with patch.object(hooks, "_request_handler", t.title_request):
        for action in ("review", "preflight", "test_run", "failure_origin_review"):
            assert hooks.title_request(FA, locale, action) == ""
        assert word in hooks.title_request(FA, locale, "chat")
        kwargs = dict(project="p", module="m", group="0001", group_id=FA,
            parent_type="T", parent_doc_number="T0001", parent_title="Task", parent_doc_id="T0001",
            head_type="TR", head_status="open", scratch_dir="scratch", raw_token="test",
            api_base_url="http://localhost/flowgate/api/v1", locale=locale)
        text = m.build_mention(**kwargs, action_scope="edit")
        assert word in text
        heading = {"ko": "작업 이름", "en": "Task names", "ja": "作業名"}[locale]
        assert "## " + heading in text
        text = m.build_mention(**kwargs, action_scope="new")
        assert "## " + heading in text and "`flow_title`" in text and word in text
        assert heading not in m.build_mention(**kwargs, action_scope="review")
        a.generate_flow_names(FA, USER, "p", TEXT, naming["chat"], flow_title=FLOW_T, kinds=("flow",))
        assert hooks.title_request(FA, locale, "new") == ""
        assert "## " + heading not in m.build_mention(**kwargs, action_scope="new")
        # The chat still owes its own title after the flow was named.
        assert "`title`" in hooks.title_request(FA, locale, "chat", naming["chat"])
        assert "`flow_title`" not in hooks.title_request(FA, locale, "chat", naming["chat"])


def test_new_report_title_is_never_a_seed(naming, monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    from starlette.responses import JSONResponse
    token = {"group_id": FA, "issued_to": USER["user_id"], "provider_id": "remote"}
    monkeypatch.setattr(inbox_routes.token_service, "inspect_for_replay", lambda _: token)
    monkeypatch.setattr(first_names_hooks, "_title_handler", t.submitted_title)
    inline(monkeypatch)
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("remote call"))
    no_context_runs(monkeypatch)
    naming["root_text"] = ""
    body = {"action": "new", "title": "서버 수정 · 레포트"}
    def register(request, raw, value):
        assert value["title"] == "서버 수정 · 레포트"
        return JSONResponse({"ok": True}, status_code=201)
    assert inbox_routes._in_write_session(register, None, "raw", body).status_code == 201
    assert body["title"] == "서버 수정 · 레포트"
    assert not ledger.load(FA).get("names", {}).get("flow", {}).get("candidate")
    assert titles(naming) == DEFAULTS


@pytest.mark.parametrize("manual", [False, True])
def test_api_new_flow_title_reaches_flow_name(naming, monkeypatch, manual):
    from modules.flow_gate.services import api_server_tools as tools
    from modules.flow_gate.services.ai_invoke import worker
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    from starlette.responses import JSONResponse
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    token = {"group_id": FA, "issued_to": USER["user_id"], "ai_run_id": "run", "provider_id": "stale"}
    monkeypatch.setattr(inbox_routes.token_service, "inspect_for_replay", lambda _: token)
    monkeypatch.setattr(first_names_hooks, "_title_handler", t.submitted_title)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {"provider_id": "actual"})
    monkeypatch.setattr(t, "_schedule", lambda *args: True)
    seen = []
    generate = a.generate_flow_names
    def capture(flow, user, provider, *args, **kwargs):
        seen.append((flow, user, provider))
        return generate(flow, user, provider, *args, **kwargs)
    monkeypatch.setattr(a, "generate_flow_names", capture)
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("extra AI call"))
    if manual:
        groups.update(FA, {"title": "내 플로우"})
        with_flow_guard(FA, lambda: title_store.save_human(FA, naming["key"], "내 카드", USER["user_id"]))
        documents.update(naming["chat"], {"title": "내 대화"})
    payload = {"doc_type": "TR", "content": "Report body", "title": "서버 수정 · 레포트", "flow_title": FLOW_T}
    tools.validate(tools.REGISTER_SCHEMAS["new"], payload)
    body = worker._register_envelope({"action": "new", "doc": "root", "project": "p", "group": FA}, {}, payload)
    assert body["title"] == payload["title"] and body["flow_title"] == FLOW_T
    response = inbox_routes._in_write_session(lambda *args: JSONResponse({"ok": True}, status_code=201), None, "raw", body)
    assert response.status_code == 201
    assert seen == [(FA, USER, "actual")]
    expected = ("내 플로우", "내 카드", "내 대화") if manual else (FLOW_T, None, "새 대화")
    assert titles(naming) == expected
    body["flow_title"] = "후속 이름"
    inbox_routes._in_write_session(lambda *args: JSONResponse({"ok": True}, status_code=201), None, "raw", body)
    assert titles(naming) == expected


@pytest.mark.parametrize("source", ["submission", "fallback"])
@pytest.mark.parametrize("answer", ["Build a task management application", "가" * 41, "𠮷" * 35])
def test_long_title_fits_real_card_constraint(naming, source, answer):
    naming["answers"] = {"flow": answer, "root": answer, "chat": answer}
    kwargs = {"title": answer, "flow_title": answer} if source == "submission" else {}
    result = run(naming, **kwargs)
    assert result["targets"] == {"chat": "done", "flow": "done", "root": "done"}
    expected = answer[:29] + "…"
    assert titles(naming) == (expected, expected, expected)
    assert ledger.load(FA)["names"]["flow"]["candidate"] == expected
    assert naming["calls"] == (1 if source == "submission" else 3)  # the card reads its document


@pytest.mark.parametrize("bad", [
    "flowgate.v02test.0080.0006-CH task", "Task flowgate.v02test.F80",
    "0006-CH task", "F80-12 task", "NO_TITLE",
    "Build an application with a very long prefix flowgate.v02test.0080.0006-CH",
])
def test_bad_submission_uses_own_content_fallback(naming, monkeypatch, bad):
    no_context_runs(monkeypatch)
    inline(monkeypatch)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "p"},
                      bad, naming["chat"], flow_title=bad)
    assert titles(naming) == ALL
    assert naming["calls"] == 3
    assert ledger.load(FA)["names"]["chat:" + naming["chat"]]["candidate"] == CHAT_T


@pytest.mark.parametrize("source", ["submission", "fallback"])
def test_invalid_candidate_never_reaches_any_target(naming, monkeypatch, source):
    bad = "flowgate.v02test.0080.0006-CH title"
    naming["answers"] = {"flow": bad, "root": bad, "chat": bad}
    naming["root_text"] = bad
    if source == "submission":
        monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    seeds = {"title": bad, "flow_title": bad} if source == "submission" else {}
    assert run(naming, **seeds)["status"] == "unchanged"
    assert titles(naming) == DEFAULTS
    assert not any(e.get("candidate") for e in ledger.load(FA).get("names", {}).values())
    assert run(naming, title=CHAT_T, flow_title=FLOW_T)["targets"]["chat"] == "done"
    assert titles(naming) == (FLOW_T, None, CHAT_T)


@pytest.mark.parametrize("locale, word", [("ko", "문서 번호"), ("en", "document IDs"), ("ja", "文書番号")])
@pytest.mark.parametrize("action", ["chat", "edit", "new"])
def test_title_guidance_bounds_and_excludes_identifiers(naming, locale, word, action):
    guidance = t.title_request(FA, locale, action)
    assert "30" in guidance and word in guidance


def test_heading_title_reads_first_meaningful_line():
    assert a.heading_title("root", "\n---\n# 할 일 목록 앱 요구사항\n본문") == "할 일 목록 앱 요구사항"
    assert a.heading_title("root", "## 요구사항\n- 로그인 장애를 고친다", doc_type="R") == "로그인 장애를 고친다"
    assert a.heading_title("root", "flowgate.v02test.0080.0001-R\n...") is None
