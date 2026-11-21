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

def test_s2_2_retired_and_unknown_keys_are_ignored(env):
    seq_id, items = _seed_sequence_flow()
    old_slot = _get(env, F48).json()["nodes"][3]["node_key"]
    _sql(env["db"], "UPDATE workflow_sequence_items SET label = '바뀐 설계', type = 'P' WHERE id = ?", (items[5],))
    body = _get(env, F48).json()
    new_slot = body["nodes"][3]["node_key"]
    assert new_slot != old_slot and body["identity"]["retired_node_keys"] == [old_slot]
    res = _put(env, F48, {"positions": [{"node_key": new_slot, "x": 1100, "y": 20},
                                        {"node_key": old_slot, "x": 1300, "y": 20},
                                        {"node_key": "tmp_9", "x": 50, "y": 50}], "viewport": None})
    assert res.status_code == 200
    assert res.json()["saved_positions"] == 1 and res.json()["viewport_saved"] is False
    assert res.json()["ignored"] == [{"node_key": old_slot, "reason": "retired"},
                                     {"node_key": "tmp_9", "reason": "unknown_node_key"}]
def test_started_slot_moves_its_position_to_the_result_document(env):
    from modules.flow_gate.db import workflow_sequences as ws

    _seq_id, items = _seed_sequence_flow()
    slot_key = _get(env, F48).json()["nodes"][3]["node_key"]
    _put(env, F48, {"positions": [{"node_key": slot_key, "x": 900, "y": 70}]})
    d = _doc(F48, 5, "D", "정렬 기본설계 문서", "pending_review")
    ws.set_item_result_doc_id(items[5], d)
    body = _get(env, F48).json()
    node = body["nodes"][3]
    assert node["node_key"] == f"doc:{d}" and node["identity_source"] == "document"
    assert node["position"] == {"x": 900.0, "y": 70.0, "source": "saved"} and node["state"] == "review"
    assert body["identity"]["retired_node_keys"] == [slot_key]
    assert _sql(env["db"], "SELECT reason FROM graph_retired_node_keys WHERE node_key = ?", (slot_key,)) == [{"reason": "rematch"}]
    assert all(n["node_key"] != f"doc:{d}" or n["origin"] == "sequence_slot" for n in body["nodes"])
def test_superseded_document_hands_its_position_to_the_replacement(env):
    from modules.flow_gate.db import documents as db_documents

    _seed_documents_flow()
    _put(env, F51, {"positions": [{"node_key": f"doc:{F51}.0006-D", "x": 610, "y": 330}]})
    new = _doc(F51, 8, "D", "기본설계 3판", "pending_review")
    db_documents.update(f"{F51}.0006-D", {"superseded_by": new})
    body = _get(env, F51).json()
    assert _by_key(body)[f"doc:{new}"]["position"] == {"x": 610.0, "y": 330.0, "source": "saved"}
    assert body["identity"]["rematched"] is True and body["identity"]["retired_node_keys"] == [f"doc:{F51}.0006-D"]
    assert _get(env, F51).json()["identity"]["rematched"] is False
def test_disposed_flow_rejects_layout_but_reads(env):
    _seed_documents_flow()
    _doc(F51, 9, "DC", "폐기")
    res = _put(env, F51, {"viewport": {"x": 0, "y": 0, "zoom": 1}})
    assert res.status_code == 409 and res.json()["error"]["code"] == "flow_disposed"
    assert _get(env, F51).status_code == 200
def test_flow_and_user_delete_clean_graph_rows_with_foreign_keys_off(env):
    _seed_documents_flow()
    seq_id, _ = _seed_sequence_flow()
    for flow, key in ((F51, f"doc:{F51}.0001-R"), (F48, f"doc:{F48}.0001-R")):
        _get(env, flow)
        assert _put(env, flow, {"positions": [{"node_key": key, "x": 1, "y": 1}],
                                "viewport": {"x": 0, "y": 0, "zoom": 1}}).status_code == 200
    _sql(env["db"], "INSERT INTO graph_retired_node_keys VALUES (?, 'slot_x', 'rematch', 't')", (F48,))
    tables = ("graph_flow_guards", "graph_node_positions", "graph_viewports", "graph_identity_records",
              "graph_identity_rows", "graph_retired_node_keys")
    assert all(_sql(env["db"], f"SELECT 1 FROM {t} WHERE group_id = ?", (F48,)) for t in tables)
    conn = sqlite3.connect(env["db"])
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        conn.execute("DELETE FROM groups WHERE group_id = ?", (F48,))
        conn.commit()
        for t in tables:
            assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE group_id = ?", (F48,)).fetchone()[0] == 0, t
        assert conn.execute("SELECT COUNT(*) FROM graph_node_positions WHERE group_id = ?", (F51,)).fetchone()[0] == 1
        conn.execute("DELETE FROM users WHERE user_id = 'u_kim'")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM graph_viewports").fetchone()[0] == 0
    finally:
        conn.close()
