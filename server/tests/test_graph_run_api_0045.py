"""flowgate.v02.0045 T0015 (S5) — Graph runs on a real SQLite DB (all migrations, TestClient).

P0008 §7 S5-1~S5-5 and §8.3 run events, L0009 §2.6.5~§2.6.9 (start with root binding, apply,
stop / resume, result binding, reconciliation, dispatch failure), DB0010 §3.5 (migration 144)
and §5.3 (Flow delete cleanup). Until D0004 §8 is answered a start is refused with
``run_policy_undecided``; the rest uses decided *test* policies registered under their own value
(the product default stays ``undecided``). Auth, RBAC and SSE delivery are stubbed; the token
service runs for real with its scratch directory moved under pytest's tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FG = "flowgate.v02.0071"      # Graph: R(approved, bound) -> D(u_lee) -> P(AI claude-code)
FH = "flowgate.v02.0072"      # Graph: R(rejected, bound) -> D(u_ghost: no users row)
FU = "flowgate.v02.0073"      # unassigned
FV = "flowgate.v02.0074"      # v0.1
RUN_TABLES = ("graph_runs", "graph_run_policy_entries", "graph_node_runs", "graph_run_token_map",
              "graph_run_events", "graph_propagations")
TEST_VALUE, TEST_VERSION = "s5_api_test", "1"


@pytest.fixture()
def db_path():
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import connection as conn_mod

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = sqlite3.connect(path)
    for mig in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    conn.close()
    original = conn_mod.STORE
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(path)
    conn_mod.STORE = store
    try:
        yield path
    finally:
        conn_mod.STORE = original
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture()
def env(db_path, monkeypatch, tmp_path):
    from modules.flow_gate.db import _SqliteTxnAdapter
    from modules.flow_gate.graph_workflow.api import canvas_routes, envelope, graph_routes, run_routes
    from modules.flow_gate.graph_workflow.services import canvas_events, node_dispatch, run_events
    from modules.flow_gate.services import token_service

    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    state = {"events": [], "db": db_path}

    def fake_verify(request):
        raw = request.headers.get("Authorization", "")
        if raw.startswith("Bearer user:"):
            return {"_is_user_jwt": True, "issued_to": raw[len("Bearer user:"):]}
        return JSONResponse(status_code=401, content={"ok": False})

    monkeypatch.setattr(envelope, "verify_bearer", fake_verify)
    monkeypatch.setattr(envelope, "has_permission", lambda u, p, perm: True)
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    monkeypatch.setattr(run_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    monkeypatch.setattr(token_service, "_scratch_dir", lambda project, token_id: tmp_path / "work" / token_id)
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "s5test")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_s5test", "s5-test-pepper")
    old_starter = node_dispatch.set_ai_starter(None)
    app = FastAPI()
    for module in (canvas_routes, graph_routes, run_routes):
        app.include_router(module.router)
    state["client"] = TestClient(app)
    _seed(db_path)
    state["events"].clear()
    yield state
    node_dispatch.set_ai_starter(old_starter)


class _TestPolicy:
    """Decided test double for all nine slots: start nodes, wait on rejection, all done = complete."""

    def select(self, *args):
        return args[0].start_keys() if len(args) == 2 else args[2]

    def admit(self, candidates, _open):
        return candidates

    def openable(self, _target, states):
        return "open" if all(s["state"] == "completed" for s in states) else "wait"

    def route(self, *_args):
        return [], []

    def is_complete(self, revision, state):
        return all(state.node(k) is not None and state.node(k).status == "done" for k in revision.node_keys())


_POLICY = _TestPolicy()


@pytest.fixture()
def decided():
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    old = {}
    for key in policies.POLICY_KEYS:
        run_policies.register(key, TEST_VALUE, TEST_VERSION, _POLICY)
        old[key] = policies.set_system_default(key, TEST_VALUE, TEST_VERSION)
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _doc(flow, seq, typ, review=None, status="open", **extra):
    from modules.flow_gate.db import documents as db_documents

    doc_id = f"{flow}.{seq:04d}-{typ}"
    db_documents.create({"doc_id": doc_id, "project_id": "flowgate", "module": "v02", "group_id": flow,
                         "type_code": typ, "seq": seq, "title": f"{typ} {seq}", "status": status, **extra})
    if review is not None:
        db_documents.update(doc_id, {"doc_review_status": review})
    return doc_id


def _node(key, typ, kind=None, user=None, provider=None):
    return {"node_key": key, "node_type": "document", "doc_type": typ, "label": typ,
            "assignee_kind": kind, "assignee_user_id": user, "assignee_provider_id": provider}


def _edge(eid, s, t):
    return {"edge_id": eid, "source": s, "target": t}


def _confirm(flow, nodes, edges):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services import confirm_service
    from modules.flow_gate.graph_workflow.store import definition_store

    with get_store().transaction():
        definition_store.create_draft(flow, None, "u_kim")
        definition_store.replace_draft_content(flow, nodes, edges)
    confirm_service.confirm_revision({"group_id": flow, "project_id": "flowgate"}, "u_kim", 1, None, None)


def _confirm_next(flow, nodes, edges):
    """Confirm the next revision on top of the latest one (P0008 S4)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services import confirm_service
    from modules.flow_gate.graph_workflow.store import definition_store

    latest = definition_store.latest_revision_no(flow)
    with get_store().transaction():
        definition_store.create_draft(flow, latest, "u_kim")
        definition_store.replace_draft_content(flow, nodes, edges)
    version = definition_store.draft(flow)["draft_version"]
    confirm_service.confirm_revision({"group_id": flow, "project_id": "flowgate"}, "u_kim", version, latest, None)


