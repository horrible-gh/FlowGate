"""flowgate.v02.0045 T0013 (S4) — v0.1 contacts delegate to the engine router (D0004 §4.3).

Regression: for an unassigned or v0.1 Flow every contact gives the v0.1 result unchanged (the
same request with the contact bypassed answers the same and leaves the same rows). A Graph or
conflict Flow is refused with the v0.1 flat error (P0008 §9 S4-9~S4-11). L0009 §2.2.3: an
unassigned Flow's decide runs inside the Flow guard — a v0.1 failure leaves it unassigned, and a
decide and a Graph confirmation racing on one Flow never both win (E-05). The real v0.1 decision
routes run against a real SQLite DB; only the bearer check and SSE delivery are stubbed.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FA = "flowgate.v02.0061"      # unassigned
FB = "flowgate.v02.0062"      # unassigned twin of FA (baseline without the contact)
FG = "flowgate.v02.0063"      # Graph
FV = "flowgate.v02.0064"      # v0.1
SEQ = [{"id": 1, "type": "D", "label": "설계"}, {"id": 2, "type": "T", "label": "구현"}]


@pytest.fixture()
def env(monkeypatch):
    from modules.flow_gate.api.v1 import workflow_decision_routes as routes
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.graph_workflow.services import canvas_events

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
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    state = {"events": [], "db": path}
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    monkeypatch.setattr(routes, "verify_bearer", lambda request: {
        "_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    app = FastAPI()
    app.include_router(routes.router)
    state["client"] = TestClient(app)
    state["routes"] = routes
    _seed(path)
    state["events"].clear()                                 # the seeding confirmation's own events
    try:
        yield state
    finally:
        conn_mod.STORE = original
        try:
            os.unlink(path)
        except OSError:
            pass


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _seed(db):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db import workflow_sequences as ws

    db_projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    _sql(db, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
             "VALUES ('u_kim', 'u_kim', 'k@x', 'x', 't', 't')")
    for gid in (FA, FB, FG, FV):
        db_groups.create({"group_id": gid, "project_id": "flowgate", "module": "v02", "title": gid, "status": "OPEN"})
        db_documents.create({"doc_id": root(gid), "project_id": "flowgate", "module": "v02", "group_id": gid,
                             "type_code": "R", "seq": 1, "title": "요구", "status": "open"})
    ws.insert_sequence(root(FV))
    ws.insert_sequence_item(ws.get_sequence_by_doc_id(root(FV))["id"], 1, "D", "설계", "R", 0)
    _make_graph(FG)


def root(flow):
    return f"{flow}.0001-R"


def _make_graph(flow):
    from modules.flow_gate.graph_workflow.services import confirm_service
    from modules.flow_gate.graph_workflow.store import definition_store
    from modules.flow_gate.db.connection import get_store

    with get_store().transaction():
        definition_store.create_draft(flow, None, "u_kim")
        definition_store.replace_draft_content(flow, [{"node_key": "gn_start000001", "node_type": "document",
                                                       "doc_type": "R", "label": "요구"}], [])
    confirm_service.confirm_revision({"group_id": flow, "project_id": "flowgate"}, "u_kim", 1, None, None)


def _bypass(env, monkeypatch):
    """The same routes with every Graph contact turned into a plain pass-through."""
    contact = env["routes"]._graph_contact
    monkeypatch.setattr(contact, "guarded_assignment", lambda doc, call, actor=None: call())
    monkeypatch.setattr(contact, "progress_refusal", lambda doc: None)


def decide(env, flow, sequence=SEQ):
    return env["client"].post(f"/api/v1/workflow/{root(flow)}/decide", json={"doc_class": "R", "sequence": sequence})


def sequence_rows(env, flow):
    return _sql(env["db"], "SELECT i.item_seq, i.type, i.label, i.sort_order FROM workflow_sequence_items i "
                           "JOIN workflow_sequences s ON s.id = i.sequence_id WHERE s.doc_id = ? ORDER BY i.sort_order",
                (root(flow),))


def ownership_events(env):
    return [e.payload for e in env["events"] if e.event_type == "flow_ownership_changed"]


# ── decide (S4-9, S4-10) ─────────────────────────────────────────────────────

def test_decide_on_unassigned_flow_is_unchanged_and_announces_v01(env, monkeypatch):
    res = decide(env, FA)
    assert res.status_code == 201, res.json()
    [event] = ownership_events(env)
    assert event == {"flow_id": FA, "from": "unassigned", "to": "v01", "revision_no": None,
                     "sequence_root_doc_id": root(FA), "root_binding": None, "actor_user_id": "u_kim",
                     "origin_client_id": None}
    _bypass(env, monkeypatch)
    baseline = decide(env, FB)
    assert baseline.status_code == res.status_code
    def comparable(body):                                   # row ids and doc ids differ between the twins
        return {**body, "doc_id": None, "head": {**body["head"], "id": None}}

    assert comparable(baseline.json()) == comparable(res.json())
    assert sequence_rows(env, FA) == sequence_rows(env, FB) != []
    review = _sql(env["db"], "SELECT group_id, doc_review_status FROM documents WHERE doc_id IN (?, ?) ORDER BY group_id",
                  (root(FA), root(FB)))
    assert review[0]["doc_review_status"] == review[1]["doc_review_status"] == "wf_in_progress"


def test_decide_on_v01_flow_keeps_the_v01_answer(env, monkeypatch):
    res = decide(env, FV)
    _bypass(env, monkeypatch)
    assert (res.status_code, res.json()) == (decide(env, FV).status_code, decide(env, FV).json())
    assert res.json() == {"error": "already_decided", "doc_id": root(FV)}
    assert ownership_events(env) == []


def test_decide_on_graph_flow_is_refused_flat(env):
    res = decide(env, FG)
    assert res.status_code == 409
    assert res.json() == {"error": "flow_owned_by_graph", "doc_id": root(FG), "group_id": FG, "graph_revision_no": 1}
    assert sequence_rows(env, FG) == []


def test_decide_on_conflict_flow_is_refused(env):
    from modules.flow_gate.db import workflow_sequences as ws

    ws.insert_sequence(root(FG))                            # abnormal data: both records
    res = decide(env, FG)
    assert res.status_code == 409
    assert res.json() == {"error": "flow_ownership_conflict", "doc_id": root(FG), "group_id": FG,
                          "graph_revision_no": 1}


def test_failed_decide_inside_the_guard_leaves_the_flow_unassigned(env, monkeypatch):
    """L0009 E-06: the v0.1 exception rolls the whole guarded unit back and reaches v0.1 as is."""
    routes = env["routes"]
    real = routes.decide_workflow

    def decide_then_fail(**kw):
        real(**kw)
        raise ValueError("boom after insert")

    monkeypatch.setattr(routes, "decide_workflow", decide_then_fail)
    res = decide(env, FA)
    assert res.status_code == 400 and res.json() == {"error": "invalid_sequence", "detail": "boom after insert"}
    assert sequence_rows(env, FA) == []
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM workflow_sequences WHERE doc_id = ?", (root(FA),))[0]["n"] == 0
    assert ownership_events(env) == []


# ── advance / sequence PATCH (S4-11) ─────────────────────────────────────────

def test_advance_refusal_and_unchanged_v01_answers(env, monkeypatch):
    res = env["client"].post(f"/api/v1/workflow/{root(FG)}/advance", json={"ref_doc_ids": None, "continuous": False})
    assert res.status_code == 409
    assert res.json() == {"error": "flow_owned_by_graph", "doc_id": root(FG), "group_id": FG, "graph_revision_no": 1}
    unassigned = env["client"].post(f"/api/v1/workflow/{root(FA)}/advance", json={})
    _bypass(env, monkeypatch)
    baseline = env["client"].post(f"/api/v1/workflow/{root(FA)}/advance", json={})
    assert (unassigned.status_code, unassigned.json()) == (baseline.status_code, baseline.json())
    assert unassigned.json()["error"] == "sequence_not_decided"


def test_sequence_patch_refused_on_graph_and_unchanged_otherwise(env, monkeypatch):
    body = {"doc_id": root(FG), "items": [{"type": "D", "label": "설계"}]}
    res = env["client"].patch("/api/v1/workflow/sequence", json=body)
    assert res.status_code == 409 and res.json()["error"] == "flow_owned_by_graph"
    v01 = env["client"].patch("/api/v1/workflow/sequence",
                              json={"doc_id": root(FV), "items": [{"type": "D", "label": "설계"},
                                                                 {"type": "P", "label": "프로토콜"}]})
    assert v01.status_code == 200, v01.json()
    assert [r["type"] for r in sequence_rows(env, FV)] == ["D", "P"]
    unassigned = env["client"].patch("/api/v1/workflow/sequence", json={"doc_id": root(FA), "items": SEQ})
    _bypass(env, monkeypatch)
    baseline = env["client"].patch("/api/v1/workflow/sequence", json={"doc_id": root(FA), "items": SEQ})
    assert (unassigned.status_code, unassigned.json()) == (baseline.status_code, baseline.json())
    assert ownership_events(env) == []


# ── inbox registration / review decision delegation ──────────────────────────

def test_document_events_reach_the_result_binder_only_for_graph_flows(env, monkeypatch):
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.graph_workflow.services import result_binder

    seen = []
    monkeypatch.setattr(result_binder, "on_document_event", lambda kind, flow, payload: seen.append((kind, flow)))
    for flow in (FA, FV, FG):
        v01_contact.after_inbox_registered(flow, f"{flow}.0002-D", "D", "tok_1")
        v01_contact.after_review_decided(flow, f"{flow}.0002-D", "approve")
    assert seen == [("inbox_registered", FG), ("review_decided", FG)]

    def broken(*_a):
        raise RuntimeError("binder down")

    monkeypatch.setattr(result_binder, "on_document_event", broken)
    v01_contact.after_inbox_registered(FG, f"{FG}.0002-D", "D", None)        # never raises
    v01_contact.after_review_decided(None, "x", "approve")                    # no Flow: nothing


# ── assignment race (L0009 E-05) ─────────────────────────────────────────────

def test_decide_and_confirmation_racing_on_one_flow_never_both_win(env):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services import confirm_service, v01_contact
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.store import definition_store
    from modules.flow_gate.services.workflow_decision_service import decide_workflow

    with get_store().transaction():
        definition_store.create_draft(FA, None, "u_kim")
        definition_store.replace_draft_content(FA, [{"node_key": "gn_race0000001", "node_type": "document",
                                                     "doc_type": "R", "label": "요구"}], [])
    barrier = threading.Barrier(2)
    outcome: dict = {}

    def run_decide():
        barrier.wait()
        outcome["decide"] = v01_contact.guarded_v01_assignment(
            {"doc_id": root(FA), "group_id": FA},
            lambda: decide_workflow(doc_id=root(FA), doc_class="R", sequence=SEQ), "u_kim")

    def run_confirm():
        barrier.wait()
        try:
            outcome["confirm"] = confirm_service.confirm_revision(
                {"group_id": FA, "project_id": "flowgate"}, "u_kim", 1, None, None)
        except GraphApiError as exc:
            outcome["confirm"] = exc.code

    threads = [threading.Thread(target=run_decide), threading.Thread(target=run_confirm)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    has_sequence = bool(_sql(env["db"], "SELECT 1 FROM workflow_sequences WHERE doc_id = ?", (root(FA),)))
    has_assignment = bool(_sql(env["db"], "SELECT 1 FROM graph_assignments WHERE group_id = ?", (FA,)))
    assert has_sequence != has_assignment, outcome
    if has_sequence:
        assert outcome["confirm"] == "flow_owned_by_v01"
    else:
        assert isinstance(outcome["decide"], v01_contact.V01Rejection)
        assert outcome["decide"].error == "flow_owned_by_graph"


def test_graph_confirm_is_refused_after_v01_decide_through_the_route(env):
    from modules.flow_gate.graph_workflow.services import confirm_service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    assert decide(env, FA).status_code == 201
    with pytest.raises(GraphApiError) as exc:
        confirm_service.confirm_revision({"group_id": FA, "project_id": "flowgate"}, "u_kim", 1, None, None)
    assert exc.value.code == "flow_owned_by_v01"
