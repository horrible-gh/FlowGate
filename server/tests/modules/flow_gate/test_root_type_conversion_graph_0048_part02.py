"""Independently selectable scenarios; shared fixtures remain in test_root_type_conversion_graph_0048.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_root_type_conversion_graph_0048 import (
    DECIDED,
    FB,
    FC,
    FD,
    FG,
    FLOWS,
    FU,
    FV,
    FastAPI,
    NEXT,
    Path,
    START,
    TestClient,
    USER,
    _MIGRATIONS_DIR,
    _SERVER_DIR,
    _bypass,
    _make_draft,
    _make_graph,
    _seed,
    _sql,
    binding_row,
    contact_calls,
    convert,
    converted_events,
    doc_row,
    env,
    os,
    pytest,
    rel_path,
    root,
    sqlite3,
    start_node,
    sys,
    tempfile,
)

def test_unassigned_flow_round_trip_and_same_type_no_op(env):
    assert convert(env, root(FU)).status_code == 200
    back = convert(env, root(FU, "B"), "R")
    assert back.status_code == 200 and back.json()["data"]["doc_id"] == root(FU)
    same = convert(env, root(FU), "R")
    assert same.status_code == 200 and same.json()["data"]["doc_id"] == root(FU)
    assert doc_row(env, root(FU))["filename"] == "0001-R_document.md"
def test_unassigned_refusals_inside_the_guard_reach_the_caller_unchanged(env, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    db_documents.create({"doc_id": f"{FU}.0002-N", "project_id": "flowgate", "module": "v02", "group_id": FU,
                         "type_code": "N", "seq": 2, "title": "child", "status": "open", "owner_id": USER,
                         "triggered_by": root(FU), "target_id": root(FU)})
    # a root with child documents is succeeded, no longer refused.
    children = convert(env, root(FU))
    assert children.status_code == 200, children.text
    assert (children.json()["conversion"]["mode"], children.json()["conversion"]["child_count"]) == ("succession", 1)
    again = convert(env, root(FU))
    not_root = convert(env, f"{FU}.0002-N", "R")
    missing = convert(env, f"{FU}.0009-R")
    _bypass(env, monkeypatch)
    for res, old in ((again, convert(env, root(FU))), (not_root, convert(env, f"{FU}.0002-N", "R")),
                     (missing, convert(env, f"{FU}.0009-R"))):
        assert (res.status_code, res.json()) == (old.status_code, old.json())
    assert again.status_code == 409
    assert (again.json()["error"], again.json()["live_root_doc_id"]) == ("root_superseded", root(FU, "B"))
    assert not_root.status_code == 422
    assert missing.status_code == 404
    assert doc_row(env, root(FU))["type_code"] == "R"
    bad = convert(env, root(FU), "X")
    assert bad.status_code == 422                           # body validation, as before
def test_unassigned_flow_with_an_r_start_draft_converts_and_validation_reports_it(env):
    from modules.flow_gate.graph_workflow.services import graph_service
    from modules.flow_gate.graph_workflow.store import definition_store

    group = {"group_id": FD, "project_id": "flowgate"}
    version = definition_store.draft(FD)["draft_version"]
    before = graph_service.validate_draft(group, USER, version)
    assert before["confirmable"] is True
    assert before["root_binding_preview"]["doc_id"] == root(FD)

    res = convert(env, root(FD))
    assert res.status_code == 200, res.json()
    assert res.json()["data"]["doc_id"] == root(FD, "B")

    after = graph_service.validate_draft(group, USER, version)
    assert after["confirmable"] is False
    assert [i["code"] for i in after["issues"]] == ["root_binding_no_start_node"]
    assert definition_store.draft(FD)["draft_version"] == version      # the draft itself is untouched
    assert binding_row(env, FD) is None
@pytest.mark.parametrize("headers, origin", [
    ({"x-canvas-client-id": "kind_abc-1"}, "kind_abc-1"),
    ({}, None),
    ({"x-canvas-client-id": "not a client id!"}, None),
])
def test_the_draft_card_event_names_the_requesting_tab(env, headers, origin):
    from modules.flow_gate.graph_workflow.services import card_document_ports

    card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, lambda flow, old, new: [START] if flow == FD else [])
    res = env["client"].patch(f"/api/v1/documents/{root(FD)}/root-type", json={"new_type": "B"}, headers=headers)
    assert res.status_code == 200, res.json()
    assert res.json()["conversion"]["draft_node_keys"] == [START]
    events = [e.payload for e in env["events"] if e.event_type == "graph_canvas_changed"]
    assert [(p["flow_id"], p["reason"], p["origin_client_id"]) for p in events] == [(FD, "draft", origin)]
