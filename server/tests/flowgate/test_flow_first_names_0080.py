"""0080: per-conversation naming, token/completion triggers and report projection."""
import pytest
from concurrent.futures import Future
from test_flow_first_names_0071 import naming, env, db_path, legacy_cards, FA, USER, TEXT, TITLE, run, titles
from test_fg_card_settings import _doc
from modules.flow_gate.db import documents, groups
from modules.flow_gate.graph_workflow.services import first_names_store as ledger
from modules.flow_gate.graph_workflow.services.flow_default_names import default_title, is_default_title
from modules.flow_gate.graph_workflow.store import title_store
from flowgate.conversation import auto_title as a, first_names_ports as p, first_names_triggers as t, legacy_adapter

pytestmark = pytest.mark.usefixtures("legacy_cards")


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
@pytest.mark.parametrize("kind, doc_type", [("flow", None), ("chat", None), ("root", "R"), ("root", "B")])
def test_all_localized_defaults(locale, kind, doc_type):
    assert is_default_title(default_title(kind, locale, doc_type=doc_type), kind, doc_type=doc_type)
    assert not is_default_title("my title", kind, doc_type=doc_type)


@pytest.mark.parametrize("locale, message, answer", [
    ("ko", "로그인 오류를 고쳐 주세요", "로그인 오류 수정"),
    ("ja", "ログイン障害を修正してください", "ログイン障害の修正"),
    ("en", "Fix the login error", "Fix login error")])
def test_localized_targets_and_prompt(naming, monkeypatch, locale, message, answer):
    groups.update(FA, {"title": default_title("flow", locale)})
    documents.update(naming["chat"], {"title": default_title("chat", locale), "meta": "{}"})
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [{"speaker": "user", "body": message}])
    prompts = []
    def complete(*args, **kwargs):
        prompts.append(args[2])
        return answer, None
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete)
    assert a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"])["status"] == "done"
    assert titles(naming) == (answer, answer, answer)
    assert len(prompts) == 1 and message in prompts[0] and "same language" in prompts[0]


def test_second_chat_reuses_flow_candidate_without_call(naming, monkeypatch):
    assert run(naming)["status"] == "done"
    second = _doc(FA, 13, "CH")
    second = second["doc_id"] if isinstance(second, dict) else second or f"{FA}.0013-CH"
    documents.update(second, {"title": "새 대화", "meta": "{}"})
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("extra call"))
    for _ in range(2):
        assert a.generate_flow_names(FA, USER, "p", chat_doc_id=second)["status"] == "done"
    assert documents.get_by_id(second)["title"] == TITLE
    assert titles(naming) == (TITLE, TITLE, TITLE)


def test_root_lookup_failure_does_not_block_chat_and_recovers(naming, monkeypatch):
    original = p.title_views
    def fail(*args):
        raise RuntimeError("root unavailable")
    monkeypatch.setattr(p, "title_views", fail)
    assert run(naming)["targets"]["chat"] == "done"
    assert documents.get_by_id(naming["chat"])["title"] == TITLE
    assert title_store.state(FA, naming["key"]) is None
    monkeypatch.setattr(p, "title_views", original)
    assert run(naming)["targets"]["root"] == "done"
    assert naming["calls"] == 1


def test_failure_retries_next_reply_without_lease_expiry(naming, monkeypatch):
    complete = legacy_adapter.complete_prompt
    def fail(*args, **kwargs):
        raise RuntimeError("temporary")
    monkeypatch.setattr(legacy_adapter, "complete_prompt", fail)
    assert run(naming)["status"] == "failed"
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete)
    assert a.generate_flow_names(FA, USER, "provider", TEXT, naming["chat"], title=TITLE)["status"] == "done"
    assert titles(naming) == (TITLE, TITLE, TITLE)


def test_existing_flow_candidate_names_new_chat(naming, monkeypatch):
    assert a.generate_flow_names(FA, USER, "p", TEXT)["status"] == "done"
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: ("다른 대화", None))
    assert a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"])["status"] == "done"
    assert titles(naming) == (TITLE, TITLE, TITLE)


