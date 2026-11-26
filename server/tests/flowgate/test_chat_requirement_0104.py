"""F104-3: explicit requirement placement and refusal atomicity."""
import os

import pytest

from test_fg_card_settings import legacy_cards, db_path, env, decided, FA, FB, _doc, node, put_draft
from test_chat_control_v02test_0023 import granted

pytestmark = pytest.mark.usefixtures("legacy_cards")


@pytest.fixture(autouse=True)
def restore_lazy_agent_pepper():
    # Token initialization reached by these tests may lazily choose the agent pepper.
    key = "FLOWGATE_AGENT_PEPPER_ID"
    previous = os.environ.get(key)
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


def group(flow_id):
    from modules.flow_gate.graph_workflow.services.canvas_service import load_flow
    return load_flow(flow_id)


def snapshot(flow_id, env):
    from flowgate.card_documents import legacy_adapter, repository
    from modules.flow_gate.graph_workflow.store import definition_store
    from modules.flow_gate.storage.paths import get_storage_root
    root = get_storage_root("flowgate", create=True)
    return (definition_store.draft(flow_id), legacy_adapter.flow_documents(flow_id), repository.rows(flow_id),
            list(env["events"]), sorted(str(p) for p in root.rglob("*") if p.is_file()))


@pytest.mark.parametrize("flow_id", [FA, FB])
def test_place_one_requirement_reuses_or_creates_and_work_follows(env, decided, flow_id):
    from flowgate.chat_control import service
    from flowgate.card_documents import repository, legacy_adapter, lines
    from modules.flow_gate.graph_workflow.store import definition_store

    before = legacy_adapter.flow_documents(flow_id)
    result = service.place(group(flow_id), "u_kim", {"cards": [{"doc_type": "R"}]}, "ko")
    [card] = result["cards"]
    draft = definition_store.draft(flow_id)
    [stored] = draft["nodes"]
    assert (stored["node_type"], stored["doc_type"], stored["palette_item_id"], stored["label"]) == (
        "document", "R", "requirement", "요구사항")
    assert draft["edges"] == [] and result["gate"] is None and result["draft_version"] == 1
    [line] = repository.rows(flow_id)
    assert line["slot"] == "requirement" and line["doc_id"] == card["requirement"]["doc_id"]
    assert line["doc_id"] == result["root"]["doc_id"]
    docs = legacy_adapter.flow_documents(flow_id)
    assert len(docs) == 1
    if before:
        assert docs == before and not line["owned"]
    else:
        assert line["owned"] and not lines.has_body(docs[0])
    work = service.place(group(flow_id), "u_kim", {"cards": [{}]}, "ko")
    assert work["after"] == card["node_key"]
    assert (card["node_key"], work["cards"][0]["node_key"]) in [
        (e["source"], e["target"]) for e in definition_store.draft(flow_id)["edges"]]


def test_two_free_requirements_refuse_without_changes(env, decided):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    _doc(FA, 2, "R")
    before = snapshot(FA, env)
    with pytest.raises(GraphApiError) as caught:
        service.place(group(FA), "u_kim", {"cards": [{"doc_type": "R"}]})
    assert (caught.value.status, caught.value.code) == (409, "chat_cards_requirement_ambiguous")
    assert caught.value.params["doc_ids"] == [f"{FA}.0001-R", f"{FA}.0002-R"]
    assert snapshot(FA, env) == before


@pytest.mark.parametrize("doc_type", ["R", "B"])
def test_existing_start_refuses_without_changes(env, decided, doc_type):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.store import definition_store

    put_draft(env, FA, 0, [node("tmp_r", doc_type)])
    existing = definition_store.draft(FA)["nodes"][0]
    before = snapshot(FA, env)
    with pytest.raises(GraphApiError) as caught:
        service.place(group(FA), "u_kim", {"cards": [{"doc_type": "R"}]})
    assert (caught.value.status, caught.value.code) == (409, "chat_cards_start_exists")
    assert caught.value.params["cards"][0]["node_key"] == existing["node_key"]
    assert caught.value.params["cards"][0]["doc_type"] == doc_type
    assert snapshot(FA, env) == before


@pytest.mark.parametrize("body", [
    {"cards": [{"doc_type": "R"}, {}]},
    {"cards": [{"doc_type": "R"}, {"doc_type": "R"}]},
    {"cards": [{"doc_type": "R", "roles": {}}]},
    {"cards": [{"doc_type": "R", "settings": {}}]},
    {"cards": [{"doc_type": "R"}], "gate": True},
    {"cards": [{"doc_type": "R"}], "after": "gn_x"},
    {"cards": [{"doc_type": "R"}], "links": []},
    {"cards": [{"doc_type": "R"}], "join": "gn_x"},
    {"cards": [{"doc_type": "B"}]},
])
def test_invalid_shapes_never_touch_storage(monkeypatch, body):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    monkeypatch.setattr(service, "_definition", lambda *_: pytest.fail("read on invalid request"))
    with pytest.raises(GraphApiError) as caught:
        service.place({"group_id": FA}, "u_kim", body)
    assert caught.value.status == 422


@pytest.mark.parametrize("change,code", [({"kind": "read"}, "chat_edit_mode_required"),
                                       ({"perm": False}, "flow_access_denied")])
def test_api_and_cli_refuse_permission_before_placement(env, granted, monkeypatch, change, code):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from flowgate.chat_control import authz, routes, tools

    granted.update(change)
    def authorize(*args, **kwargs):
        scope, flow = authz.authorize({"action_scope": "chat"}, FA)
        return scope, flow
    monkeypatch.setattr(authz, "from_run", authorize)
    monkeypatch.setattr(authz, "caller_from_bearer", lambda *a, **kw: SimpleNamespace(
        scope=authorize()[0], group=group(FA), chat_doc_id=None))
    before = snapshot(FA, env)
    status, response = tools.place_call({"group_id": FA}, {"cards": [{"doc_type": "R"}]})
    assert status == 403 and response["error"]["code"] == code
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post(f"/api/v2/chat/flows/{FA}/cards", json={"cards": [{"doc_type": "R"}]})
    assert response.status_code == 403 and response.json()["error"]["code"] == code
    assert snapshot(FA, env) == before


def test_failure_after_document_creation_rolls_back_everything(env, decided, monkeypatch):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    def fail(*args):
        raise GraphApiError(409, "test_save_failure", {})
    monkeypatch.setattr(card_settings_ports, "cards_created", fail)
    before = snapshot(FB, env)
    with pytest.raises(GraphApiError, match="test_save_failure"):
        service.place(group(FB), "u_kim", {"cards": [{"doc_type": "R"}]})
    assert snapshot(FB, env) == before


def test_free_bug_does_not_prevent_new_requirement_document(env, decided):
    from flowgate.chat_control import service
    from flowgate.card_documents import legacy_adapter, repository
    _doc(FB, 1, "B")
    result = service.place(group(FB), "u_kim", {"cards": [{"doc_type": "R"}], "gate": False})
    assert result["gate"] is None
    assert sorted(d["type_code"] for d in legacy_adapter.flow_documents(FB)) == ["B", "R"]
    [line] = repository.rows(FB)
    assert line["doc_type"] == "R" and line["owned"]


def test_schema_exposes_requirement_without_changing_work_types():
    from flowgate.chat_control import service, tools
    assert service.WORK_TYPES == ("T", "TS", "N", "D", "P", "L", "DB")
    assert tools.PLACE_SCHEMA["properties"]["cards"]["items"]["properties"]["doc_type"]["enum"] == list(service.WORK_TYPES) + ["R"]
