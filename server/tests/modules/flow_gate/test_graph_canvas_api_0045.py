"""flowgate.v02.0045 T0011 (S1+S2) — Canvas read API + layout save against a real SQLite DB.

P0008 §3 (S1-1, S1-2, S1-4, S1-6), §4 (S2-1~S2-4), L0009 §2.3 (identity keeper, rematch,
supersede handoff), DB0010 §3.2 / §5.3 (migration 141, Flow delete cleanup with FK off).
Auth (verify_bearer), RBAC and SSE delivery are stubbed at the Graph API boundary.
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

F51 = "flowgate.v02.0051"
F48 = "flowgate.v02.0048"
F45 = "flowgate.v02.0045"


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
def env(db_path, monkeypatch):
    from modules.flow_gate.db import _SqliteTxnAdapter
    from modules.flow_gate.graph_workflow.api import canvas_routes, envelope
    from modules.flow_gate.graph_workflow.services import canvas_events

    # The guard bump reads the affected-row count through the transaction cursor, as the
    # production sqloader adapters expose it (connection._execute_affected).
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    state = {"denied": set(), "events": []}

    def fake_verify(request):
        raw = request.headers.get("Authorization", "")
        if raw == "Bearer worker-noperm":
            return JSONResponse(status_code=403, content={"ok": False})
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
    state["client"] = TestClient(app)
    state["db"] = db_path
    _seed_base(db_path)
    return state


# ── seeding ──────────────────────────────────────────────────────────────────

def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _seed_base(db):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    db_projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    for uid in ("u_kim", "u_lee"):
        _sql(db, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'x', 't', 't')", (uid, uid, f"{uid}@x"))
    for gid in (F51, F48, F45):
        db_groups.create({"group_id": gid, "project_id": "flowgate", "module": "v02", "title": gid, "status": "OPEN"})
    _doc(F45, 4, "D", "v0.2 Canvas 및 Graph 기반 Workflow 실행 구조 기본설계")


def _doc(flow, seq, typ, title, review=None, status="open", **extra):
    from modules.flow_gate.db import documents as db_documents

    doc_id = f"{flow}.{seq:04d}-{typ}"
    db_documents.create({
        "doc_id": doc_id, "project_id": "flowgate", "module": "v02", "group_id": flow,
        "type_code": typ, "seq": seq, "title": title, "status": status, **extra,
    })
    if review is not None:
        db_documents.update(doc_id, {"doc_review_status": review})
    return doc_id


def _seed_documents_flow():
    """P0008 S1-1 Flow."""
    r = _doc(F51, 1, "R", "Canvas 편집 저장", "approved")
    _doc(F51, 2, "N", "Canvas 편집 저장 조사지시", "pending_review", triggered_by=r, target_id=r)
    _doc(F51, 3, "M", "회의 메모")
    _doc(F51, 4, "Q", "레이아웃 저장 단위 질의")
    _doc(F51, 5, "D", "Canvas 편집 저장 기본설계", "revised", superseded_by=f"{F51}.0006-D")
    _doc(F51, 6, "D", "Canvas 편집 저장 기본설계 (수정판)", "revised", triggered_by=f"{F45}.0004-D")
    _doc(F51, 7, "P", "취소된 프로토콜", status="cancelled")


def _seed_sequence_flow():
    """P0008 S1-2 Flow: R root, N (approved) -> NR (in review) -> D (pending, WP card), free M."""
    from modules.flow_gate.db import workflow_sequences as ws

    r = _doc(F48, 1, "R", "Explorer 정렬 개선", "wf_in_progress")
    n = _doc(F48, 2, "N", "정렬 기준 조사", "approved")
    nr = _doc(F48, 3, "NR", "정렬 기준 조사 결과", "pending_review")
    _doc(F48, 4, "M", "리뷰 메모", target_id=nr)
    ws.insert_sequence(r)
    seq_id = ws.get_sequence_by_doc_id(r)["id"]
    ws.insert_sequence_item(seq_id, 1, "N", "정렬 기준 조사", "R", 0)
    ws.insert_sequence_item(seq_id, 2, "NR", "조사 결과", "R", 1)
    ws.insert_sequence_item(seq_id, 5, "D", "정렬 기본설계", "R", 2, source_wp_card_id="c_k0dtcr9pq80j")
    items = {i["item_seq"]: i["id"] for i in ws.get_sequence_items(seq_id)}
    ws.set_item_result_doc_id(items[1], n)
    ws.set_item_result_doc_id(items[2], nr)
    return seq_id, items


def _get(env, flow, user="u_kim", query="", client_id=None):
    headers = {"Authorization": f"Bearer user:{user}"}
    if client_id:
        headers["x-canvas-client-id"] = client_id
    return env["client"].get(f"/api/v1/flows/{flow}/canvas{query}", headers=headers)


def _put(env, flow, body, user="u_kim", client_id=None):
    headers = {"Authorization": f"Bearer user:{user}"}
    if client_id:
        headers["x-canvas-client-id"] = client_id
    return env["client"].put(f"/api/v1/flows/{flow}/canvas/layout", headers=headers, json=body)


def _v01_fingerprint(db):
    rows = []
    for table in ("documents", "workflow_sequences", "workflow_sequence_items", "groups"):
        rows.append(_sql(db, f"SELECT * FROM {table} ORDER BY 1"))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def _by_key(body):
    return {n["node_key"]: n for n in body["nodes"]}


# ── S1 ───────────────────────────────────────────────────────────────────────

def test_s1_1_unassigned_document_projection(env):
    _seed_documents_flow()
    before = _v01_fingerprint(env["db"])
    res = _get(env, F51)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["flow_id"] == F51 and body["project_id"] == "flowgate"
    assert body["ownership"] == {"state": "unassigned", "sequence_root_doc_id": None, "graph_revision_no": None, "assigned_at": None}
    assert body["projection"] == "documents" and body["view"] is None and body["run"] is None
    assert body["graph"] == {"draft": None, "latest_revision_no": None, "root_binding": None}
    keys = [n["node_key"] for n in body["nodes"]]
    assert keys == [f"doc:{F51}.0001-R", f"doc:{F51}.0002-N", f"doc:{F51}.0003-M", f"doc:{F51}.0004-Q", f"doc:{F51}.0006-D"]
    assert [(n["position"]["x"], n["position"]["y"], n["position"]["source"]) for n in body["nodes"]] == [
        (0, 0, "auto"), (260, 0, "auto"), (520, 0, "auto"), (780, 0, "auto"), (1040, 0, "auto")]
    assert [n["state"] for n in body["nodes"]] == ["done", "review", "done", "in_progress", "review"]
    assert [n["kind"] for n in body["nodes"]] == ["requirement", "research", "memo", "memo", "design"]
    d = _by_key(body)[f"doc:{F51}.0006-D"]
    assert d["identity_source"] == "document" and d["origin"] == "document"
    assert d["editable"] == {"move": True, "structure": False}
    assert d["document"]["supersedes"] == [f"{F51}.0005-D"] and d["slot"] is None and d["graph_node"] is None
    assert [(e["edge_id"], e["edge_class"], e["relation"]) for e in body["edges"]] == [
        (f"ref:triggered_by:{F51}.0001-R>{F51}.0002-N", "reference", "triggered_by"),
        (f"ref:target:{F51}.0001-R>{F51}.0002-N", "reference", "target")]
    assert body["external_relations"] == [{
        "node_key": f"doc:{F51}.0006-D", "relation": "triggered_by", "other_doc_id": f"{F45}.0004-D",
        "other_flow_id": F45, "other_title": "v0.2 Canvas 및 Graph 기반 Workflow 실행 구조 기본설계"}]
    assert body["viewport"] is None
    assert body["identity"] == {"sequence_tag": None, "rematched": False, "retired_node_keys": []}
    assert body["collapsed"] == {"applied": [], "ignored_keys": [], "hidden_counts": {}}
    # A read of a Flow with nothing to rematch writes nothing anywhere.
    assert _sql(env["db"], "SELECT * FROM graph_identity_records") == []
    assert _sql(env["db"], "SELECT * FROM graph_flow_guards") == []
    assert env["events"] == []
    assert _v01_fingerprint(env["db"]) == before


def test_s1_4_collapse_hides_unbound_conversation_and_memo(env):
    _seed_documents_flow()
    body = _get(env, F51, query="?collapse=conversation,memo,archive").json()
    assert [n["node_key"] for n in body["nodes"]] == [f"doc:{F51}.0001-R", f"doc:{F51}.0002-N", f"doc:{F51}.0006-D"]
    assert _by_key(body)[f"doc:{F51}.0006-D"]["position"]["x"] == 1040
    assert body["collapsed"] == {"applied": ["conversation", "memo"], "ignored_keys": ["archive"],
                                 "hidden_counts": {"conversation": 1, "memo": 1}}


def test_s1_6_failures(env):
    res = _get(env, "flowgate.v02.9999")
    assert res.status_code == 404
    assert res.json()["error"] == {"code": "flow_not_found", "message": "Flow를 찾을 수 없습니다.",
                                   "params": {"flow_id": "flowgate.v02.9999"}}
    # 0145 T#2 added latest; retain the malformed-view case with an unsupported value.
    res = _get(env, F51, query="?view=unknown")
    assert res.status_code == 422
    assert res.json()["error"]["params"] == {"errors": [{"field": "view", "reason": "not_one_of:revision,draft,latest"}]}
    _sql(env["db"], "UPDATE groups SET deleted_at = 'x' WHERE group_id = ?", (F51,))
    assert _get(env, F51).status_code == 404
    res = _get(env, F48, client_id="bad id!")
    assert res.status_code == 422
    assert res.json()["error"]["params"]["errors"] == [{"field": "header.x-canvas-client-id", "reason": "invalid_format"}]


def test_auth_and_permission(env):
    c = env["client"]
    url = f"/api/v1/flows/{F51}/canvas"
    assert c.get(url).json()["error"]["code"] == "unauthorized"
    for token in ("worker", "worker-noperm"):
        res = c.get(url, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 403 and res.json()["error"] == {
            "code": "token_kind_not_allowed", "message": res.json()["error"]["message"], "params": {"token_kind": "worker"}}
    env["denied"].add(("u_lee", "perm_document_read"))
    res = _get(env, F51, user="u_lee")
    assert res.status_code == 403 and res.json()["error"]["params"] == {"flow_id": F51, "required": "read"}
    env["denied"].add(("u_kim", "perm_document_update"))
    res = _put(env, F51, {"viewport": {"x": 0, "y": 0, "zoom": 1}})
    assert res.status_code == 403 and res.json()["error"]["params"] == {"flow_id": F51, "required": "write"}




# ── S2 ───────────────────────────────────────────────────────────────────────