def _seed(db):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db import workflow_sequences as ws

    db_projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    for uid in ("u_kim", "u_lee"):
        _sql(db, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'x', 't', 't')", (uid, uid, f"{uid}@x"))
    _sql(db, "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
             "VALUES ('claude-code', 'S5 test provider', 'cli', 'claude', 't', 't')")
    for gid in (FG, FH, FU, FV):
        db_groups.create({"group_id": gid, "project_id": "flowgate", "module": "v02", "title": gid, "status": "OPEN"})
    _doc(FG, 1, "R", "approved")
    _doc(FH, 1, "R", "rejected")
    _doc(FU, 1, "R")
    _doc(FV, 1, "R")
    ws.insert_sequence(f"{FV}.0001-R")
    ws.insert_sequence_item(ws.get_sequence_by_doc_id(f"{FV}.0001-R")["id"], 1, "D", "설계", "R", 0)
    _confirm(FG, [_node("gn_r", "R"), _node("gn_d", "D", "human", user="u_lee"),
                  _node("gn_p", "P", "ai", provider="claude-code")],
             [_edge("ge_rd", "gn_r", "gn_d"), _edge("ge_dp", "gn_d", "gn_p")])
    _confirm(FH, [_node("gn_r", "R"), _node("gn_d", "D", "human", user="u_ghost")], [_edge("ge_rd", "gn_r", "gn_d")])


def _h():
    return {"Authorization": "Bearer user:u_kim", "x-canvas-client-id": "tab_1"}


def start(env, flow=FG, body=None):
    return env["client"].post(f"/api/v1/flows/{flow}/runs", json=body, headers=_h())


