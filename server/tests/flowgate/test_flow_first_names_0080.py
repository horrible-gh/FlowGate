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


def test_second_chat_has_own_candidate(naming, monkeypatch):
    assert run(naming)["status"] == "done"
    second = _doc(FA, 13, "CH")
    second = second["doc_id"] if isinstance(second, dict) else second or f"{FA}.0013-CH"
    documents.update(second, {"title": "새 대화", "meta": "{}"})
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [{"speaker": "user", "body": "배포 속도 개선"}])
    calls = []
    def complete(*args, **kwargs):
        calls.append(args[2])
        return "배포 최적화", None
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete)
    for _ in range(2):
        assert a.generate_flow_names(FA, USER, "p", chat_doc_id=second)["status"] == "done"
    assert len(calls) == 1 and "배포 속도 개선" in calls[0]
    assert documents.get_by_id(second)["title"] == "배포 최적화"
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
    assert run(naming)["status"] == "done"
    assert titles(naming) == (TITLE, TITLE, TITLE)


def test_existing_flow_candidate_does_not_name_new_chat(naming, monkeypatch):
    assert a.generate_flow_names(FA, USER, "p", TEXT)["status"] == "done"
    monkeypatch.setattr(legacy_adapter, "complete_prompt", lambda *args, **kwargs: ("다른 대화", None))
    assert a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"])["status"] == "done"
    assert titles(naming) == (TITLE, TITLE, "다른 대화")


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
    assert seen == ["actual-provider"]
    assert titles(naming) == (TITLE, TITLE, "새 대화")


def test_live_provider_wins_over_token_fallback(naming, monkeypatch):
    queued = []
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: queued.append(args) or True)
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {
        "doc_ref": naming["chat"], "issued_to": "u", "provider_id": "actual"})
    assert t.schedule_reply(naming["chat"], {"speaker": "ai", "body": "done", "source_run_id": "run",
        "_naming_context": {"ai_run_id": "run", "doc_ref": naming["chat"], "issued_to": "u", "provider_id": "old"}})
    assert queued == [(naming["chat"], "u", "actual")]
