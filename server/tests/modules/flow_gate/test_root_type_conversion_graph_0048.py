"""R↔B root-type conversion and the Graph engine (C / ③).

``PATCH /documents/{doc_id}/root-type`` now runs the converter through the engine router's v0.1
contact (``graph_workflow.api.v01_contact.guarded_assignment``), the same contact decide and the
sequence save use:

- ⓐ Graph Flow -> 409 ``flow_owned_by_graph`` (v0.1 flat body). The document id, its file, the
  ``graph_root_bindings`` row and the Canvas start Node's document all stay as they were — the
  defect of the earlier implementation (a binding left on a deleted id, an R start Node bound to a B root) cannot occur.
- ⓑ conflict Flow -> 409 ``flow_ownership_conflict`` (was a detail-string 409 from the sequence gate).
- ⓒ unassigned Flow without a draft -> 200, the same result as before (new id, file renamed,
  references repointed), now inside the Flow guard.
- ⓓ unassigned Flow with a draft whose start Node is R -> 200; the draft's validation then reports
  ``root_binding_no_start_node`` (the existing check catches the kind change; nothing is rewritten).
- ⓔ v0.1 Flow -> 409 with the converter's own detail, unchanged.
- ⓕ group disposed / AI run in progress -> the existing refusals, before the contact is consulted.

The real documents router runs against a real SQLite DB with every migration applied; only the
current user (auth) and SSE delivery are stubbed, and the AI-run refusal (raised by the route's
guard as the mutation policy error the app's middleware answers) is mapped by the same response
function the middleware uses.
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
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FU = "flowgate.v02.0081"      # unassigned, no draft
FB = "flowgate.v02.0082"      # unassigned twin of FU (baseline with the contact bypassed)
FD = "flowgate.v02.0083"      # unassigned, a draft whose start Node is R
FG = "flowgate.v02.0084"      # Graph (confirmed revision 1, root bound to the R start Node)
FC = "flowgate.v02.0085"      # conflict (Graph assignment + a v0.1 sequence)
FV = "flowgate.v02.0086"      # v0.1 (a sequence)
FLOWS = (FU, FB, FD, FG, FC, FV)
START = "gn_start000001"
NEXT = "gn_design00001"
USER = "u_kim"
DECIDED = "Cannot convert root type after the workflow decision was taken."


def root(flow: str, type_code: str = "R") -> str:
    return f"{flow}.0001-{type_code}"


def rel_path(flow: str, type_code: str = "R") -> str:
    return f"documents/flowgate/main/v02/{flow.rsplit('.', 1)[1]}/0001-{type_code}_document.md"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.graph_workflow.services import canvas_events
    from modules.flow_gate.services.mutation_policy import MutationPolicyError, mutation_error_response

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
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    # The seeding confirms revisions, which ask the run state ports startup registers (f511c917). Wire them as
    # startup does, in registries restored after the test (the test_graph_definition_api_0045 pattern), instead of
    # relying on an earlier suite having left them registered.
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
    state = {"events": [], "db": path, "storage": tmp_path, "routes": routes}
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER, "is_admin": True}
    app.add_exception_handler(MutationPolicyError, lambda _request, exc: mutation_error_response(exc))
    state["client"] = TestClient(app)
    _seed(path, tmp_path)
    state["events"].clear()                                 # the seeding confirmations' own events
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


def _seed(db, storage: Path):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db import workflow_sequences as ws

    db_projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    _sql(db, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
             "VALUES (?, ?, 'k@x', 'x', 't', 't')", (USER, USER))
    for gid in FLOWS:
        db_groups.create({"group_id": gid, "project_id": "flowgate", "module": "v02", "title": gid, "status": "OPEN"})
        file = storage / rel_path(gid)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"# root of {gid}\nbody preserved\n", encoding="utf-8")
        db_documents.create({"doc_id": root(gid), "project_id": "flowgate", "module": "v02", "group_id": gid,
                             "type_code": "R", "seq": 1, "title": "요구", "status": "open",
                             "file_path": rel_path(gid), "filename": "0001-R_document.md", "owner_id": USER})
    for gid in (FV,):
        ws.insert_sequence(root(gid))
        ws.insert_sequence_item(ws.get_sequence_by_doc_id(root(gid))["id"], 1, "D", "설계", "R", 0)
    _make_draft(FD)
    _make_graph(FG)
    _make_graph(FC)
    ws.insert_sequence(root(FC))                            # abnormal data: both records


def _make_draft(flow):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.store import definition_store

    with get_store().transaction():
        definition_store.create_draft(flow, None, USER)
        # edb61b31: a run start is a root-type document node with an outgoing edge, so the R start leads to one card.
        definition_store.replace_draft_content(flow, [
            {"node_key": START, "node_type": "document", "doc_type": "R", "label": "요구"},
            {"node_key": NEXT, "node_type": "document", "doc_type": "D", "label": "설계",
             "assignee_kind": "human", "assignee_user_id": USER, "assignee_provider_id": None},
        ], [{"edge_id": "ge_start_next1", "source": START, "target": NEXT}])


def _make_graph(flow):
    from modules.flow_gate.graph_workflow.services import confirm_service

    _make_draft(flow)
    confirm_service.confirm_revision({"group_id": flow, "project_id": "flowgate"}, USER, 1, None, None)


def convert(env, doc_id, new_type="B"):
    return env["client"].patch(f"/api/v1/documents/{doc_id}/root-type", json={"new_type": new_type})


def _bypass(env, monkeypatch):
    """The same route with the Graph contact turned into a plain pass-through (the pre-0048 route)."""
    monkeypatch.setattr(env["routes"]._graph_contact, "guarded_assignment", lambda doc, call, actor=None: call())


def doc_row(env, doc_id):
    rows = _sql(env["db"], "SELECT doc_id, type_code, filename, file_path, group_id FROM documents WHERE doc_id = ?", (doc_id,))
    return rows[0] if rows else None


def binding_row(env, flow):
    rows = _sql(env["db"], "SELECT group_id, node_key, doc_id, doc_type, bound_at_revision_no FROM graph_root_bindings "
                           "WHERE group_id = ?", (flow,))
    return rows[0] if rows else None


def converted_events(env):
    return _sql(env["db"], "SELECT metadata FROM workflow_events WHERE event_type = 'doc_type_converted'")


def start_node(flow):
    """The Canvas read's Graph start Node as the screen gets it."""
    from modules.flow_gate.graph_workflow.services import canvas_service

    canvas = canvas_service.read_canvas({"group_id": flow, "project_id": "flowgate"}, USER, view=None, collapse=None,
                                        client_id=None)
    [node] = [n for n in canvas["nodes"] if n["node_key"] == START]
    return canvas["ownership"]["state"], node


