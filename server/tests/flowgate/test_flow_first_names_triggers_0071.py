"""Entry-point and refresh integration using the card 2 real database fixture."""
import pytest
from test_flow_first_names_0071 import (naming, env, db_path, legacy_cards, FA, USER, TEXT, TITLE, titles,
                                        FLOW_T, CARD_T, CHAT_T, ALL, DEFAULTS)
from flowgate.conversation import first_names_triggers as t, auto_title as a, legacy_adapter, activity
from modules.flow_gate.db import documents
from modules.flow_gate.graph_workflow.api import run_routes as routes
from starlette.requests import Request
from modules.flow_gate.graph_workflow.services import first_names_hooks
from modules.flow_gate.api.v1.events import publisher

pytestmark = pytest.mark.usefixtures("legacy_cards")
_real_schedule = t._schedule
_real_run_context = t.run_context


@pytest.fixture()
def wired(naming, monkeypatch):
    monkeypatch.setattr(activity, "readable_chat_document", lambda doc_id, user: documents.get_by_id(doc_id))
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [
        {"speaker": "user", "body": TEXT}, {"speaker": "ai", "body": "확인합니다"}])
    monkeypatch.setattr(legacy_adapter, "is_group_disposed", lambda _: False)
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: fn(*args) or True)
    return naming


@pytest.mark.parametrize("first", ["chat", "run"])
def test_both_orders_name_each_target_from_its_own_content(wired, first):
    chat = lambda: t.chat(wired["chat"], USER, {"provider_id": "provider"})
    run = lambda: t._run(FA, USER["user_id"], 1)
    (chat if first == "chat" else run)()
    (run if first == "chat" else chat)()
    if first == "chat":
        assert titles(wired) == ALL
        assert sorted(wired["kinds"]) == ["chat", "flow", "root"]
    else:
        # A card run start never calls the model: the card takes its document's first line.
        assert titles(wired) == (FLOW_T, "로그인 장애 요구사항", CHAT_T)
        assert sorted(wired["kinds"]) == ["chat", "flow"]


def test_chat_route_returns_the_chat_own_title(wired):
    result = t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert result["title"] == CHAT_T


def test_unflagged_chat_participates(wired):
    documents.update(wired["chat"], {"meta": "{}"})
    t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert titles(wired) == ALL


def test_chat_before_ai_reply_does_not_generate(wired, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [{"speaker": "user", "body": TEXT}])
    with pytest.raises(a.c.ConversationError):
        t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert wired["calls"] == 0


def test_reply_notification_is_after_commit_and_scoped(wired, monkeypatch):
    from modules.flow_gate.db.connection import in_transaction
    events = []
    monkeypatch.setattr(t, "_schedule", lambda *args: True)
    def publish(event):
        assert not in_transaction()
        assert titles(wired) == (FLOW_T, None, CHAT_T)
        assert event.payload["targets"] == ["chat", "flow"]
        events.append(event)
    monkeypatch.setattr(publisher, "broadcast_event_nowait", publish)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"}, CHAT_T,
                      wired["chat"], flow_title=FLOW_T)
    assert len(events) == 1
    assert events[0].group_id == FA


def test_chat_and_run_overlap_do_not_generate_twice(wired):
    wired["hook"] = lambda: t._run(FA, USER["user_id"], 1) if wired["calls"] == 1 else None
    t.chat(wired["chat"], USER, {"provider_id": "provider"})
    assert wired["kinds"].count("chat") == 1 and wired["kinds"].count("flow") == 1
    assert titles(wired)[0] == FLOW_T and titles(wired)[2] == CHAT_T