def current(env, flow=FG):
    res = env["client"].get(f"/api/v1/flows/{flow}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def control(env, run_id, action, reason=None):
    return env["client"].post(f"/api/v1/runs/{run_id}/{action}", json={"reason": reason} if reason else None,
                              headers=_h())


def run_events(env):
    return [(e.payload["event_seq"], e.event_type, e.payload.get("node_key"), e.payload.get("cause"))
            for e in env["events"] if e.event_type.startswith("graph_run") or e.event_type == "graph_node_state_changed"]


def nodes_of(run):
    return {n["node_key"]: n for n in run["node_runs"]}


def token_of(env, node="gn_d"):
    rows = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY mapped_at DESC", (node,))
    return rows[0]["token_id"]


def submit(flow, doc_id, typ, token_id):
    from modules.flow_gate.graph_workflow.api import v01_contact

    v01_contact.after_inbox_registered(flow, doc_id, typ, token_id)


def decide_review(flow, doc_id, action, review):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    db_documents.update(doc_id, {"doc_review_status": review})
    v01_contact.after_review_decided(flow, doc_id, action)


def count(env, table, where="1 = 1", params=()):
    return _sql(env["db"], f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)[0]["n"]


# ── S5-1 / S5-3: refusals ────────────────────────────────────────────────────

def test_start_is_refused_while_the_rules_are_undecided(env):
    res = start(env, body={"revision_no": 1})
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "run_policy_undecided"
    assert err["message"] == "실행 규칙이 아직 정해지지 않아 실행을 시작할 수 없습니다."
    assert err["params"] == {"flow_id": FG, "revision_no": 1, "undecided_policies": [
        {"policy": "start_node", "rule_ref": "Q-1"}, {"policy": "rejection_path", "rule_ref": "Q-6"},
        {"policy": "completion", "rule_ref": "Q-10"}]}
    for table in RUN_TABLES:
        assert count(env, table) == 0, table
    assert env["events"] == [] and current(env) is None
    policies = env["client"].get(f"/api/v1/flows/{FG}/graph", headers=_h()).json()["policies"]
    assert set(policies.values()) == {"undecided"}


def test_start_refusals_follow_the_l0009_order(env, decided):
    unassigned = start(env, FU)
    assert unassigned.status_code == 409 and unassigned.json()["error"]["code"] == "flow_unassigned"
    assert unassigned.json()["error"]["params"] == {"flow_id": FU}
    v01 = start(env, FV).json()["error"]
    assert v01["code"] == "flow_owned_by_v01" and v01["params"]["sequence_root_doc_id"] == f"{FV}.0001-R"
    missing = start(env, body={"revision_no": 7})
    assert missing.status_code == 404 and missing.json()["error"]["params"] == {"flow_id": FG, "revision_no": 7}
    bad = start(env, body={"revision_no": 0})
    assert bad.status_code == 422 and bad.json()["error"]["params"]["errors"][0]["field"] == "revision_no"
    first = start(env)
    assert first.status_code == 201
    again = start(env)
    assert again.status_code == 409 and again.json()["error"]["params"] == {
        "flow_id": FG, "run_id": first.json()["run"]["run_id"], "status": "running"}


def test_a_deleted_root_document_refuses_the_start(env, decided):
    from modules.flow_gate.db import documents as db_documents

    db_documents.update(f"{FG}.0001-R", {"status": "cancelled"})
    res = start(env)
    assert res.status_code == 409 and res.json()["error"]["code"] == "root_binding_lost"
    assert res.json()["error"]["params"] == {"flow_id": FG, "node_key": "gn_r", "doc_id": f"{FG}.0001-R"}
    assert count(env, "graph_runs") == 0


# ── S5-2: start ──────────────────────────────────────────────────────────────

def test_start_binds_the_root_and_opens_the_next_node(env, decided):
    res = start(env, body={"revision_no": 1})
    assert res.status_code == 201, res.text
    run = res.json()["run"]
    assert run["run_id"].startswith("run_") and len(run["run_id"]) == 16
    assert (run["flow_id"], run["revision_no"], run["status"], run["started_by"]) == (FG, 1, "running", "u_kim")
    assert run["open_node_keys"] == ["gn_d"] and run["last_event_seq"] == 3
    n = nodes_of(run)
    assert n["gn_r"]["status"] == "done" and n["gn_r"]["root_bound"] is True and n["gn_r"]["dispatch"] is None
    assert n["gn_r"]["result_doc_id"] == f"{FG}.0001-R" and n["gn_r"]["opened_at"] is None
    assert n["gn_d"]["status"] == "open" and n["gn_d"]["dispatch"] == {
        "token_issued": True, "mention_id": None, "issued_to": "u_lee"}
    assert n["gn_p"]["status"] == "pending" and n["gn_p"]["dispatch"] is None
    assert run_events(env) == [(1, "graph_run_status_changed", None, "user_start"),
                               (2, "graph_node_state_changed", "gn_r", "root_bound_start"),
                               (3, "graph_node_state_changed", "gn_d", "opened")]
    first = env["events"][0]
    assert first.payload["from"] is None and first.payload["to"] == "running" and "origin_client_id" not in first.payload
    assert env["events"][1].payload["canvas_state"] == "done" and env["events"][2].payload["canvas_state"] == "in_progress"
    rid = run["run_id"]
    entries = {r["policy_key"]: {"value": r["policy_value"], "impl_version": r["impl_version"]}
               for r in _sql(env["db"], "SELECT * FROM graph_run_policy_entries WHERE run_id = ?", (rid,))}
    assert len(entries) == 9
    digest = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    head = _sql(env["db"], "SELECT * FROM graph_runs WHERE run_id = ?", (rid,))[0]
    assert head["policy_digest"] == digest and head["active_group_id"] == FG and head["policy_source"] == "system_default"
    token = _sql(env["db"], "SELECT * FROM tokens WHERE token_id = ?", (token_of(env),))[0]
    assert (token["action_scope"], token["doc_ref"], token["issued_to"], token["group_id"]) == (
        "new", f"{FG}.0001-R", "u_lee", FG)
    assert _sql(env["db"], "SELECT node_key, outcome, status FROM graph_propagations WHERE run_id = ?", (rid,)) == [
        {"node_key": "gn_r", "outcome": "approved", "status": "done"}]
    assert [r["event_seq"] for r in _sql(env["db"], "SELECT event_seq FROM graph_run_events ORDER BY event_seq")] == [1, 2, 3]


def test_a_rejected_root_starts_blocked_and_waits(env, decided):
    run = start(env, FH).json()["run"]
    assert nodes_of(run)["gn_r"]["status"] == "rejected" and run["open_node_keys"] == []
    evs = [e.payload for e in env["events"]]
    assert [(p["node_key"], p["from"], p["to"], p["cause"], p["canvas_state"]) for p in evs[1:]] == [
        ("gn_r", "pending", "review", "root_bound_start", "blocked"),
        ("gn_r", "review", "rejected", "review_rejected", "blocked")]
    props = _sql(env["db"], "SELECT outcome, status, source_event_seq FROM graph_propagations")
    assert props == [{"outcome": "rejected", "status": "done", "source_event_seq": 3}]


# ── result binding (L0009 §2.6.8) ────────────────────────────────────────────

def test_results_move_the_run_to_completion(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env, "gn_d"))
    decide_review(FG, d, "approve", "approved")
    p_run = nodes_of(current(env))["gn_p"]
    assert p_run["status"] == "open" and p_run["dispatch"] == {
        "token_issued": True, "mention_id": None, "issued_to": "claude-code", "error_code": "ai_invoke_failed"}
    p_token = _sql(env["db"], "SELECT * FROM tokens WHERE token_id = ?", (token_of(env, "gn_p"),))[0]
    assert p_token["issued_to"] == "u_kim" and p_token["provider_id"] == "claude-code"
    p = _doc(FG, 3, "P")
    submit(FG, p, "P", token_of(env, "gn_p"))
    decide_review(FG, p, "approve", "approved")
    assert run_events(env)[3:] == [
        (4, "graph_node_state_changed", "gn_d", "result_submitted"),
        (5, "graph_node_state_changed", "gn_d", "review_approved"),
        (6, "graph_node_state_changed", "gn_p", "opened"),
        (7, "graph_node_state_changed", "gn_p", "dispatch_failed"),
        (8, "graph_node_state_changed", "gn_p", "result_submitted"),
        (9, "graph_node_state_changed", "gn_p", "review_approved"),
        (10, "graph_run_status_changed", None, "completed")]
    assert env["events"][-1].payload["actor_user_id"] is None
    head = _sql(env["db"], "SELECT status, active_group_id, finished_at, last_event_seq FROM graph_runs")[0]
    assert head["status"] == "completed" and head["active_group_id"] is None and head["finished_at"]
    assert head["last_event_seq"] == 10 and current(env) is None
    got = env["client"].get(f"/api/v1/runs/{run_id}", headers=_h()).json()["run"]
    assert {k: v["result_doc_id"] for k, v in nodes_of(got).items()} == {"gn_r": f"{FG}.0001-R", "gn_d": d, "gn_p": p}
    listed = env["client"].get(f"/api/v1/flows/{FG}/runs?limit=5", headers=_h()).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "completed" and "node_runs" not in listed["items"][0]


