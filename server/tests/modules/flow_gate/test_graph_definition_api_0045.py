"""(S3+S4) — Graph definition / confirmation API on a real SQLite DB.

(S3-1~S3-6, GET graph / revisions), (S4-1~S4-7), Canvas S1-4 / S1-5 with Graph
nodes, (graph_canvas_changed draft / revision, flow_ownership_changed), the earlier implementation
(confirmation order, root binding), DB0010 §3.3 / §3.4 / §5.3 (migrations 142 / 143, cleanup).
Auth (verify_bearer), RBAC and SSE delivery are stubbed at the Graph API boundary.
"""
from __future__ import annotations

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

F51 = "flowgate.v02.0051"     # unassigned, R + N + M (+ draft)
F55 = "flowgate.v02.0055"     # unassigned, no documents
F31 = "flowgate.v02.0031"     # unassigned legacy group with two live R
F48 = "flowgate.v02.0048"     # v0.1 (sequence)
GRAPH_TABLES = ("graph_drafts", "graph_draft_nodes", "graph_draft_edges", "graph_revisions",
                "graph_revision_nodes", "graph_revision_edges", "graph_assignments", "graph_root_bindings")


@pytest.fixture(autouse=True)
def registered_card_ports(monkeypatch):
    """Exercise production card lines without depending on an earlier startup lifespan."""
    from flowgate.card_documents import integration
    from modules.flow_gate.graph_workflow.services import card_document_ports

    # Restore any handlers an earlier suite installed when this test finishes.
    monkeypatch.setattr(card_document_ports, "_handlers", {})
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
    integration.register()
    try:
        yield
    finally:
        integration.unregister()


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
    from modules.flow_gate.graph_workflow.api import canvas_routes, envelope, graph_routes
    from modules.flow_gate.graph_workflow.services import canvas_events

    # with the card document ports registered (startup / an earlier lifespan in
    # this process) a draft save files the cards' documents; keep them in a temp storage root.
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    state = {"denied": set(), "events": []}

    def fake_verify(request):
        raw = request.headers.get("Authorization", "")
        if raw == "Bearer worker":
            return {"issued_to": "u_kim", "project": "flowgate"}
        if raw.startswith("Bearer user:"):
            return {"_is_user_jwt": True, "issued_to": raw[len("Bearer user:"):]}
        return JSONResponse(status_code=401, content={"ok": False})

    monkeypatch.setattr(envelope, "verify_bearer", fake_verify)
    monkeypatch.setattr(envelope, "has_permission", lambda u, p, perm: (u, perm) not in state["denied"])
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    app = FastAPI()
    app.include_router(canvas_routes.router)
    app.include_router(graph_routes.router)
    state["client"] = TestClient(app)
    state["db"] = db_path
    _seed(db_path)
    return state


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _doc(flow, seq, typ, title, review=None, status="open", **extra):
    from modules.flow_gate.db import documents as db_documents

    doc_id = f"{flow}.{seq:04d}-{typ}"
    db_documents.create({"doc_id": doc_id, "project_id": "flowgate", "module": "v02", "group_id": flow,
                         "type_code": typ, "seq": seq, "title": title, "status": status, **extra})
    if review is not None:
        db_documents.update(doc_id, {"doc_review_status": review})
    return doc_id


def _seed(db):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db import workflow_sequences as ws

    db_projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    for uid in ("u_kim", "u_park"):
        _sql(db, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'x', 't', 't')", (uid, uid, f"{uid}@x"))
    for gid in (F51, F55, F31, F48):
        db_groups.create({"group_id": gid, "project_id": "flowgate", "module": "v02", "title": gid, "status": "OPEN"})
    r = _doc(F51, 1, "R", "Canvas 편집 저장", "approved")
    _doc(F51, 2, "N", "조사지시", "pending_review", triggered_by=r)
    _doc(F51, 3, "M", "킥오프 메모", triggered_by=r)
    _doc(F31, 1, "R", "옛 요구", "approved")
    _doc(F31, 4, "R", "또 다른 요구", "approved")
    r48 = _doc(F48, 1, "R", "Explorer 정렬", "wf_in_progress")
    ws.insert_sequence(r48)
    ws.insert_sequence_item(ws.get_sequence_by_doc_id(r48)["id"], 1, "D", "설계", "R", 0)


