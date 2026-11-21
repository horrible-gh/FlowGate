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

def test_s4_1_first_confirmation_assigns_and_binds_root(env):
    km = linear_draft(env)
    env["events"].clear()
    res = confirm(env, F51, 1)
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["revision_no"] == 1 and body["confirmed_by"] == "u_kim"
    assert body["ownership"]["state"] == "graph" and body["ownership"]["graph_revision_no"] == 1
    assert body["ownership"]["assigned_at"]
    assert body["root_binding"] == {"status": "bound", "node_key": km["tmp_r"], "doc_id": f"{F51}.0001-R",
                                    "bound_at_revision_no": 1}
    assert body["retired_node_keys"] == [f"doc:{F51}.0001-R"]
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_drafts")[0]["n"] == 0
    assert _sql(env["db"], "SELECT assigned_revision_no FROM graph_assignments") == [{"assigned_revision_no": 1}]
    assert _sql(env["db"], "SELECT doc_type FROM graph_root_bindings") == [{"doc_type": "R"}]
    [own] = events(env, "flow_ownership_changed")
    assert own["from"] == "unassigned" and own["to"] == "graph" and own["revision_no"] == 1
    assert own["root_binding"] == {"node_key": km["tmp_r"], "doc_id": f"{F51}.0001-R"}
    assert own["origin_client_id"] == "tab_a"
    [changed] = events(env, "graph_canvas_changed")
    assert changed["reason"] == "revision" and changed["revision_no"] == 1

    view = canvas(env, F51).json()
    assert view["ownership"]["state"] == "graph" and view["projection"] == "graph"
    assert view["view"] == {"mode": "revision", "revision_no": 1, "draft_version": None}
    assert view["graph"] == {"draft": None, "latest_revision_no": 1, "root_binding": {
        "node_key": km["tmp_r"], "doc_id": f"{F51}.0001-R", "bound_at_revision_no": 1}}
    by_key = {n["node_key"]: n for n in view["nodes"]}
    assert f"doc:{F51}.0001-R" not in by_key
    start = by_key[km["tmp_r"]]
    assert start["origin"] == "graph_node" and start["state"] == "done" and start["title"] == "Canvas 편집 저장"
    assert start["document"]["doc_id"] == f"{F51}.0001-R" and start["graph_node"]["root_bound"] is True
    assert start["editable"] == {"move": True, "structure": False}
    assert {f"doc:{F51}.0002-N", f"doc:{F51}.0003-M"} <= set(by_key)
    refs = [e for e in view["edges"] if e["edge_class"] == "reference"]
    assert {(e["source"], e["target"]) for e in refs} == {(km["tmp_r"], f"doc:{F51}.0002-N"),
                                                          (km["tmp_r"], f"doc:{F51}.0003-M")}
    # the absorbed document key is retired for layout saves
    res = env["client"].put(f"/api/v1/flows/{F51}/canvas/layout",
                            json={"positions": [{"node_key": f"doc:{F51}.0001-R", "x": 1, "y": 1}], "viewport": None},
                            headers=_h())
    assert res.json()["ignored"] == [{"node_key": f"doc:{F51}.0001-R", "reason": "retired"}]
def test_s4_2_flow_without_root_confirms_without_binding(env):
    put_draft(env, F55, 0, [node("tmp_1", "N", "조사")], [])
    body = confirm(env, F55, 1).json()
    assert body["root_binding"] == {"status": "not_needed", "node_key": None, "doc_id": None, "bound_at_revision_no": None}
    assert body["retired_node_keys"] == []
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_root_bindings")[0]["n"] == 0
def test_s4_3_next_revision_inherits_the_binding_and_retires_removed_nodes(env):
    km = linear_draft(env)
    confirm(env, F51, 1)
    graph_view = env["client"].get(f"/api/v1/flows/{F51}/graph", headers=_h()).json()
    rev = graph_view["latest_revision"]
    assert rev["revision_no"] == 1 and len(rev["nodes"]) == 3
    copy = [node(n["node_key"], n["doc_type"], n["label"]) for n in rev["nodes"][:2]]
    res = put_draft(env, F51, 0, copy, [edge(km["tmp_e1"], km["tmp_r"], km["tmp_d"])])
    assert res.status_code == 200
    head = env["client"].get(f"/api/v1/flows/{F51}/graph", headers=_h()).json()["draft"]
    assert head["base_revision_no"] == 1
    assert canvas(env, F51).json()["view"]["mode"] == "revision"
    draft_view = canvas(env, F51, "?view=draft").json()
    assert draft_view["view"] == {"mode": "draft", "revision_no": None, "draft_version": 1}
    assert {n["origin"] for n in draft_view["nodes"] if n["identity_source"] == "graph_node"} == {"draft_node"}
    body = confirm(env, F51, 1, base=1).json()
    assert body["revision_no"] == 2
    assert body["root_binding"] == {"status": "inherited", "node_key": km["tmp_r"], "doc_id": f"{F51}.0001-R",
                                    "bound_at_revision_no": 1}
    retired = _sql(env["db"], "SELECT reason FROM graph_retired_node_keys WHERE node_key = ?", (km["tmp_t"],))
    assert retired == [{"reason": "revision_removed"}]
    assert len(events(env, "flow_ownership_changed")) == 1        # only the first confirmation
    one = env["client"].get(f"/api/v1/flows/{F51}/graph/revisions/1", headers=_h()).json()["revision"]
    assert len(one["nodes"]) == 3 and one["root_binding"]["node_key"] == km["tmp_r"]
    missing = env["client"].get(f"/api/v1/flows/{F51}/graph/revisions/9", headers=_h())
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "revision_not_found"
def test_s4_4_structure_failure_writes_nothing(env):
    put_draft(env, F51, 0, [node("tmp_1", "R"), node("tmp_2", "D"), node("tmp_3", "T")],
              [edge("tmp_e1", "tmp_1", "tmp_2"), edge("tmp_e2", "tmp_1", "tmp_3")])
    res = confirm(env, F51, 1)
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "graph_validation_failed" and err["params"]["draft_version"] == 1
    assert [i["structure"] for i in err["params"]["issues"]] == ["branch"]
    assert "1" in err["message"] and err["params"]["issues"][0]["message"]
    for table in GRAPH_TABLES[3:]:
        assert _sql(env["db"], f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0, table
    assert canvas(env, F51).json()["ownership"]["state"] == "unassigned"
def test_s4_5_two_live_roots_block_the_binding(env):
    put_draft(env, F31, 0, [node("tmp_1", "R")], [])
    res = confirm(env, F31, 1)
    assert res.status_code == 422
    [issue] = res.json()["error"]["params"]["issues"]
    assert issue["code"] == "root_binding_ambiguous_root" and issue["rule_ref"] is None
    assert issue["node_keys"] == [f"doc:{F31}.0001-R", f"doc:{F31}.0004-R"]
    assert "2" in issue["message"] and not any(k.startswith("_") for k in issue)