def test_documents_that_are_not_results_bind_nothing(env, decided):
    start(env)
    seq = current(env)["last_event_seq"]
    submit(FG, _doc(FG, 2, "D"), "D", None)                         # no token: Q-12, not bound
    submit(FG, _doc(FG, 3, "M"), "M", token_of(env))                # the node's token, another type (E-24)
    decide_review(FG, f"{FG}.0002-D", "approve", "approved")        # not a node result
    assert current(env)["last_event_seq"] == seq and nodes_of(current(env))["gn_d"]["status"] == "open"
    d = _doc(FG, 4, "D")
    submit(FG, d, "D", token_of(env))
    submit(FG, _doc(FG, 5, "D"), "D", token_of(env))                # a second result is not bound
    decide_review(FG, d, "approve", "approved")
    after = current(env)["last_event_seq"]
    decide_review(FG, d, "approve", "approved")                     # repeated: ignored, no event_seq
    assert current(env)["last_event_seq"] == after and nodes_of(current(env))["gn_d"]["result_doc_id"] == d


def test_a_revision_replaces_the_result_and_a_chain_gap_is_walked(env, decided):
    from modules.flow_gate.db import documents as db_documents

    start(env)
    d1 = _doc(FG, 2, "D")
    submit(FG, d1, "D", token_of(env))
    decide_review(FG, d1, "reject", "rejected")
    assert nodes_of(current(env))["gn_d"]["status"] == "rejected"
    d2 = _doc(FG, 3, "D", "rejected")                              # revision of d1, rejected, both unreported
    db_documents.set_superseded_by(d1, d2)
    d3 = _doc(FG, 4, "D", "pending_review")
    db_documents.set_superseded_by(d2, d3)
    submit(FG, d3, "D", None)                                      # gap d1 -> d2 -> d3: walk it
    node = nodes_of(current(env))["gn_d"]
    assert node["status"] == "review" and node["result_doc_id"] == d3
    assert [c for _s, _t, k, c in run_events(env) if k == "gn_d"][-3:] == ["revised", "review_rejected", "revised"]
    d4 = _doc(FG, 5, "D")
    db_documents.set_superseded_by(d3, d4)
    submit(FG, d4, "D", None)                                      # direct revision (one hop)
    assert nodes_of(current(env))["gn_d"]["result_doc_id"] == d4