def _h(user="u_kim", client_id="tab_a"):
    headers = {"Authorization": f"Bearer user:{user}"}
    if client_id:
        headers["x-canvas-client-id"] = client_id
    return headers


def node(ref, doc_type="D", label=None, node_type="document", palette=None, assignee=None):
    key = {"node_key": None, "client_key": ref} if ref.startswith("tmp_") else {"node_key": ref, "client_key": None}
    return {**key, "node_type": node_type, "doc_type": doc_type if node_type == "document" else None,
            "palette_item_id": palette, "label": label or (doc_type or node_type), "assignee": assignee}


def edge(ref, source, target):
    key = {"edge_id": None, "client_key": ref} if ref.startswith("tmp_") else {"edge_id": ref, "client_key": None}
    return {**key, "source": source, "target": target, "condition": None}


def put_draft(env, flow, version, nodes, edges, **kw):
    return env["client"].put(f"/api/v1/flows/{flow}/graph/draft",
                             json={"draft_version": version, "nodes": nodes, "edges": edges}, headers=_h(**kw))


def validate(env, flow, version):
    return env["client"].post(f"/api/v1/flows/{flow}/graph/draft/validate", json={"draft_version": version},
                              headers=_h())


def confirm(env, flow, version, base=None, **kw):
    return env["client"].post(f"/api/v1/flows/{flow}/graph/revisions",
                              json={"draft_version": version, "base_revision_no": base}, headers=_h(**kw))


def canvas(env, flow, query=""):
    return env["client"].get(f"/api/v1/flows/{flow}/canvas{query}", headers=_h())


def linear_draft(env, flow=F51):
    """R -> D -> T draft (version 1). Returns the key map."""
    res = put_draft(env, flow, 0, [node("tmp_r", "R", "요구 시작"), node("tmp_d", "D", "기본설계", palette="basic_design",
                                    assignee={"kind": "ai", "provider_id": "claude-code", "user_id": None}),
                                   node("tmp_t", "T", "구현", palette="task")],
                    [edge("tmp_e1", "tmp_r", "tmp_d"), edge("tmp_e2", "tmp_d", "tmp_t")])
    assert res.status_code == 200, res.json()
    return res.json()["key_map"]


def events(env, event_type):
    return [e.payload for e in env["events"] if e.event_type == event_type]


# ── S3: draft save ───────────────────────────────────────────────────────────

def test_s3_1_first_save_issues_keys_and_publishes_draft_event(env):
    km = linear_draft(env)
    assert set(km) == {"tmp_r", "tmp_d", "tmp_t", "tmp_e1", "tmp_e2"}
    assert all(km[k].startswith("gn_") and len(km[k]) == 15 for k in ("tmp_r", "tmp_d", "tmp_t"))
    assert all(km[k].startswith("ge_") for k in ("tmp_e1", "tmp_e2"))
    [ev] = events(env, "graph_canvas_changed")
    assert ev["reason"] == "draft" and ev["draft_version"] == 1 and ev["origin_client_id"] == "tab_a"
    assert ev["revision_no"] is None and ev["node_keys"] == []
    body = env["client"].get(f"/api/v1/flows/{F51}/graph", headers=_h()).json()
    assert body["ownership"]["state"] == "unassigned"
    assert body["draft"]["draft_version"] == 1 and body["draft"]["base_revision_no"] is None
    assert body["draft"]["confirmable"] is True and body["draft"]["blocked_reason"] is None
    assert [n["label"] for n in body["draft"]["nodes"]] == ["요구 시작", "기본설계", "구현"]
    assert body["draft"]["nodes"][1]["assignee"] == {"kind": "ai", "provider_id": "claude-code", "user_id": None}
    assert body["draft"]["edges"][0] == {"edge_id": km["tmp_e1"], "source": km["tmp_r"], "target": km["tmp_d"],
                                         "condition": None}
    assert body["latest_revision"] is None
    assert set(body["policies"].values()) == {"undecided"} and len(body["policies"]) == 7