def test_worker_token_context_without_live_or_persisted_run(naming, monkeypatch):
    queued = []
    class Pool:
        def submit(self, fn, *args):
            queued.append((fn, args))
            future = Future()
            future.set_result(None)
            return future
    monkeypatch.setattr(a, "_first_names_pool", Pool())
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: None)
    monkeypatch.setattr(legacy_adapter, "persisted_naming_run", lambda _: None)
    turn = {"speaker": "ai", "body": "done", "source_run_id": "worker-run",
            "_naming_context": {"ai_run_id": "worker-run", "doc_ref": naming["chat"],
                                "issued_to": USER["user_id"], "provider_id": "worker-provider"}}
    assert t.schedule_reply(naming["chat"], turn)
    fn, args = queued[0]
    assert args == (naming["chat"], USER["user_id"], "worker-provider")
    fn(*args)
    assert titles(naming) == (TITLE, TITLE, TITLE)
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
    assert events == [(FA, ["flow", "card", "chat"], naming["chat"])]


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


def test_completed_card_names_flow_and_root_with_actual_provider(naming, monkeypatch):
    seen = []
    monkeypatch.setattr(t, "run_context", lambda flow, revision, provider: (provider, TEXT, None))
    def complete(project, provider, prompt, **kwargs):
        seen.append(provider)
        return TITLE, None
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete)
    t._completed(FA, USER["user_id"], "actual-provider", f"{FA}.0001-R")
    assert seen == []
    assert titles(naming) == ("새 플로우", None, "새 대화")


def test_live_provider_wins_over_token_fallback(naming, monkeypatch):
    queued = []
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: queued.append(args) or True)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {
        "doc_ref": naming["chat"], "issued_to": "u", "provider_id": "actual"})
    assert t.schedule_reply(naming["chat"], {"speaker": "ai", "body": "done", "source_run_id": "run",
        "_naming_context": {"ai_run_id": "run", "doc_ref": naming["chat"], "issued_to": "u", "provider_id": "old"}})
    assert queued == [(naming["chat"], "u", "actual")]


@pytest.mark.parametrize("source", ["chat", "card"])
def test_remote_submission_title_shared_without_extra_ai(naming, monkeypatch, source):
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {"provider_id": "remote"})
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("remote call"))
    token = {"group_id": FA, "issued_to": USER["user_id"], "ai_run_id": "remote-run"}
    doc_id = naming["chat"] if source == "chat" else f"{FA}.0001-R"
    assert t.title_request(FA)
    t.submitted_title(token, '"로그인 오류 수정"', doc_id)
    assert titles(naming) == (TITLE, TITLE, TITLE)
    assert not t.title_request(FA)
    t.submitted_title(token, "ignored later title", doc_id)
    assert titles(naming) == (TITLE, TITLE, TITLE)
    assert naming["calls"] == 0


def test_remote_missing_title_logs_reason_and_next_reply_recovers(naming, monkeypatch, caplog):
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    assert run(naming)["status"] == "unchanged"
    assert "reason=title_missing" in caplog.text
    assert naming["calls"] == 0
    assert a.generate_flow_names(FA, USER, "remote", chat_doc_id=naming["chat"], title=TITLE)["status"] == "done"
    assert titles(naming) == (TITLE, TITLE, TITLE)


def test_local_failure_only_one_call_and_safe_reason(naming, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: (calls.append(1) or (None, "timeout")))
    assert run(naming)["status"] == "unchanged"
    assert run(naming)["status"] == "unchanged"
    assert len(calls) == 1  # One supplementary request for the entire flow, including failures.
    assert "reason=timeout" in caplog.text
    assert TEXT not in caplog.text
    assert a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], title=TITLE)["status"] == "done"
    assert len(calls) == 1


def test_remote_partial_save_reuses_candidate(naming, monkeypatch):
    original = p.apply
    def fail(flow, kind, *args):
        if kind == "root":
            raise RuntimeError("temporary")
        return original(flow, kind, *args)
    monkeypatch.setattr(p, "apply", fail)
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("extra call"))
    assert a.generate_flow_names(FA, USER, "remote", chat_doc_id=naming["chat"], title=TITLE)["status"] == "pending"
    monkeypatch.setattr(p, "apply", original)
    assert a.generate_flow_names(FA, USER, "remote", chat_doc_id=naming["chat"])["status"] == "done"
    assert titles(naming) == (TITLE, TITLE, TITLE)


