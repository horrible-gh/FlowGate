"""Independently selectable scenarios; shared fixtures remain in test_graph_canvas_api_0045.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_graph_canvas_api_0045 import (
    F45,
    F48,
    F51,
    FastAPI,
    JSONResponse,
    Path,
    TestClient,
    _MIGRATIONS_DIR,
    _SERVER_DIR,
    _by_key,
    _doc,
    _get,
    _put,
    _seed_base,
    _seed_documents_flow,
    _seed_sequence_flow,
    _sql,
    _v01_fingerprint,
    db_path,
    env,
    hashlib,
    json,
    os,
    pytest,
    sqlite3,
    sys,
    tempfile,
)

def test_s1_2_sequence_projection_and_first_identity_record(env):
    from modules.flow_gate.db import workflow_sequences as ws
    from modules.flow_gate.services.work_plan_apply_service import build_workflow_tag

    seq_id, items = _seed_sequence_flow()
    before = _v01_fingerprint(env["db"])
    body = _get(env, F48, client_id="tab_7f3k2a").json()
    assert body["ownership"]["state"] == "v01" and body["ownership"]["sequence_root_doc_id"] == f"{F48}.0001-R"
    assert body["projection"] == "sequence"
    nodes = body["nodes"]
    slot_key = nodes[3]["node_key"]
    assert slot_key.startswith("slot_") and len(slot_key) == 17
    assert [n["node_key"] for n in nodes] == [
        f"doc:{F48}.0001-R", f"doc:{F48}.0002-N", f"doc:{F48}.0003-NR", slot_key, f"doc:{F48}.0004-M"]
    assert [n["origin"] for n in nodes] == ["document", "sequence_slot", "sequence_slot", "sequence_slot", "document"]
    assert [n["state"] for n in nodes] == ["in_progress", "done", "review", "draft", "done"]
    assert [(n["position"]["x"], n["position"]["y"]) for n in nodes] == [(0, 0), (260, 0), (520, 0), (780, 0), (260, 180)]
    assert nodes[3]["title"] == "정렬 기본설계" and nodes[3]["identity_source"] == "sequence_slot"
    assert nodes[3]["slot"] == {"item_id": items[5], "item_seq": 5, "order": 3, "type": "D", "label": "정렬 기본설계",
                                "derived_status": "pending", "protected": False, "source_wp_card_id": "c_k0dtcr9pq80j"}
    assert nodes[2]["slot"]["protected"] is True and nodes[2]["slot"]["derived_status"] == "in_progress"
    assert [(e["source"], e["target"], e["edge_class"]) for e in body["edges"]] == [
        (f"doc:{F48}.0001-R", f"doc:{F48}.0002-N", "execution"),
        (f"doc:{F48}.0002-N", f"doc:{F48}.0003-NR", "execution"),
        (f"doc:{F48}.0003-NR", slot_key, "execution"),
        (f"doc:{F48}.0003-NR", f"doc:{F48}.0004-M", "reference")]
    tag = build_workflow_tag(ws.get_sequence_by_id(seq_id), ws.get_sequence_items(seq_id))
    assert body["identity"] == {"sequence_tag": tag, "rematched": True, "retired_node_keys": []}
    assert [(e.event_type, e.payload["reason"], e.payload["origin_client_id"]) for e in env["events"]] == [
        ("graph_canvas_changed", "identity_rematch", "tab_7f3k2a")]
    assert env["events"][0].group_id == F48 and env["events"][0].project == "flowgate"
    again = _get(env, F48).json()
    assert again["nodes"][3]["node_key"] == slot_key and again["identity"]["rematched"] is False
    assert len(env["events"]) == 1
    assert _v01_fingerprint(env["db"]) == before
def test_s2_1_positions_are_shared_and_viewport_is_per_user(env):
    _seed_documents_flow()
    res = _put(env, F51, {
        "positions": [{"node_key": f"doc:{F51}.0002-N", "x": 320, "y": 60},
                      {"node_key": f"doc:{F51}.0006-D", "x": 600, "y": 60}],
        "viewport": {"x": -120, "y": 0, "zoom": 0.7},
    }, client_id="tab_9c1m4e")
    assert res.status_code == 200, res.text
    body = res.json()
    assert {k: body[k] for k in ("flow_id", "saved_positions", "viewport_saved", "ignored")} == {
        "flow_id": F51, "saved_positions": 2, "viewport_saved": True, "ignored": []}
    ev = env["events"][-1]
    assert ev.payload == {"flow_id": F51, "reason": "layout", "actor_user_id": "u_kim", "origin_client_id": "tab_9c1m4e",
                          "draft_version": None, "revision_no": None,
                          "node_keys": [f"doc:{F51}.0002-N", f"doc:{F51}.0006-D"]}
    mine = _by_key(_get(env, F51).json())
    assert mine[f"doc:{F51}.0002-N"]["position"] == {"x": 320.0, "y": 60.0, "source": "saved"}
    assert mine[f"doc:{F51}.0001-R"]["position"]["source"] == "auto"
    assert _get(env, F51).json()["viewport"] == {"x": -120.0, "y": 0.0, "zoom": 0.7}
    other = _get(env, F51, user="u_lee").json()
    assert other["viewport"] is None
    assert _by_key(other)[f"doc:{F51}.0006-D"]["position"] == {"x": 600.0, "y": 60.0, "source": "saved"}
    # Viewport only: saved, but no event (a personal value, P0008 §8.3).
    count = len(env["events"])
    assert _put(env, F51, {"viewport": {"x": 1, "y": 2, "zoom": 1.5}}).json()["viewport_saved"] is True
    assert len(env["events"]) == count
def test_s2_3_shape_errors(env):
    _seed_documents_flow()
    res = _put(env, F51, {"positions": [], "viewport": None})
    assert res.status_code == 422
    assert res.json()["error"]["message"] == "저장할 좌표나 화면 위치가 없습니다."
    assert res.json()["error"]["params"] == {"errors": [{"field": "positions", "reason": "empty_with_null_viewport"}]}
    res = _put(env, F51, {"positions": [{"node_key": f"doc:{F51}.0002-N", "x": "320", "y": 60}],
                          "viewport": {"x": 0, "y": 0, "zoom": 0}})
    assert res.status_code == 422 and res.json()["error"]["params"]["errors"] == [
        {"field": "positions[0].x", "reason": "not_a_number"}, {"field": "viewport.zoom", "reason": "not_positive"}]
    assert _sql(env["db"], "SELECT * FROM graph_node_positions") == []
def test_s2_4_rematch_after_v01_edit_keeps_positions(env):
    from modules.flow_gate.db import workflow_sequences as ws

    seq_id, items = _seed_sequence_flow()
    slot_key = _get(env, F48).json()["nodes"][3]["node_key"]
    assert _put(env, F48, {"positions": [{"node_key": slot_key, "x": 1040, "y": 0}]}).json()["saved_positions"] == 1
    # v0.1 screen edit: a P row inserted before D; pending rows are re-issued (new ids / item_seq).
    _sql(env["db"], "DELETE FROM workflow_sequence_items WHERE id = ?", (items[5],))
    ws.insert_sequence_item(seq_id, 6, "P", "정렬 API 프로토콜", "R", 2)
    ws.insert_sequence_item(seq_id, 7, "D", "정렬 기본설계", "R", 3, source_wp_card_id="c_k0dtcr9pq80j")
    body = _get(env, F48, client_id="tab_7f3k2a").json()
    nodes = body["nodes"]
    assert nodes[4]["node_key"] == slot_key and nodes[4]["position"] == {"x": 1040.0, "y": 0.0, "source": "saved"}
    assert nodes[3]["node_key"].startswith("slot_") and nodes[3]["node_key"] != slot_key
    assert nodes[3]["position"] == {"x": 780, "y": 0, "source": "auto"} and nodes[3]["slot"]["order"] == 3
    assert body["identity"]["rematched"] is True and body["identity"]["retired_node_keys"] == []
    assert body["edges"][2]["target"] == nodes[3]["node_key"] and body["edges"][3]["source"] == nodes[3]["node_key"]
