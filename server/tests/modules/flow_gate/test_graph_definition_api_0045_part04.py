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

def test_s4_6_confirmation_loses_to_an_earlier_v01_decide(env):
    from modules.flow_gate.db import workflow_sequences as ws

    linear_draft(env)
    ws.insert_sequence(f"{F51}.0001-R")
    res = confirm(env, F51, 1)
    assert res.status_code == 409
    assert res.json()["error"]["params"] == {"flow_id": F51, "ownership": "v01", "sequence_root_doc_id": f"{F51}.0001-R"}
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_revisions")[0]["n"] == 0
def test_s4_7_base_revision_and_conflict_and_order(env):
    linear_draft(env)
    assert confirm(env, F51, 1, base=3).json()["error"]["code"] == "revision_conflict"
    confirm(env, F51, 1)
    again = confirm(env, F51, 1)                            # same draft confirmed twice
    assert again.status_code == 409 and again.json()["error"]["code"] == "revision_conflict"
    assert again.json()["error"]["params"] == {"flow_id": F51, "base_revision_no": None, "latest_revision_no": 1}
    assert confirm(env, F51, 1, base=1).json()["error"]["code"] == "draft_not_found"
    from modules.flow_gate.db import workflow_sequences as ws
    ws.insert_sequence(f"{F51}.0001-R")                     # abnormal: both records -> conflict
    res = confirm(env, F51, 1, base=1)
    assert res.json()["error"]["code"] == "flow_ownership_conflict"
    assert res.json()["error"]["params"]["graph_revision_no"] == 1
    conflict = canvas(env, F51).json()
    assert conflict["ownership"]["state"] == "conflict" and conflict["projection"] == "graph"
    assert conflict["ownership"]["sequence_root_doc_id"] == f"{F51}.0001-R"
    assert all(n["editable"]["structure"] is False for n in conflict["nodes"])
    assert put_draft(env, F51, 0, [], []).json()["error"]["code"] == "flow_ownership_conflict"
def test_bound_start_node_removed_from_next_draft_is_refused(env):
    km = linear_draft(env)
    confirm(env, F51, 1)
    put_draft(env, F51, 0, [node(km["tmp_d"], "D")], [])
    res = confirm(env, F51, 1, base=1)
    assert res.status_code == 422
    [issue] = res.json()["error"]["params"]["issues"]
    assert issue["code"] == "root_binding_start_removed" and issue["removed_node_key"] == km["tmp_r"]
    assert issue["node_keys"] == []
def test_draft_view_404_and_validation_body_checks(env):
    linear_draft(env)
    confirm(env, F51, 1)
    res = canvas(env, F51, "?view=draft")
    assert res.status_code == 404 and res.json()["error"]["code"] == "draft_not_found"
    bad = env["client"].post(f"/api/v1/flows/{F51}/graph/revisions", json={"draft_version": 0}, headers=_h())
    assert bad.status_code == 422 and bad.json()["error"]["params"]["errors"][0]["field"] == "draft_version"
def test_migrations_create_tables_and_sqlite_triggers(env):
    tables = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(GRAPH_TABLES) <= tables
    triggers = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert {"trg_graph_definitions_group_deleted", "trg_graph_assignment_group_deleted"} <= triggers
def test_group_delete_with_foreign_keys_off_leaves_no_graph_rows(env):
    linear_draft(env)
    confirm(env, F51, 1)
    put_draft(env, F51, 0, [node("tmp_z", "R")], [])
    put_draft(env, F55, 0, [node("tmp_1", "N")], [])
    conn = sqlite3.connect(env["db"])
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        conn.execute("DELETE FROM groups WHERE group_id = ?", (F51,))
        conn.commit()
    finally:
        conn.close()
    for table in GRAPH_TABLES:
        assert _sql(env["db"], f"SELECT COUNT(*) AS n FROM {table} WHERE group_id = ?", (F51,))[0]["n"] == 0, table
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_drafts WHERE group_id = ?", (F55,))[0]["n"] == 1