def test_second_save_is_whole_replacement_and_retires_removed_nodes(env):
    km = linear_draft(env)
    res = put_draft(env, F51, 1, [node(km["tmp_r"], "R", "요구"), node(km["tmp_d"], "D", "설계")],
                    [edge(km["tmp_e1"], km["tmp_r"], km["tmp_d"])])
    assert res.status_code == 200
    out = res.json()
    assert out["draft_version"] == 2 and out["key_map"] == {}
    assert out["removed_node_keys"] == [km["tmp_t"]] and out["removed_edge_ids"] == [km["tmp_e2"]]
    retired = _sql(env["db"], "SELECT node_key, reason FROM graph_retired_node_keys WHERE group_id = ?", (F51,))
    assert retired == [{"node_key": km["tmp_t"], "reason": "draft_removed"}]
    # a removed key cannot be sent again as an existing key
    again = put_draft(env, F51, 2, [node(km["tmp_t"], "T")], [])
    assert again.status_code == 422
    assert again.json()["error"]["params"]["errors"] == [{"field": "nodes[0].node_key", "reason": "not_in_draft_or_revision"}]


def test_s3_2_stale_version_is_refused_and_nothing_written(env):
    linear_draft(env)
    put_draft(env, F51, 1, [node("tmp_x", "R")], [], user="u_park")
    res = put_draft(env, F51, 1, [node("tmp_y", "N")], [])
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "draft_version_conflict"
    assert err["params"]["expected_draft_version"] == 1 and err["params"]["current_draft_version"] == 2
    assert err["params"]["updated_by"] == "u_park" and err["params"]["updated_at"]
    first_again = put_draft(env, F51, 0, [node("tmp_y", "N")], [])
    assert first_again.status_code == 409
    assert [n["label"] for n in env["client"].get(f"/api/v1/flows/{F51}/graph", headers=_h()).json()["draft"]["nodes"]] == ["R"]


def test_s3_3_v01_flow_refuses_draft(env):
    res = put_draft(env, F48, 0, [node("tmp_1", "D")], [])
    assert res.status_code == 409
    assert res.json()["error"]["params"] == {"flow_id": F48, "ownership": "v01",
                                             "sequence_root_doc_id": f"{F48}.0001-R"}
    assert res.json()["error"]["code"] == "flow_owned_by_v01"
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_drafts")[0]["n"] == 0


def test_s3_4_format_errors_and_unregistered_doc_type(env):
    linear_draft(env)
    res = put_draft(env, F51, 1, [node("tmp_2", None, "구현", palette="task"), node("gn_zz9zz9zz9zz9", "D")],
                    [edge("tmp_e9", "tmp_2", "tmp_404")])
    assert res.status_code == 422
    assert res.json()["error"]["params"]["errors"] == [
        {"field": "nodes[0].doc_type", "reason": "required_for_document_node"},
        {"field": "nodes[1].node_key", "reason": "not_in_draft_or_revision"},
        {"field": "edges[0].target", "reason": "unknown_node_reference"},
    ]
    bad_type = put_draft(env, F51, 1, [node("tmp_q", "ZZ")], [])
    assert bad_type.json()["error"]["params"]["errors"] == [{"field": "nodes[0].doc_type", "reason": "invalid_doc_type"}]
    missing = env["client"].put(f"/api/v1/flows/{F51}/graph/draft", json={"nodes": []}, headers=_h())
    assert missing.status_code == 422




# ── S3: validation ───────────────────────────────────────────────────────────





# ── Canvas with a draft ─────────────────────────────────────────





# ── S4: confirmation ─────────────────────────────────────────────────────────



















# ── migrations 142 / 143 and Flow delete cleanup ─────────────────────────────