@pytest.mark.parametrize("action", ["new", "edit"])
@pytest.mark.parametrize("dry_run, status, changed", [(False, 200, True), (True, 200, False), (False, 422, False)])
def test_card_submission_only_names_after_success(naming, monkeypatch, dry_run, status, changed, action):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    from starlette.responses import JSONResponse
    token = {"group_id": FA, "issued_to": USER["user_id"], "provider_id": "remote"}
    monkeypatch.setattr(inbox_routes.token_service, "inspect_for_replay", lambda _: token)
    monkeypatch.setattr(first_names_hooks, "_title_handler", t.submitted_title)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: None)
    monkeypatch.setattr(legacy_adapter, "persisted_naming_run", lambda _: None)
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("extra call"))
    response = inbox_routes._in_write_session(
        lambda *args: JSONResponse({"ok": status == 200}, status_code=status), None, "raw",
        {"action": action, "doc_id": f"{FA}.0001-R", "group_id": "forged",
         "title": TITLE if action == "edit" else "서버 수정 · 레포트",
         "flow_title": TITLE, "dry_run": dry_run})
    assert response.status_code == status
    assert titles(naming) == ((TITLE, TITLE, TITLE) if changed else ("새 플로우", None, "새 대화"))


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
        assert ledger.load(FA)["lease_until"] > 0
        accepted.append(a.generate_flow_names(FA, USER, "p", TEXT, naming["chat"], title=TITLE))
        assert titles(naming) == (TITLE, TITLE, TITLE)
        return model_answer, "timeout" if model_answer is None else None
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete)
    run(naming)
    assert accepted[0]["status"] == "done"
    assert ledger.load(FA)["candidate"] == TITLE
    assert ledger.load(FA)["lease_until"] == 0
    assert titles(naming) == (TITLE, TITLE, TITLE)
    assert run(naming)["status"] == "done"


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
        heading = {"ko": "공통 작업 이름", "en": "Shared task title", "ja": "共通の作業名"}[locale]
        assert "## " + heading in text
        text = m.build_mention(**kwargs, action_scope="new")
        assert "## " + heading in text and "`flow_title`" in text and word in text
        assert heading not in m.build_mention(**kwargs, action_scope="review")
        a.generate_flow_names(FA, USER, "p", TEXT, naming["chat"], title=TITLE)
        assert hooks.title_request(FA, locale, "new") == ""
        assert heading not in m.build_mention(**kwargs, action_scope="new")


def test_new_report_title_is_never_a_shared_seed(naming, monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    from starlette.responses import JSONResponse
    token = {"group_id": FA, "issued_to": USER["user_id"], "provider_id": "remote"}
    monkeypatch.setattr(inbox_routes.token_service, "inspect_for_replay", lambda _: token)
    monkeypatch.setattr(first_names_hooks, "_title_handler", t.submitted_title)
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: fn(*args))
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: True)
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: pytest.fail("remote call"))
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: None)
    monkeypatch.setattr(legacy_adapter, "persisted_naming_run", lambda _: None)
    body = {"action": "new", "title": "서버 수정 · 레포트"}
    def register(request, raw, value):
        assert value["title"] == "서버 수정 · 레포트"
        return JSONResponse({"ok": True}, status_code=201)
    assert inbox_routes._in_write_session(register, None, "raw", body).status_code == 201
    assert body["title"] == "서버 수정 · 레포트"
    assert not ledger.load(FA).get("candidate")
    assert titles(naming) == ("새 플로우", None, "새 대화")


@pytest.mark.parametrize("manual", [False, True])
def test_api_new_flow_title_reaches_shared_names(naming, monkeypatch, manual):
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
    payload = {"doc_type": "TR", "content": "Report body", "title": "서버 수정 · 레포트", "flow_title": TITLE}
    tools.validate(tools.REGISTER_SCHEMAS["new"], payload)
    body = worker._register_envelope({"action": "new", "doc": "root", "project": "p", "group": FA}, {}, payload)
    assert body["title"] == payload["title"] and body["flow_title"] == TITLE
    response = inbox_routes._in_write_session(lambda *args: JSONResponse({"ok": True}, status_code=201), None, "raw", body)
    assert response.status_code == 201
    assert seen == [(FA, USER, "actual")]
    expected = ("내 플로우", "내 카드", "내 대화") if manual else (TITLE, TITLE, TITLE)
    assert titles(naming) == expected
    body["flow_title"] = "후속 이름"
    inbox_routes._in_write_session(lambda *args: JSONResponse({"ok": True}, status_code=201), None, "raw", body)
    assert titles(naming) == expected
