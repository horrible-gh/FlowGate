"""Independently selectable scenarios; shared fixtures remain in test_graph_run_api_0045.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_graph_run_api_0045 import (
    FG,
    FH,
    FU,
    FV,
    FastAPI,
    JSONResponse,
    Path,
    RUN_TABLES,
    TEST_VALUE,
    TEST_VERSION,
    TestClient,
    _MIGRATIONS_DIR,
    _POLICY,
    _SERVER_DIR,
    _TestPolicy,
    _confirm,
    _confirm_next,
    _doc,
    _edge,
    _fake_ai_invoke,
    _h,
    _node,
    _seed,
    _sql,
    control,
    count,
    current,
    db_path,
    decide_review,
    decided,
    env,
    hashlib,
    json,
    nodes_of,
    os,
    pytest,
    run_events,
    sqlite3,
    start,
    submit,
    sys,
    tempfile,
    token_of,
)

def test_canvas_shows_the_run(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))
    body = env["client"].get(f"/api/v1/flows/{FG}/canvas", headers=_h()).json()
    assert {k: body["run"][k] for k in ("run_id", "status", "revision_no", "open_node_keys", "last_event_seq")} ==         {"run_id": run_id, "status": "running", "revision_no": 1, "open_node_keys": [], "last_event_seq": 4}
    # with the band's facts of the same run read
    assert body["run"]["idle_reason"] is None and body["run"]["pending_node_keys"] == []
    nodes = {n["node_key"]: n for n in body["nodes"]}
    assert nodes["gn_r"]["state"] == "done" and nodes["gn_r"]["node_run"]["status"] == "done"
    assert nodes["gn_d"]["state"] == "review" and nodes["gn_d"]["document"]["doc_id"] == d
    assert nodes["gn_d"]["node_run"] == {"status": "review", "opened_at": nodes["gn_d"]["node_run"]["opened_at"],
                                         "finished_at": None, "result_doc_id": d,
                                         "dispatch": {"token_issued": True, "mention_id": None, "issued_to": "u_lee"},
                                         "awaiting_approval": True,                       # a person's result
                                         "stage": None, "instruction_doc_id": None}       # no AI: no card run stage
    assert nodes["gn_p"]["state"] == "draft" and nodes["gn_p"]["node_run"]["status"] == "pending"
    assert f"doc:{d}" not in nodes and f"doc:{FG}.0001-R" not in nodes          # results are absorbed
def test_canvas_draws_the_revision_the_run_pinned(env, decided):
    _confirm_next(FG, [_node("gn_r", "R"), _node("gn_d", "D", "human", user="u_lee"),
                       _node("gn_q", "T", "human", user="u_kim")],
                  [_edge("ge_rd", "gn_r", "gn_d"), _edge("ge_dq", "gn_d", "gn_q")])
    run_id = start(env, body={"revision_no": 1}).json()["run"]["run_id"]          # S5-2: an older revision

    def canvas(query=""):
        res = env["client"].get(f"/api/v1/flows/{FG}/canvas{query}", headers=_h())
        assert res.status_code == 200, res.text
        return res.json()

    def graph_nodes(body):
        return {n["node_key"]: n for n in body["nodes"] if n["origin"] == "graph_node"}

    for body in (canvas(), canvas("?view=revision")):
        assert body["view"] == {"mode": "revision", "revision_no": 1, "draft_version": None}
        assert body["graph"]["latest_revision_no"] == 2
        assert (body["run"]["run_id"], body["run"]["revision_no"]) == (run_id, 1)
        nodes = graph_nodes(body)
        assert {k: n["node_run"]["status"] for k, n in nodes.items()} == {
            "gn_r": "done", "gn_d": "open", "gn_p": "pending"}                       # revision 1 only, all with runs
        assert {e["edge_id"] for e in body["edges"] if e["edge_class"] == "execution"} == {"ge_rd", "ge_dp"}
    control(env, run_id, "stop")
    assert canvas()["view"]["revision_no"] == 1 and canvas()["run"]["status"] == "stopped"
    control(env, run_id, "resume")
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env, "gn_d"))
    decide_review(FG, d, "approve", "approved")
    p = _doc(FG, 3, "P")
    submit(FG, p, "P", token_of(env, "gn_p"))
    decide_review(FG, p, "approve", "approved")
    assert current(env) is None
    body = canvas()                                     # finished run of revision 1 is not laid over revision 2
    assert body["view"]["revision_no"] == 2 and body["run"] is None
    nodes = graph_nodes(body)
    assert set(nodes) == {"gn_r", "gn_d", "gn_q"} and all(n["node_run"] is None for n in nodes.values())
def test_a_finished_run_of_the_latest_revision_stays_on_the_canvas(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env, "gn_d"))
    decide_review(FG, d, "approve", "approved")
    p = _doc(FG, 3, "P")
    submit(FG, p, "P", token_of(env, "gn_p"))
    decide_review(FG, p, "approve", "approved")
    body = env["client"].get(f"/api/v1/flows/{FG}/canvas", headers=_h()).json()
    assert body["view"]["revision_no"] == 1 and (body["run"]["run_id"], body["run"]["status"]) == (run_id, "completed")
def test_run_reads_and_request_checks(env, decided):
    assert env["client"].get("/api/v1/runs/run_missing", headers=_h()).json()["error"] == {
        "code": "run_not_found", "message": "실행을 찾을 수 없습니다.", "params": {"run_id": "run_missing"}}
    assert env["client"].get(f"/api/v1/flows/{FG}/runs?limit=0", headers=_h()).status_code == 422
    assert env["client"].get(f"/api/v1/flows/{FG}/runs?limit=101", headers=_h()).status_code == 422
    empty = env["client"].get(f"/api/v1/flows/{FG}/runs", headers=_h()).json()
    assert (empty["total"], empty["items"], empty["limit"], empty["offset"]) == (0, [], 20, 0)
    run_id = start(env).json()["run"]["run_id"]
    long = env["client"].post(f"/api/v1/runs/{run_id}/stop", json={"reason": "x" * 501}, headers=_h())
    assert long.status_code == 422 and long.json()["error"]["params"]["errors"] == [{"field": "reason", "reason": "too_long"}]
    assert env["client"].post(f"/api/v1/flows/{FG}/runs", headers={}).status_code == 401
def test_migration_144_tables_trigger_and_constraints(env, decided):
    tables = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(RUN_TABLES) <= tables
    triggers = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert "trg_graph_runs_group_deleted" in triggers
    run_id = start(env).json()["run"]["run_id"]
    conn = sqlite3.connect(env["db"])
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):                # I-08: one active run per Flow
            conn.execute("INSERT INTO graph_runs (run_id, group_id, revision_no, status, active_group_id, started_at, "
                         "policy_source, policy_set_id, policy_set_version, policy_digest, updated_at) "
                         "VALUES ('run_x', ?, 1, 'running', ?, 't', 'system_default', 's', '1', ?, 't')",
                         (FG, FG, "0" * 64))
        with pytest.raises(sqlite3.IntegrityError):                # I-09: one node run per result document
            conn.execute("UPDATE graph_node_runs SET result_doc_id = ? WHERE run_id = ? AND node_key = 'gn_d'",
                         (f"{FG}.0001-R", run_id))
    finally:
        conn.close()
def test_group_delete_with_foreign_keys_off_leaves_no_run_rows(env, decided):
    start(env)
    start(env, FH)
    conn = sqlite3.connect(env["db"])
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        conn.execute("DELETE FROM groups WHERE group_id = ?", (FG,))
        conn.commit()
    finally:
        conn.close()
    assert count(env, "graph_runs", "group_id = ?", (FG,)) == 0
    for table in RUN_TABLES[1:]:
        assert count(env, table, "run_id NOT IN (SELECT run_id FROM graph_runs)") == 0, table
    assert count(env, "graph_runs", "group_id = ?", (FH,)) == 1
