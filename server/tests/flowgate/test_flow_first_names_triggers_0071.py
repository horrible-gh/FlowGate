"""Entry-point and refresh integration using the card 2 real database fixture."""
import json
import pytest
from test_flow_first_names_0071 import naming, env, db_path, legacy_cards, FA, USER, TEXT, TITLE, titles
from flowgate.conversation import first_names_triggers as t, auto_title as a, legacy_adapter, activity
from modules.flow_gate.db import documents
from modules.flow_gate.graph_workflow.api import run_routes as routes
from starlette.requests import Request
from modules.flow_gate.graph_workflow.services import first_names_hooks
from modules.flow_gate.api.v1.events import publisher

pytestmark = pytest.mark.usefixtures("legacy_cards")

@pytest.fixture()
def wired(naming, monkeypatch):
    monkeypatch.setattr(activity, "readable_chat_document", lambda doc_id, user: documents.get_by_id(doc_id))
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [
        {"speaker": "user", "body": TEXT}, {"speaker": "ai", "body": "확인합니다"}])
    monkeypatch.setattr(legacy_adapter, "is_group_disposed", lambda _: False)
    monkeypatch.setattr(t, "run_context", lambda *args: ("provider", TEXT, naming["chat"]))
    return naming

@pytest.mark.parametrize("first", ["chat", "run"])
def test_both_orders_share_one_candidate_and_saved_titles(wired, first):
    chat = lambda: t.chat(wired["chat"], USER, {"provider_id": "provider"})
    run = lambda: t._run(FA, USER["user_id"], 1)
    (chat if first == "chat" else run)()
    (run if first == "chat" else chat)()
    assert titles(wired) == (TITLE, TITLE, TITLE)
    assert wired["calls"] == 1


def test_unflagged_chat_participates(wired):
    documents.update(wired["chat"], {"meta": "{}"})
    t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert titles(wired) == (TITLE, TITLE, TITLE)


def test_chat_before_ai_reply_does_not_generate(wired, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [{"speaker": "user", "body": TEXT}])
    with pytest.raises(a.c.ConversationError):
        t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert wired["calls"] == 0


def test_run_notification_is_after_commit_and_scoped(wired, monkeypatch):
    from modules.flow_gate.db.connection import in_transaction
    events = []
    def publish(event):
        assert not in_transaction()
        assert titles(wired) == (TITLE, TITLE, TITLE)
        events.append(event)
    monkeypatch.setattr(publisher, "broadcast_event_nowait", publish)
    t._run(FA, USER["user_id"], 1)
    assert len(events) == 1
    assert events[0].group_id == FA


def test_chat_and_run_overlap_share_claim(wired):
    wired["hook"] = lambda: t._run(FA, USER["user_id"], 1)
    t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert wired["calls"] == 1 and titles(wired) == (TITLE, TITLE, TITLE)


def test_run_without_chat_does_not_create_chat(wired, monkeypatch):
    monkeypatch.setattr(t, "run_context", lambda *args: ("provider", TEXT, None))
    before = documents.list_documents("p", group_id=FA, type_code="CH")
    t._run(FA, USER["user_id"], 1)
    assert titles(wired) == (TITLE, TITLE, "새 대화")
    assert documents.list_documents("p", group_id=FA, type_code="CH") == before


@pytest.mark.parametrize("mode", ["full", "step"])
def test_route_schedules_only_after_successful_full_run(monkeypatch, mode):
    order = []
    monkeypatch.setattr(routes.envelope, "authenticate", lambda _: "u")
    monkeypatch.setattr(routes.envelope, "authorize", lambda *args: None)
    monkeypatch.setattr(routes.canvas_service, "load_flow", lambda _: {"group_id": FA})
    def start(*args):
        order.append("committed")
        return {"run": {"mode": mode, "revision_no": 7}}
    monkeypatch.setattr(routes.run_service, "start_run", start)
    monkeypatch.setattr(first_names_hooks, "schedule", lambda *args: order.append(args))
    response = routes.post_flow_run(FA, Request({"type": "http", "headers": []}), {"mode": mode})
    assert response.status_code == 201
    assert order == (["committed", (FA, "u", 7)] if mode == "full" else ["committed"])


def test_naming_schedule_failure_cannot_fail_accepted_run(monkeypatch):
    monkeypatch.setattr(routes.envelope, "authenticate", lambda _: "u")
    monkeypatch.setattr(routes.envelope, "authorize", lambda *args: None)
    monkeypatch.setattr(routes.canvas_service, "load_flow", lambda _: {"group_id": FA})
    monkeypatch.setattr(routes.run_service, "start_run", lambda *args: {"run": {"mode": "full", "revision_no": 7}})
    def fail(*args):
        raise RuntimeError("closed pool")
    monkeypatch.setattr(first_names_hooks, "schedule", fail)
    response = routes.post_flow_run(FA, Request({"type": "http", "headers": []}), {"mode": "full"})
    assert response.status_code == 201


def test_real_run_context_reads_bound_body_and_selected_provider(naming, monkeypatch, tmp_path):
    from modules.flow_gate.graph_workflow.store import definition_store, assignment_store
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    from modules.flow_gate.storage import paths
    root = tmp_path / "root.md"
    root.write_text("---\ntitle: placeholder\n---\n" + TEXT, encoding="utf-8")
    monkeypatch.setattr(definition_store, "revision", lambda f, r: {"nodes": [{"node_key": "a", "assignee_provider_id": "old"}]})
    monkeypatch.setattr(card_settings_ports, "uses_ai", lambda *args: True)
    monkeypatch.setattr(card_settings_ports, "run_settings", lambda *args: {"provider_id": "selected"})
    monkeypatch.setattr(assignment_store, "root_binding", lambda _: {"doc_id": naming["chat"]})
    monkeypatch.setattr(paths, "locate_storage_path", lambda *args, **kwargs: (root, "readable"))
    provider, text, chat_id = t.run_context(FA, 1)
    assert provider == "selected" and text.strip() == TEXT and chat_id == naming["chat"]
    root.write_text("---\ntitle: metadata only\n---\n", encoding="utf-8")
    assert not t.run_context(FA, 1)[1].strip()


def test_reply_hook_uses_run_provider_without_waiting_for_worker(wired, monkeypatch):
    from concurrent.futures import Future
    queued = []
    monkeypatch.setattr(legacy_adapter, "get_run_record", lambda _: {
        "doc_ref": wired["chat"], "issued_to": USER["user_id"], "provider_id": "selected"})
    class Pool:
        def submit(self, fn, *args):
            queued.append((fn, args))
            future = Future()
            future.set_result(None)
            return future
    monkeypatch.setattr(a, "_first_names_pool", Pool())
    assert t.schedule_reply(wired["chat"], {"speaker": "ai", "body": "done", "source_run_id": "run"})
    assert wired["calls"] == 0
    fn, args = queued[0]
    assert args == (wired["chat"], USER["user_id"], "selected")
    fn(*args)
    assert wired["calls"] == 1 and titles(wired) == (TITLE, TITLE, TITLE)
    assert not t.schedule_reply("other", {"speaker": "ai", "body": "done", "source_run_id": "run"})


def test_composition_registers_and_removes_full_run_port(monkeypatch):
    from flowgate.conversation import integration
    integration.register()
    assert first_names_hooks._handler is t.schedule_run
    integration.unregister()
    assert first_names_hooks._handler is None