# ── ⓐ Graph Flow ─────────────────────────────────────────────────────────────

def test_graph_flow_is_refused_flat_and_nothing_moves(env):
    state_before, node_before = start_node(FG)
    assert state_before == "graph"
    assert (node_before["document"] or {}).get("doc_id") == root(FG)
    binding_before = binding_row(env, FG)
    assert binding_before == {"group_id": FG, "node_key": START, "doc_id": root(FG), "doc_type": "R",
                              "bound_at_revision_no": 1}
    row_before = doc_row(env, root(FG))

    res = convert(env, root(FG))

    assert res.status_code == 409
    assert res.json() == {"error": "flow_owned_by_graph", "doc_id": root(FG), "group_id": FG, "graph_revision_no": 1}
    assert doc_row(env, root(FG)) == row_before
    assert doc_row(env, root(FG, "B")) is None
    assert (env["storage"] / rel_path(FG)).is_file()
    assert not (env["storage"] / rel_path(FG, "B")).exists()
    assert binding_row(env, FG) == binding_before
    state_after, node_after = start_node(FG)
    assert state_after == "graph"
    assert node_after["document"] == node_before["document"]
    assert node_after["doc_type"] == "R"
    assert converted_events(env) == []


def test_graph_flow_b_to_r_is_refused_the_same_way(env):
    res = convert(env, root(FG), "R")                       # already R: still refused, nothing is read as a no-op
    assert res.status_code == 409 and res.json()["error"] == "flow_owned_by_graph"