def test_run_without_chat_does_not_create_chat(wired, monkeypatch):
    before = documents.list_documents("p", group_id=FA, type_code="CH")
    t._run(FA, USER["user_id"], 1)
    assert titles(wired) == ("새 플로우", "로그인 장애 요구사항", "새 대화")
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
    provider, text, chat_id = _real_run_context(FA, 1)
    assert provider == "selected" and text.strip() == TEXT and chat_id is None
    root.write_text("---\ntitle: metadata only\n---\n", encoding="utf-8")
    assert not _real_run_context(FA, 1)[1].strip()


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("body", ["# Library seat reservation requirements\n\nReserve seats.", ""])
def test_chat_names_unexecuted_root_from_real_document(naming, monkeypatch, tmp_path, remote, body):
    from modules.flow_gate.graph_workflow.store import assignment_store
    from modules.flow_gate.storage import paths
    from flowgate.conversation import first_names_ports as ports
    assert not assignment_store.root_binding(FA)
    assert ports.targets(FA, None)[1]["root"]["id"] == naming["key"]
    root = tmp_path / "unexecuted-root.md"
    root.write_text("---\ntitle: metadata is not a body\n---\n" + body, encoding="utf-8")
    monkeypatch.setattr(paths, "locate_storage_path", lambda *args, **kwargs: (root, "readable"))
    monkeypatch.setattr(t, "run_context", _real_run_context)
    monkeypatch.setattr(t, "_schedule", lambda fn, *args: fn(*args) or True)
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: remote)
    assert _real_run_context(FA, 0, "provider")[1].strip() == body
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"},
                      CHAT_T, naming["chat"], flow_title=FLOW_T)
    expected = ("Library seat reservation requ…" if remote else CARD_T) if body else None
    assert titles(naming) == (FLOW_T, expected, CHAT_T)
    assert naming["calls"] == (1 if body and not remote else 0)
    assert not assignment_store.root_binding(FA)  # Naming must not confirm or execute the card.


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
    monkeypatch.setattr(t, "_schedule", _real_schedule)
    assert t.schedule_reply(wired["chat"], {"speaker": "ai", "body": "done", "source_run_id": "run"})
    assert wired["calls"] == 0
    fn, args = queued[0]
    assert args == (wired["chat"], USER["user_id"], "selected")
    fn(*args)
    assert titles(wired) == ALL
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
    assert _real_run_context(FA, 1)[0] == expected


def test_submitted_titles_apply_seeds_inline_and_queue_the_rest(wired, monkeypatch):
    from unittest.mock import Mock
    generate, schedule = Mock(), Mock()
    monkeypatch.setattr(a, "generate_flow_names", generate)
    monkeypatch.setattr(t, "_schedule", schedule)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"},
                      '  "제목: 제출된 이름."  ', wired["chat"], flow_title="플로우 이름")
    generate.assert_called_once_with(FA, {"user_id": USER["user_id"]}, "provider", "", wired["chat"],
                                     title="제출된 이름", flow_title="플로우 이름", kinds=("chat", "flow"))
    schedule.assert_called_once_with(generate, FA, {"user_id": USER["user_id"]}, "provider", "",
                                     wired["chat"], None, True, None, ("root",))


@pytest.mark.parametrize("title", [None, "   ", "제목:"])
def test_submitted_without_valid_title_queues_every_target(wired, monkeypatch, title):
    from unittest.mock import Mock
    generate, schedule = Mock(), Mock(return_value=True)
    monkeypatch.setattr(a, "generate_flow_names", generate)
    monkeypatch.setattr(t, "_schedule", schedule)
    t.submitted_title({"group_id": FA, "issued_to": USER["user_id"], "provider_id": "provider"},
                      title, wired["chat"])
    generate.assert_not_called()
    schedule.assert_called_once_with(generate, FA, {"user_id": USER["user_id"]}, "provider", "",
                                     wired["chat"], None, True, None, ("chat", "flow", "root"))


@pytest.mark.parametrize("error", [FileNotFoundError("private root path"),
                                  PermissionError("private root path"),
                                  UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private root path")])
def test_root_read_failure_is_contained_and_safe(wired, monkeypatch, caplog, error):
    from unittest.mock import Mock
    from modules.flow_gate.graph_workflow.store import definition_store
    monkeypatch.setattr(definition_store, "latest_revision_no", lambda _: 1)
    monkeypatch.setattr(t, "run_context", Mock(side_effect=error))
    result = a.generate_flow_names(FA, USER, "provider", "", wired["chat"])
    assert result["targets"]["chat"] == "done" and result["targets"]["root"] == "unchanged"
    assert any("reason=root_read_failed" in r.getMessage() for r in caplog.records)
    assert "private root path" not in caplog.text
