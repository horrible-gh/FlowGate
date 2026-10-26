"""flowgate.v02.0045 T0013 (S3+S4) — Graph definition / confirmation API on a real SQLite DB.

P0008 §5 (S3-1~S3-6, GET graph / revisions), §6 (S4-1~S4-7), §3 Canvas S1-4 / S1-5 with Graph
nodes, §8 (graph_canvas_changed draft / revision, flow_ownership_changed), L0009 §2.4
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

    # flowgate.v02.0053 T#1: with the card document ports registered (startup / an earlier lifespan in
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


def test_draft_write_needs_edit_permission_and_a_user_token(env):
    env["denied"].add(("u_kim", "perm_document_update"))
    assert put_draft(env, F51, 0, [], []).json()["error"]["code"] == "flow_access_denied"
    worker = env["client"].put(f"/api/v1/flows/{F51}/graph/draft", json={"draft_version": 0, "nodes": [], "edges": []},
                               headers={"Authorization": "Bearer worker"})
    assert worker.status_code == 403 and worker.json()["error"]["code"] == "token_kind_not_allowed"


# ── S3: validation ───────────────────────────────────────────────────────────

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


# ── Canvas with a draft (P0008 S1-4) ─────────────────────────────────────────

def test_s1_4_unassigned_canvas_shows_documents_and_editable_draft(env):
    km = linear_draft(env)
    body = canvas(env, F51, "?view=revision").json()       # view ignored on an unassigned Flow
    assert body["projection"] == "documents"
    assert body["view"] == {"mode": "draft", "revision_no": None, "draft_version": 1}
    assert body["graph"]["draft"]["confirmable"] is True and body["graph"]["latest_revision_no"] is None
    by_key = {n["node_key"]: n for n in body["nodes"]}
    assert f"doc:{F51}.0001-R" in by_key
    draft_node = by_key[km["tmp_r"]]
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


# ── S4: confirmation ─────────────────────────────────────────────────────────

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


# ── migrations 142 / 143 and Flow delete cleanup ─────────────────────────────

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