# ── S5-5: stop / resume (L0009 §2.6.7) ───────────────────────────────────────

def test_stop_and_resume(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    stopped = control(env, run_id, "stop", "설계 재검토")
    assert stopped.status_code == 200 and stopped.json()["run"]["status"] == "stopped"
    assert stopped.json()["run"]["stopped_at"] and "node_runs" not in stopped.json()["run"]
    assert env["events"][-1].payload["reason"] == "설계 재검토" and env["events"][-1].payload["cause"] == "user_stop"
    again = control(env, run_id, "stop")
    assert again.status_code == 409 and again.json()["error"]["params"] == {"run_id": run_id, "status": "stopped"}
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))
    decide_review(FG, d, "approve", "approved")                     # stopped: done, but nothing opens
    held = current(env)
    assert nodes_of(held)["gn_d"]["status"] == "done" and nodes_of(held)["gn_p"]["status"] == "pending"
    assert _sql(env["db"], "SELECT status FROM graph_propagations WHERE node_key = 'gn_d'") == [{"status": "pending"}]
    resumed = control(env, run_id, "resume")
    assert resumed.status_code == 200 and resumed.json()["run"]["status"] == "running"
    assert resumed.json()["run"]["stopped_at"] is None and resumed.json()["run"]["open_node_keys"] == ["gn_p"]
    assert [c for *_x, c in run_events(env)][-3:] == ["user_resume", "opened", "dispatch_failed"]
    assert _sql(env["db"], "SELECT status FROM graph_propagations WHERE node_key = 'gn_d'") == [{"status": "done"}]
    twice = control(env, run_id, "resume")
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "run_not_stopped"
    assert control(env, "run_nope", "stop").status_code == 404


def test_resume_refuses_an_unrestorable_snapshot(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    control(env, run_id, "stop")
    _sql(env["db"], "UPDATE graph_run_policy_entries SET impl_version = 'gone' WHERE policy_key = 'join'")
    from modules.flow_gate.graph_workflow.services.policy_snapshot import check_active_runs

    assert check_active_runs() == [{"policy": "join", "value": TEST_VALUE, "impl_version": "gone"}]
    res = control(env, run_id, "resume")
    assert res.status_code == 500 and res.json()["error"]["code"] == "internal_error"
    assert _sql(env["db"], "SELECT status FROM graph_runs")[0]["status"] == "stopped"     # E-33: nothing written


# ── reconciliation (L0009 §2.6.8, E-27) ──────────────────────────────────────

def test_a_lost_delegation_is_recovered_before_a_run_read(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import result_binder
    from modules.flow_gate.services import token_service

    start(env)
    real = result_binder.on_document_event
    monkeypatch.setattr(result_binder, "on_document_event", lambda *a: (_ for _ in ()).throw(RuntimeError("lost")))
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))                               # swallowed by the contact
    token_service.consume(token_of(env), "flowgate", d)              # what the inbox records next
    decide_review(FG, d, "approve", "approved")                     # lost as well
    monkeypatch.setattr(result_binder, "on_document_event", real)
    assert _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = 'gn_d'") == [{"status": "open"}]
    run = current(env)
    assert nodes_of(run)["gn_d"]["status"] == "done" and run["open_node_keys"] == ["gn_p"]
    assert [c for *_x, c in run_events(env)][3:6] == ["result_submitted", "review_approved", "opened"]


