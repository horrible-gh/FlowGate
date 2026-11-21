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

def test_a_conversion_without_a_draft_card_change_sends_no_canvas_event(env):
    res = env["client"].patch(f"/api/v1/documents/{root(FU)}/root-type", json={"new_type": "B"},
                              headers={"x-canvas-client-id": "kind_abc-2"})
    assert res.status_code == 200, res.json()
    assert [e for e in env["events"] if e.event_type == "graph_canvas_changed"] == []
def test_v01_flow_keeps_the_converter_refusal(env, monkeypatch):
    res = convert(env, root(FV))
    assert (res.status_code, res.json()) == (409, {"detail": DECIDED})
    _bypass(env, monkeypatch)
    old = convert(env, root(FV))
    assert (old.status_code, old.json()) == (res.status_code, res.json())
    assert doc_row(env, root(FV))["type_code"] == "R"
@pytest.mark.parametrize("flow", [FG, FU])
def test_disposed_group_is_refused_first(env, contact_calls, flow):
    from modules.flow_gate.db import documents as db_documents

    db_documents.create({"doc_id": f"{flow}.0009-DC", "project_id": "flowgate", "module": "v02", "group_id": flow,
                         "type_code": "DC", "seq": 9, "title": "폐기", "status": "closed", "owner_id": USER})
    res = convert(env, root(flow))
    assert (res.status_code, res.json()) == (409, {"detail": "Modification not allowed: the group has been disposed."})
    assert contact_calls == []
    assert doc_row(env, root(flow))["type_code"] == "R"
@pytest.mark.parametrize("flow", [FG, FU])
def test_ai_run_in_progress_is_refused_first(env, contact_calls, monkeypatch, flow):
    from modules.flow_gate.services import mutation_policy

    lease = {"group_id": flow, "run_id": "run_live_1", "token_id": "tok_1", "action_scope": "edit"}
    monkeypatch.setattr(mutation_policy.db_leases, "get_active", lambda group_id: lease if group_id == flow else None)
    monkeypatch.setattr(mutation_policy, "_lease_run_live", lambda run_id: True)
    res = convert(env, root(flow))
    assert res.status_code == 423
    assert res.json()["error"]["code"] == "GROUP_AI_RUN_LOCKED"
    assert contact_calls == []
    assert doc_row(env, root(flow))["type_code"] == "R"
def test_every_conversion_reaches_the_contact_once(env, contact_calls):
    for flow in (FU, FD, FG, FC, FV):
        convert(env, root(flow))
    assert contact_calls == [root(FU), root(FD), root(FG), root(FC), root(FV)]
