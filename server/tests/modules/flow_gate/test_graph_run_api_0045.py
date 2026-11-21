"""(S5) — Graph runs on a real SQLite DB (all migrations, TestClient).

S5-1~S5-5 and run events, the earlier implementation~ (start with root binding, apply,
stop / resume, result binding, reconciliation, dispatch failure), DB0010 §3.5 (migration 144)
and (Flow delete cleanup). Until the design is answered a start is refused with
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

_SERVER_DIR = Path(__file__).resolve().parents[3]
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
    # Token scratch stays where token_scratch.create requires it: <storage root>/work/<project>/<token>.
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "s5test")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_s5test", "s5-test-pepper")
    # Wire startup's required run ports while preserving the caller's registries.
    from flowgate.graph_run import integration as graph_run
    from modules.flow_gate.graph_workflow.services import run_state_ports
    from modules.flow_gate.services import graph_execution_hooks, graph_source_hooks
    monkeypatch.setattr(run_state_ports, "_handlers", dict(run_state_ports._handlers))
    monkeypatch.setattr(graph_execution_hooks, "_handler", graph_execution_hooks._handler)
    for attr in ("_handler", "_mutation_handler", "_fence_handler"):
        monkeypatch.setattr(graph_source_hooks, attr, getattr(graph_source_hooks, attr))
    graph_run.register()
    from flowgate.graph_validation.run_scope import run_scope
    from modules.flow_gate.graph_workflow.domain import run_scope_ports
    monkeypatch.setattr(run_scope_ports, "_handler", run_scope)
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
    """Confirm the next revision on top of the latest one."""
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


def test_0056_T1_개정_rejected_root_starts_done_and_opens_work(env, decided):
    """revised: root input bypasses review without changing the rejected document."""
    run = start(env, FH).json()["run"]
    root = nodes_of(run)["gn_r"]
    assert (root["status"], root["awaiting_approval"], run["open_node_keys"]) == ("done", False, ["gn_d"])
    evs = [e.payload for e in env["events"]]
    assert not any(p.get("cause") == "review_rejected" for p in evs)
    assert any(p.get("node_key") == "gn_r" and p.get("to") == "done"
               and p.get("cause") == "root_bound_start" for p in evs)
    props = _sql(env["db"], "SELECT outcome, status, source_event_seq FROM graph_propagations")
    assert props == [{"outcome": "approved", "status": "done", "source_event_seq": 2}]
    assert _sql(env["db"], "SELECT doc_review_status FROM documents WHERE doc_id = ?",
                (f"{FH}.0001-R",))[0]["doc_review_status"] == "rejected"


# ── result binding ────────────────────────────────────────────







# ── S5-5: stop / resume ───────────────────────────────────────





# ── reconciliation ──────────────────────────────────────



# ── dispatch ─────────────────────────────────────





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






# ── Canvas and reads ────────────────────────────────────────────











# ── migration 144 (DB0010 §3.5 / §5.3) ───────────────────────────────────────