# ── ⓑ conflict Flow ──────────────────────────────────────────────────────────

def test_conflict_flow_is_refused_flat(env, monkeypatch):
    res = convert(env, root(FC))
    assert res.status_code == 409
    assert res.json() == {"error": "flow_ownership_conflict", "doc_id": root(FC), "group_id": FC,
                          "graph_revision_no": 1}
    assert doc_row(env, root(FC))["type_code"] == "R"
    assert binding_row(env, FC)["doc_id"] == root(FC)
    # before 0048 the sequence gate answered it with a detail string; since the earlier implementation the converter judges the Flow
    # ownership again inside the guard, so with the contact bypassed it still refuses with the same flat body.
    _bypass(env, monkeypatch)
    old = convert(env, root(FC))
    assert (old.status_code, old.json()) == (409, res.json())
    assert doc_row(env, root(FC))["type_code"] == "R"


# ── ⓒ unassigned Flow without a draft ────────────────────────────────────────

def test_unassigned_flow_converts_as_before_inside_the_flow_guard(env, monkeypatch):
    from modules.flow_gate.graph_workflow.services import v01_contact

    guarded = []
    real_guard = v01_contact.with_flow_guard
    monkeypatch.setattr(v01_contact, "with_flow_guard", lambda flow_id, body: guarded.append(flow_id) or real_guard(flow_id, body))
    for flow in (FU, FB):                                   # an inbound FK row that must follow the rename
        _sql(env["db"], "INSERT INTO events (doc_id, event_type, created_at) VALUES (?, 'created', datetime('now'))",
             (root(flow),))

    res = convert(env, root(FU))
    assert res.status_code == 200, res.json()
    assert guarded == [FU]
    data = res.json()["data"]
    assert (data["doc_id"], data["type_code"], data["filename"]) == (root(FU, "B"), "B", "0001-B_document.md")
    assert doc_row(env, root(FU)) is None
    assert not (env["storage"] / rel_path(FU)).exists()
    assert "body preserved" in (env["storage"] / rel_path(FU, "B")).read_text(encoding="utf-8")
    assert _sql(env["db"], "SELECT 1 FROM events WHERE doc_id = ?", (root(FU, "B"),))
    assert _sql(env["db"], "SELECT 1 FROM events WHERE doc_id = ?", (root(FU),)) == []
    # the note names the mode, children and moved attachments.
    assert [e["metadata"] for e in converted_events(env)] == [
        f"R->B ({root(FU)} -> {root(FU, 'B')}) mode=number_swap children=0 attachments=0/0 ghosts=0"]
    # the Flow stays unassigned and nobody is told it changed engine
    from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
    assert read_ownership(FU).state == "unassigned"
    assert [e for e in env["events"] if e.event_type == "flow_ownership_changed"] == []

    _bypass(env, monkeypatch)
    baseline = convert(env, root(FB))
    assert baseline.status_code == res.status_code

    def comparable(body):                                   # ids and the Flow's own names differ between the twins
        row = dict(body["data"])
        for key in ("id", "doc_id", "group_id", "file_path", "created_at", "updated_at"):
            row.pop(key, None)
        return row

    assert comparable(baseline.json()) == comparable(res.json())






# ── ⓓ unassigned Flow with a draft (start Node R) ─────────────────────────────



# ── the requesting tab on the draft card event ───────────────────────────────
# The tab that switched reads its Canvas again after the answer; the event its conversion causes names that tab's
# x-canvas-client-id, so the tab reads once for both. No header, or one not of the the plan format: no origin
# (as before), and the conversion is never refused for it.





# ── ⓔ v0.1 Flow ──────────────────────────────────────────────────────────────



# ── ⓕ guards keep their order ────────────────────────────────────────────────

@pytest.fixture()
def contact_calls(env, monkeypatch):
    calls = []
    contact = env["routes"]._graph_contact
    real = contact.guarded_assignment
    monkeypatch.setattr(contact, "guarded_assignment", lambda doc, call, actor=None: calls.append(doc["doc_id"]) or real(doc, call, actor))
    return calls
