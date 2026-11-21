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

def test_a_failed_token_issue_keeps_the_run_and_is_retried_on_resume(env, decided):
    # flowgate.v02.0083 T#1 (0056 T#1 개정): the rejected bound root is the run's input, done at start;
    # gn_d opens at once and its token issue to u_ghost (no users row) fails. No root revision is needed.
    res = start(env, FH)
    assert res.status_code == 201
    run = res.json()["run"]
    assert run["open_node_keys"] == ["gn_d"] and nodes_of(run)["gn_r"]["status"] == "done"
    d = nodes_of(current(env, FH))["gn_d"]
    assert d["status"] == "open" and d["dispatch"] == {
        "token_issued": False, "mention_id": None, "issued_to": "u_ghost", "error_code": "token_issue_failed"}
    assert count(env, "tokens", "issued_to = ?", ("u_ghost",)) == 0 and count(env, "graph_run_token_map") == 0
    _sql(env["db"], "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                    "VALUES ('u_ghost', 'u_ghost', 'g@x', 'x', 't', 't')")
    rid = run["run_id"]
    # 0061 T#7: the run waits idle on gn_d and an idle run is not stopped; resume goes on and re-dispatches it
    seen = len(run_events(env))
    assert control(env, rid, "resume").status_code == 200
    d = nodes_of(current(env, FH))["gn_d"]
    assert d["dispatch"] == {"token_issued": True, "mention_id": None, "issued_to": "u_ghost"}
    assert "redispatched" in [c for *_x, c in run_events(env)[seen:]]
def test_an_ai_node_is_started_after_the_commit(env, decided):
    from modules.flow_gate.graph_workflow.services import node_dispatch

    jobs = []
    node_dispatch.set_ai_starter(lambda job: jobs.append(job))
    start(env)
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))
    decide_review(FG, d, "approve", "approved")
    assert [(j["node_key"], j["provider_id"], j["token"]["token_id"]) for j in jobs] == [
        ("gn_p", "claude-code", token_of(env, "gn_p"))]
    assert nodes_of(current(env))["gn_p"]["dispatch"] == {"token_issued": True, "mention_id": None,
                                                          "issued_to": "claude-code"}
def test_an_ai_node_is_started_once_through_the_ai_invoke_service(env, decided, monkeypatch):
    calls = _fake_ai_invoke(monkeypatch)
    start(env)
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))
    decide_review(FG, d, "approve", "approved")
    assert len(calls) == 1
    call = calls[0]
    assert {k: call[k] for k in ("project_id", "module", "group_id", "doc_ref", "action_scope", "mode",
                                 "issued_to", "provider_id", "api_base_url", "continuation_target_seq")} == {
        "project_id": "flowgate", "module": "v02", "group_id": FG, "doc_ref": f"{FG}.0001-R", "action_scope": "new",
        "mode": "single", "issued_to": "u_kim", "provider_id": "claude-code", "api_base_url": "/flowgate/api/v1",
        "continuation_target_seq": None}
    issued = call["issued"]
    assert issued["mention"] and issued["raw_token"] in issued["mention"]
    token = _sql(env["db"], "SELECT * FROM tokens WHERE token_id = ?", (issued["token_id"],))[0]
    assert (token["action_scope"], token["doc_ref"], token["issued_to"], token["provider_id"], token["ai_run_id"],
            token["group_id"]) == ("new", f"{FG}.0001-R", "u_kim", "claude-code", "air_graph_1", FG)
    mapped = [r["token_id"] for r in _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = 'gn_p'")]
    assert len(mapped) == 2 and issued["token_id"] in mapped                   # node executor's + the AI run's
    p_run = nodes_of(current(env))["gn_p"]
    assert p_run["status"] == "open" and p_run["dispatch"] == {
        "token_issued": True, "mention_id": None, "issued_to": "claude-code"}  # no ai_invoke_failed
    assert "dispatch_failed" not in [c for *_x, c in run_events(env)]
    p = _doc(FG, 3, "P")
    submit(FG, p, "P", issued["token_id"])                                     # the AI's submission binds
    p_run = nodes_of(current(env))["gn_p"]
    assert (p_run["status"], p_run["result_doc_id"]) == ("review", p)
def test_a_refused_ai_start_marks_the_node_and_keeps_its_token(env, decided, monkeypatch):
    calls = _fake_ai_invoke(monkeypatch, refuse="run_in_progress")
    start(env)
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))
    decide_review(FG, d, "approve", "approved")
    assert calls == []
    p_run = nodes_of(current(env))["gn_p"]
    assert p_run["dispatch"] == {"token_issued": True, "mention_id": None, "issued_to": "claude-code",
                                 "error_code": "ai_invoke_failed"}
    assert count(env, "graph_run_token_map", "node_key = 'gn_p'") == 1
    assert [c for *_x, c in run_events(env)][-1] == "dispatch_failed"
def test_canvas_absorbs_root_and_result_documents(env, decided):
    """Keep projection coverage independent of the legacy run status/event assertions below."""
    start(env)
    result = _doc(FG, 2, "D")
    submit(FG, result, "D", token_of(env))
    response = env["client"].get(f"/api/v1/flows/{FG}/canvas", headers=_h())
    assert response.status_code == 200, response.text
    nodes = {n["node_key"]: n for n in response.json()["nodes"]}
    assert nodes["gn_r"]["document"]["doc_id"] == f"{FG}.0001-R"
    assert nodes["gn_d"]["document"]["doc_id"] == result
    assert f"doc:{result}" not in nodes and f"doc:{FG}.0001-R" not in nodes