# ── dispatch (L0009 §2.6.6, E-22 / E-23) ─────────────────────────────────────

def test_a_failed_token_issue_keeps_the_run_and_is_retried_on_resume(env, decided):
    res = start(env, FH)
    assert res.status_code == 201
    run = res.json()["run"]
    assert run["open_node_keys"] == [] and nodes_of(run)["gn_r"]["status"] == "rejected"
    from modules.flow_gate.db import documents as db_documents

    r2 = _doc(FH, 2, "R", "approved")                              # the rejected root is revised and approved
    db_documents.set_superseded_by(f"{FH}.0001-R", r2)
    submit(FH, r2, "R", None)
    decide_review(FH, r2, "approve", "approved")
    d = nodes_of(current(env, FH))["gn_d"]
    assert d["status"] == "open" and d["dispatch"] == {
        "token_issued": False, "mention_id": None, "issued_to": "u_ghost", "error_code": "token_issue_failed"}
    assert count(env, "tokens", "issued_to = ?", ("u_ghost",)) == 0 and count(env, "graph_run_token_map") == 0
    _sql(env["db"], "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                    "VALUES ('u_ghost', 'u_ghost', 'g@x', 'x', 't', 't')")
    rid = run["run_id"]
    control(env, rid, "stop")
    assert control(env, rid, "resume").status_code == 200
    d = nodes_of(current(env, FH))["gn_d"]
    assert d["dispatch"] == {"token_issued": True, "mention_id": None, "issued_to": "u_ghost"}
    assert [c for *_x, c in run_events(env)][-1] == "redispatched"


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


def _fake_ai_invoke(monkeypatch, refuse=None):
    """The v0.1 AI invoke admission as far as Graph sees it: mint through issue_builder, then launch."""
    from fastapi import HTTPException

    from modules.flow_gate.graph_workflow.services import node_dispatch
    from modules.flow_gate.services import ai_invoke_service

    calls = []

    def fake_start_run(**kw):
        if refuse:
            raise HTTPException(status_code=409, detail={"code": refuse})
        issued = kw["issue_builder"](ai_run_id="air_graph_1")
        assert kw["mention_builder"]("raw", "dir") is None
        calls.append({**kw, "issued": issued})
        return {"run_id": "air_graph_1", "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", fake_start_run)
    node_dispatch.set_ai_starter(node_dispatch._start_with_ai_invoke)          # the product default
    return calls


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


# ── Canvas (P0008 S1-3) and reads ────────────────────────────────────────────

def test_canvas_shows_the_run(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))
    body = env["client"].get(f"/api/v1/flows/{FG}/canvas", headers=_h()).json()
    assert body["run"] == {"run_id": run_id, "status": "running", "revision_no": 1, "open_node_keys": [],
                           "last_event_seq": 4}
    nodes = {n["node_key"]: n for n in body["nodes"]}
    assert nodes["gn_r"]["state"] == "done" and nodes["gn_r"]["node_run"]["status"] == "done"
    assert nodes["gn_d"]["state"] == "review" and nodes["gn_d"]["document"]["doc_id"] == d
    assert nodes["gn_d"]["node_run"] == {"status": "review", "opened_at": nodes["gn_d"]["node_run"]["opened_at"],
                                         "finished_at": None, "result_doc_id": d,
                                         "dispatch": {"token_issued": True, "mention_id": None, "issued_to": "u_lee"}}
    assert nodes["gn_p"]["state"] == "draft" and nodes["gn_p"]["node_run"]["status"] == "pending"
    assert f"doc:{d}" not in nodes and f"doc:{FG}.0001-R" not in nodes          # results are absorbed


def test_canvas_draws_the_revision_the_run_pinned(env, decided):
    _confirm_next(FG, [_node("gn_r", "R"), _node("gn_d", "D", "human", user="u_lee"),
                       _node("gn_q", "T", "human", user="u_kim")],
                  [_edge("ge_rd", "gn_r", "gn_d"), _edge("ge_dq", "gn_d", "gn_q")])
    run_id = start(env, body={"revision_no": 1}).json()["run"]["run_id"]          # P0008 S5-2: an older revision

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


# ── migration 144 (DB0010 §3.5 / §5.3) ───────────────────────────────────────

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
