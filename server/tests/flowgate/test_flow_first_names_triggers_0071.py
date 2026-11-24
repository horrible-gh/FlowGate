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
def test_both_orders_name_chat_from_its_own_message(wired, first):
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


def test_reply_notification_is_after_commit_and_scoped(wired, monkeypatch):
    from modules.flow_gate.db.connection import in_transaction
    events = []
    def publish(event):
        assert not in_transaction()
        assert titles(wired) == (TITLE, TITLE, TITLE)
        assert event.payload["targets"] == ["flow", "card", "chat"]
        events.append(event)
    monkeypatch.setattr(publisher, "broadcast_event_nowait", publish)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"}, TITLE, wired["chat"])
    assert len(events) == 1
    assert events[0].group_id == FA


def test_chat_and_run_overlap_share_one_claim(wired):
    wired["hook"] = lambda: t._run(FA, USER["user_id"], 1)
    t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert wired["calls"] == 1 and titles(wired) == (TITLE, TITLE, TITLE)


def test_run_without_chat_does_not_create_chat(wired, monkeypatch):
    monkeypatch.setattr(t, "run_context", lambda *args: ("provider", TEXT, None))
    before = documents.list_documents("p", group_id=FA, type_code="CH")
    t._run(FA, USER["user_id"], 1)
    assert titles(wired) == ("새 플로우", None, "새 대화")
    assert wired["calls"] == 0
    assert documents.list_documents("p", group_id=FA, type_code="CH") == before


@pytest.mark.parametrize("mode", ["full", "step"])
def test_route_schedules_after_successful_full_or_step_run(monkeypatch, mode):
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
    assert order == ["committed", (FA, "u", 7)]


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
    assert provider == "selected" and text.strip() == TEXT and chat_id is None
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


@pytest.mark.parametrize("role_state, expected", [("assigned", "role-provider"), ("not_used", None), ("unavailable", None)])
def test_run_context_uses_current_execution_role(naming, monkeypatch, role_state, expected):
    from flowgate.ai_roles import resolve, availability, account, role_store
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.graph_workflow.store import definition_store, assignment_store
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    from types import SimpleNamespace
    node = {"node_key": "role-card", "node_type": "document", "doc_type": "T",
            "assignee_provider_id": "stale-legacy"}
    monkeypatch.setattr(definition_store, "revision", lambda *args: {"nodes": [node]})
    monkeypatch.setattr(assignment_store, "root_binding", lambda _: {})
    monkeypatch.setattr(sa, "enabled", lambda: True)
    monkeypatch.setattr(card_settings_ports, "uses_ai", lambda *args: True)
    monkeypatch.setattr(card_settings_ports, "run_settings", lambda *args: {"provider_id": "stale-legacy"})
    value = role_store.RoleValue("not_used") if role_state == "not_used" else role_store.RoleValue("assigned", "role-provider")
    inputs = resolve.CardRoleInputs(slots={"execution": SimpleNamespace(value=value, source="card_explicit")},
                                    init=SimpleNamespace(state="initialized"), node=node)
    monkeypatch.setattr(resolve, "read_card_role_inputs", lambda *args: inputs)
    monkeypatch.setattr(availability, "check_availability", lambda *args, **kwargs: "disabled" if role_state == "unavailable" else None)
    assert t.run_context(FA, 1)[0] == expected



def test_submitted_title_skips_context_reads(wired, monkeypatch):
    from unittest.mock import Mock
    first = Mock(side_effect=AssertionError("unexpected first message read"))
    root = Mock(side_effect=FileNotFoundError("unreadable root"))
    generate = Mock()
    schedule = Mock()
    monkeypatch.setattr(a, "_first_user_message", first)
    monkeypatch.setattr(t, "run_context", root)
    monkeypatch.setattr(a, "generate_flow_names", generate)
    monkeypatch.setattr(t, "_schedule", schedule)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"},
                      '  "제목: 제출된 이름."  ', wired["chat"])
    generate.assert_called_once_with(FA, {"user_id": USER["user_id"]}, "provider", "",
                                     wired["chat"], title="제출된 이름")
    first.assert_not_called()
    root.assert_not_called()
    schedule.assert_not_called()


def test_submitted_title_saves_without_root_or_message(wired, monkeypatch):
    from unittest.mock import Mock
    root = Mock(side_effect=FileNotFoundError("unreadable root"))
    monkeypatch.setattr(t, "run_context", root)
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [])
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"},
                      TITLE, wired["chat"])
    assert titles(wired) == (TITLE, TITLE, TITLE)
    assert wired["calls"] == 0
    root.assert_not_called()


@pytest.mark.parametrize("title", [None, "   ", "제목:"])
@pytest.mark.parametrize("error", [FileNotFoundError("private root path"),
                                  PermissionError("private root path"),
                                  UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private root path")])
def test_submitted_title_root_failure_still_schedules(wired, monkeypatch, caplog, title, error):
    from unittest.mock import Mock
    from modules.flow_gate.graph_workflow.store import definition_store
    monkeypatch.setattr(a, "_first_user_message", lambda _: "")
    monkeypatch.setattr(definition_store, "latest_revision_no", lambda _: 1)
    root = Mock(side_effect=error)
    schedule = Mock(return_value=True)
    monkeypatch.setattr(t, "run_context", root)
    monkeypatch.setattr(t, "_schedule", schedule)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"},
                      title, wired["chat"])
    root.assert_called_once_with(FA, 1, "provider")
    schedule.assert_called_once_with(a.generate_flow_names, FA, {"user_id": USER["user_id"]},
                                     "provider", "", wired["chat"])
    warnings = [r.getMessage() for r in caplog.records if "reason=root_read_failed" in r.getMessage()]
    assert warnings == [f"flow_auto_title reason=root_read_failed flow={FA}"]
    assert "private root path" not in caplog.text


@pytest.mark.parametrize("first_message", [TEXT, ""])
def test_submitted_title_fallback_preserves_context_order(wired, monkeypatch, first_message):
    from unittest.mock import Mock
    from modules.flow_gate.graph_workflow.store import definition_store
    order = []
    def first(_):
        order.append("first")
        return first_message
    def root(*args):
        order.append("root")
        return "provider", TEXT, None
    monkeypatch.setattr(a, "_first_user_message", first)
    monkeypatch.setattr(t, "run_context", root)
    monkeypatch.setattr(definition_store, "latest_revision_no", lambda _: 1)
    schedule = Mock(return_value=True)
    monkeypatch.setattr(t, "_schedule", schedule)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"},
                      None, wired["chat"])
    assert order == (["first"] if first_message else ["first", "root"])
    schedule.assert_called_once_with(a.generate_flow_names, FA, {"user_id": USER["user_id"]},
                                     "provider", TEXT, wired["chat"])
