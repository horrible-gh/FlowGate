"""Independently selectable scenarios; shared fixtures remain in test_graph_definition_api_0045.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_graph_definition_api_0045 import (
    F31,
    F48,
    F51,
    F55,
    FastAPI,
    GRAPH_TABLES,
    JSONResponse,
    Path,
    TestClient,
    _MIGRATIONS_DIR,
    _SERVER_DIR,
    _doc,
    _h,
    _seed,
    _sql,
    canvas,
    confirm,
    db_path,
    edge,
    env,
    events,
    linear_draft,
    node,
    os,
    put_draft,
    pytest,
    registered_card_ports,
    sqlite3,
    sys,
    tempfile,
    validate,
)

def test_draft_write_needs_edit_permission_and_a_user_token(env):
    env["denied"].add(("u_kim", "perm_document_update"))
    assert put_draft(env, F51, 0, [], []).json()["error"]["code"] == "flow_access_denied"
    worker = env["client"].put(f"/api/v1/flows/{F51}/graph/draft", json={"draft_version": 0, "nodes": [], "edges": []},
                               headers={"Authorization": "Bearer worker"})
    assert worker.status_code == 403 and worker.json()["error"]["code"] == "token_kind_not_allowed"
def test_s3_5_validation_reports_undecided_rules_in_order(env):
    res = put_draft(env, F51, 0, [
        node("tmp_1", "R", "요구 시작"), node("tmp_2", "D"), node("tmp_3", "T"), node("tmp_4", "T"),
        node("tmp_5", None, "확인", node_type="gate", palette="confirm"),
    ], [edge("tmp_e1", "tmp_1", "tmp_2"), edge("tmp_e2", "tmp_2", "tmp_3"), edge("tmp_e3", "tmp_2", "tmp_4"),
        edge("tmp_e4", "tmp_3", "tmp_5"), edge("tmp_e5", "tmp_4", "tmp_5")])
    km = res.json()["key_map"]
    body = validate(env, F51, 1).json()
    assert body["confirmable"] is False
    # L0009 §2.5.2: by the smallest node key, then code rank (join before gate_node).
    rank = {"branch": 0, "join": 1, "gate_node": 2}
    expected = sorted([("branch", "Q-2", [km["tmp_2"]]), ("join", "Q-4", [km["tmp_5"]]),
                       ("gate_node", "Q-11", [km["tmp_5"]])], key=lambda i: (i[2][0], rank[i[0]]))
    assert [(i["structure"], i["rule_ref"], i["node_keys"]) for i in body["issues"]] == expected
    assert body["issues"][[i["structure"] for i in body["issues"]].index("branch")]["edge_ids"] == sorted(
        [km["tmp_e2"], km["tmp_e3"]])
    assert {i["code"] for i in body["issues"]} == {"rule_undecided"}
    assert body["issues"][0]["message"]
    assert body["root_binding_preview"] == {"status": "bindable", "node_key": km["tmp_1"],
                                            "doc_id": f"{F51}.0001-R", "reason": None}
    en = env["client"].post(f"/api/v1/flows/{F51}/graph/draft/validate", json={"draft_version": 1},
                            headers={**_h(), "x-locale": "en"}).json()
    assert all(i["message"].isascii() for i in en["issues"])
def test_s3_6_linear_draft_is_confirmable_and_validation_writes_nothing(env):
    linear_draft(env)
    before = _sql(env["db"], "SELECT draft_version FROM graph_drafts")
    body = validate(env, F51, 1).json()
    assert body["confirmable"] is True and body["issues"] == []
    assert _sql(env["db"], "SELECT draft_version FROM graph_drafts") == before
    assert validate(env, F51, 7).json()["error"]["code"] == "draft_version_conflict"
    assert validate(env, F55, 1).json()["error"]["code"] == "draft_not_found"
def test_s1_4_unassigned_canvas_shows_documents_and_editable_draft(env):
    km = linear_draft(env)
    body = canvas(env, F51, "?view=revision").json()       # view ignored on an unassigned Flow
    assert body["projection"] == "documents"
    assert body["view"] == {"mode": "draft", "revision_no": None, "draft_version": 1}
    assert body["graph"]["draft"]["confirmable"] is True and body["graph"]["latest_revision_no"] is None
    by_key = {n["node_key"]: n for n in body["nodes"]}
    assert f"doc:{F51}.0001-R" not in by_key
    draft_node = by_key[km["tmp_r"]]
    assert [(n["node_key"], line["slot"]) for n in body["nodes"]
            for line in n.get("card_documents", []) if line.get("doc_id") == f"{F51}.0001-R"] == [
        (km["tmp_r"], "requirement")]
    assert draft_node["origin"] == "draft_node" and draft_node["identity_source"] == "graph_node"
    assert draft_node["editable"] == {"move": True, "structure": True} and draft_node["state"] == "draft"
    assert draft_node["graph_node"]["root_bound"] is False and draft_node["kind"] == "requirement"
    assert draft_node["position"] == {"x": 0, "y": 320, "source": "auto"}
    assert by_key[km["tmp_d"]]["position"]["x"] == 260
    draft_edges = [e for e in body["edges"] if e["origin"] == "draft"]
    assert {(e["source"], e["target"], e["editable"]) for e in draft_edges} == {
        (km["tmp_r"], km["tmp_d"], True), (km["tmp_d"], km["tmp_t"], True)}
    # a draft node is a layout key
    res = env["client"].put(f"/api/v1/flows/{F51}/canvas/layout",
                            json={"positions": [{"node_key": km["tmp_d"], "x": 560, "y": 320}], "viewport": None},
                            headers=_h())
    assert res.json()["saved_positions"] == 1
def test_v01_canvas_reports_a_leftover_draft_as_not_confirmable(env):
    from modules.flow_gate.db import workflow_sequences as ws

    linear_draft(env)
    ws.insert_sequence(f"{F51}.0001-R")                     # v0.1 decide won meanwhile
    body = canvas(env, F51).json()
    assert body["ownership"]["state"] == "v01" and body["projection"] == "sequence"
    assert body["graph"]["draft"]["confirmable"] is False
    assert body["graph"]["draft"]["blocked_reason"] == "flow_owned_by_v01"
    assert all(n["origin"] != "draft_node" for n in body["nodes"])
