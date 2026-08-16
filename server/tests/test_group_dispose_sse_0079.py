"""Regression: group dispose must propagate immediately (TR0079.0003 rework).

Rejection (group 0079): "the SSE isn't applied right away so the action bar doesn't change.
The action bar still lets you act on documents in a disposed group." — disposing a group did not push any SSE
event, so already-open document tabs stayed stale and their action bar kept offering
approve/reject/workflow actions on a now-discarded group.

Two server-side guarantees back the fix:
  1. POST /groups/{id}/dispose broadcasts a GROUP_VIEW_REFRESH event on success (and
     stays silent on failure), so every connected client refreshes immediately.
  2. The document detail payload exposes `group_disposed` so the client can collapse
     the action bar for ANY document in a discarded group (not just the DC carrier).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


# ---------------------------------------------------------------------------
# (1) Dispose route SSE broadcast — no DB, pure route wiring.
# ---------------------------------------------------------------------------

def _dispose_client(monkeypatch, dispose_result, captured):
    from modules.flow_gate.api.v1 import tree_routes
    from modules.flow_gate.api.v1.events import publisher

    monkeypatch.setattr(
        tree_routes.process_service, "dispose_group",
        lambda *a, **k: dispose_result,
    )

    async def _capture(event):
        captured.append(event)
        return 1

    monkeypatch.setattr(publisher, "broadcast_event", _capture)

    app = FastAPI()
    app.include_router(tree_routes.router)
    return TestClient(app)


def test_dispose_route_broadcasts_group_view_refresh(monkeypatch):
    from modules.flow_gate.api.v1.events.event_types import EventType

    captured: list = []
    client = _dispose_client(
        monkeypatch,
        {"status": "success", "group_id": "flowgate.default.0079",
         "project": "flowgate", "doc_id": "flowgate.default.0079.0012-DC"},
        captured,
    )

    res = client.post("/api/v1/groups/flowgate.default.0079/dispose",
                      json={"reason_detail": "obsolete"})

    assert res.status_code == 200
    assert len(captured) == 1
    event = captured[0]
    assert event.event_type == EventType.GROUP_VIEW_REFRESH
    assert event.group_id == "flowgate.default.0079"
    assert event.project == "flowgate"
    assert event.payload.get("reason") == "group_disposed"


def test_dispose_route_does_not_broadcast_on_failure(monkeypatch):
    captured: list = []
    client = _dispose_client(
        monkeypatch,
        {"status": "error", "message": "Group is already disposed: x"},
        captured,
    )

    res = client.post("/api/v1/groups/flowgate.default.0079/dispose",
                      json={"reason_detail": "obsolete"})

    # The handled error still returns 200 with the error body, but NO SSE goes out —
    # nothing changed, so spectators must not be told to refresh.
    assert res.status_code == 200
    assert captured == []


# ---------------------------------------------------------------------------
# (2) Document detail exposes group_disposed — real DB with all migrations.
# ---------------------------------------------------------------------------

def _migrations() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


@pytest.fixture()
def real_store():
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import connection as conn_mod

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    for mig in _migrations():
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    conn.close()

    original = conn_mod.STORE
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(db_path)
    conn_mod.STORE = store
    try:
        yield store, db_path
    finally:
        conn_mod.STORE = original
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _seed_group_with_root(group_id: str, root_doc_id: str,
                          project_id: str = "flowgate", module: str = "default") -> None:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    db_projects.create({"project_id": project_id, "project_name": project_id})
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": module,
        "title": "Dispose target",
        "status": "OPEN",
    })
    db_docs.insert_document(
        doc_id=root_doc_id, doc_type="R", project=project_id, module=module,
        title="Requirement", group_id=group_id, direction="inbox", status="open",
    )


def test_detail_exposes_group_disposed_flag(real_store):
    from modules.flow_gate import process_service
    from modules.flow_gate.documents import document_service
    from modules.flow_gate.documents.routers.documents import _parse_doc_workflow

    _store, _db_path = real_store
    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Before disposal: the flag is false.
    doc = document_service.get_document(root_id)
    assert _parse_doc_workflow(doc)["group_disposed"] is False

    # Dispose creates the file-less DC marker for the group.
    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # After disposal: every document in the group reports group_disposed=True, so the
    # client collapses the action bar for the requirement too — not just the DC doc.
    doc = document_service.get_document(root_id)
    assert _parse_doc_workflow(doc)["group_disposed"] is True


# ---------------------------------------------------------------------------
# (3) Server-side action guard — rework rejection: "reject the action even if it's fired".
#     Hiding the action bar is UX only; the server must REJECT any forward action
#     on a disposed group's document even if a stale client still fires the button.
# ---------------------------------------------------------------------------

def test_is_group_disposed_flips_after_dispose(real_store):
    from modules.flow_gate import process_service

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    assert process_service.is_group_disposed(gid) is False
    assert process_service.is_group_disposed(None) is False  # no group → never disposed

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    assert process_service.is_group_disposed(gid) is True


def test_workflow_review_guard_rejects_disposed_group(real_store):
    """workflow.py action endpoints raise 409 group_disposed once the group is disposed."""
    from fastapi import HTTPException
    from modules.flow_gate import process_service
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.workflow.routers.workflow import _guard_group_not_disposed

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Live group: the guard is a no-op (approve/reject/advance proceed normally).
    _guard_group_not_disposed(db_docs.get_by_id(root_id), root_id)
    # Unknown document: caller's own 404 path owns it — guard must not raise.
    _guard_group_not_disposed(None, "flowgate.default.0079.9999-R")

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # Disposed group: the same forward action is now rejected at the server.
    with pytest.raises(HTTPException) as exc:
        _guard_group_not_disposed(db_docs.get_by_id(root_id), root_id)
    assert exc.value.status_code == 409
    assert "group_disposed" in str(exc.value.detail)


def test_decision_route_guard_rejects_disposed_group(real_store):
    """workflow_decision_routes (decide/advance/review-request) return 409 group_disposed."""
    from modules.flow_gate import process_service
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.api.v1.workflow_decision_routes import _disposed_group_response

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Live group: no rejection response (None → endpoint proceeds).
    assert _disposed_group_response(root_id, db_docs.get_by_id(root_id)) is None

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    resp = _disposed_group_response(root_id, db_docs.get_by_id(root_id))
    assert resp is not None
    assert resp.status_code == 409


def test_inbox_edit_guard_rejects_disposed_group(real_store):
    """The inbox ingestion path rejects create/edit/review in a disposed group.

    Rework rejection (group 0079): "documents in a disposed group still get edited just fine." rev2 guarded the
    workflow/review/decision routes but NOT the inbox path, which is how a document is
    actually edited (action: edit) — so a disposed group's documents could still be
    modified by a direct inbox submission. _disposed_group_fail closes that gap.
    """
    from modules.flow_gate import process_service
    from modules.flow_gate.api.inbox_routes import _disposed_group_fail

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Live group (and missing group_id): the inbox guard is a no-op → submission proceeds.
    assert _disposed_group_fail(gid, "Modification") is None
    assert _disposed_group_fail(None, "Modification") is None  # no group → never disposed

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # Disposed group: create/edit/review are all rejected at the inbox source with 409.
    for action in ("Modification", "Creation", "Review"):
        resp = _disposed_group_fail(gid, action)
        assert resp is not None, action
        assert resp.status_code == 409, action


# ---------------------------------------------------------------------------
# (4) Web-UI document router guard — 2nd-pass rejection: "documents in a disposed
#     group still get edited just fine … test.test.0024.0001-R R document still gets edited just fine".
#     The inbox guard (3) only covers the token-bound AI worker path. A logged-in
#     user edits through the document router (PATCH /content, /update, /workflow,
#     /transitions, conversation turn), which never checked the disposed-group
#     signal — so an R document in a discarded group still edited fine.
# ---------------------------------------------------------------------------

def test_document_router_guard_rejects_disposed_group(real_store):
    from fastapi import HTTPException
    from modules.flow_gate import process_service
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers.documents import _reject_if_group_disposed

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Live group (and a doc with no group_id): the web-UI edit guard is a no-op.
    _reject_if_group_disposed(db_docs.get_by_id(root_id))
    _reject_if_group_disposed({"group_id": None})  # no group → never disposed

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # Disposed group: a content/field/workflow/transition edit on the R document — the
    # exact rejected symptom — is now rejected at the server with 409.
    with pytest.raises(HTTPException) as exc:
        _reject_if_group_disposed(db_docs.get_by_id(root_id))
    assert exc.value.status_code == 409
    assert "disposed" in str(exc.value.detail).lower()


# ---------------------------------------------------------------------------
# (5) 3rd-pass rejection: "documents in a disposed group still get edited just fine … group rename, sequence
#     edit, Q&A registration and answer writing" — three further write surfaces that the document
#     router guard (4) does NOT cover, because they live on their own endpoints:
#       - group rename          → workflow.py PUT /groups/{id}            (update_group_endpoint)
#       - sequence edit         → workflow_decision_routes PATCH /workflow/sequence
#       - Q&A register/answer    → q_tapi_routes POST /q/.../questions|answers, qa_routes answer
# ---------------------------------------------------------------------------

def test_group_rename_guard_rejects_disposed_group(real_store, monkeypatch):
    """PUT /groups/{id} (group rename) is rejected once the group is disposed."""
    from fastapi import HTTPException
    from modules.flow_gate import process_service
    from modules.flow_gate.workflow.routers import workflow as wf

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Caller has the manage permission; isolate the test from RBAC seeding.
    monkeypatch.setattr(wf, "_get_user_permissions", lambda u: {"project.group.manage"})
    body = wf.GroupUpdateRequest(title="renamed")

    # Live group: the rename succeeds.
    res = wf.update_group_endpoint(gid, body, {"user_id": "u-x"})
    assert res["group_id"] == gid

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # Disposed group: renaming is now rejected at the server with 409.
    with pytest.raises(HTTPException) as exc:
        wf.update_group_endpoint(gid, body, {"user_id": "u-x"})
    assert exc.value.status_code == 409
    assert "group_disposed" in str(exc.value.detail)


def test_sequence_edit_guard_rejects_disposed_group(real_store):
    """PATCH /workflow/sequence (sequence edit) returns 409 once the group is disposed.

    Reuses the same _disposed_group_response the endpoint now calls before
    edit_workflow_pending — the wiring the 3rd-pass rejection exposed as missing.
    """
    from modules.flow_gate import process_service
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.api.v1.workflow_decision_routes import _disposed_group_response

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Live group: sequence edit proceeds (None → endpoint runs edit_workflow_pending).
    assert _disposed_group_response(root_id, db_docs.get_by_id(root_id)) is None

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    resp = _disposed_group_response(root_id, db_docs.get_by_id(root_id))
    assert resp is not None
    assert resp.status_code == 409


def test_qa_register_guard_rejects_disposed_group(real_store):
    """Q&A registration/answer writing (q_tapi write path) is rejected once the group is disposed,
    while the read path stays open so the disposed group's Q&A remains viewable."""
    from fastapi.responses import JSONResponse
    from modules.flow_gate import process_service
    from modules.flow_gate.api.v1.q_tapi_routes import _doc_project_or_403

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # Live group: reject_disposed=True does NOT 409 (only RBAC may 403) — disposal-gating
    # is the property under test, so assert it specifically does not fire here.
    live = _doc_project_or_403(root_id, "u-nobody", "perm_document_create", reject_disposed=True)
    assert not (isinstance(live, JSONResponse) and live.status_code == 409)

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # Disposed group, write path: 409 (disposal check precedes the permission check).
    resp = _doc_project_or_403(root_id, "u-nobody", "perm_document_create", reject_disposed=True)
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 409

    # Disposed group, read path (reject_disposed=False): NOT gated by disposal — viewing
    # the Q&A of a discarded group is still allowed (any 4xx here is RBAC, never 409).
    read = _doc_project_or_403(root_id, "u-nobody", "perm_document_read", reject_disposed=False)
    assert not (isinstance(read, JSONResponse) and read.status_code == 409)


# ---------------------------------------------------------------------------
# (6) 5th-pass review (issues): two write endpoints in workflow.py that the
#     document-router guard (4) does NOT cover, because they live in workflow.py:
#       - POST  /documents/{id}/register_result   (register_document_result_endpoint)
#       - PATCH /documents/{id}/rejection_reason   (update_rejection_reason_endpoint)
#     Both mutate the document (review_status flip / rejection_history append) and
#     previously never consulted the disposed-group signal — the residual gap that
#     contradicted the "closed every write entry point" claim. Both now call
#     _guard_group_not_disposed before any write.
# ---------------------------------------------------------------------------

def test_rejection_reason_guard_rejects_disposed_group(real_store, monkeypatch):
    """PATCH /documents/{id}/rejection_reason is rejected once the group is disposed."""
    import asyncio
    import json
    from fastapi import HTTPException
    from modules.flow_gate import process_service
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.workflow.routers import workflow as wf

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    # 0419 T0006: PATCH now CORRECTS an existing rejection, so the doc must already
    # be rejected (with a history entry) before it has anything to correct.
    db_docs.update(root_id, {
        "doc_review_status": "rejected",
        "rejection_reason": "originally wrong",
        "rejection_history": json.dumps([{
            "rejection_id": "rej_seed",
            "reason": "originally wrong",
            "rejected_at": "2026-01-01T00:00:00",
            "rejected_by": "u-x",
            "ai_response": None,
            "responded_at": None,
            "response_recorded_by": None,
            "response_revision_no": None,
        }]),
    })

    monkeypatch.setattr(wf, "_get_user_permissions", lambda u: {"document.reject"})
    body = wf.RejectionReasonRequest(reason="still wrong")
    user = {"user_id": "u-x"}

    # Live group: correcting the rejection reason succeeds and updates the entry in place.
    res = asyncio.run(wf.update_rejection_reason_endpoint(root_id, body, user))
    assert res["document"]["doc_id"] == root_id
    assert res["document"]["rejection_reason"] == "still wrong"

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # Disposed group: the same write — correcting a rejection record on a discarded
    # group's document — is now rejected at the server with 409 before any DB write.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wf.update_rejection_reason_endpoint(root_id, body, user))
    assert exc.value.status_code == 409
    assert "group_disposed" in str(exc.value.detail)


def test_register_result_guard_rejects_disposed_group(real_store, monkeypatch):
    """POST /documents/{id}/register_result is rejected once the group is disposed.

    The guard precedes the workflow-sequence lookup, so a disposed group is rejected
    with 409 even when (as here) no sequence is seeded — the disposal check is what is
    under test, and it must short-circuit before the result re-registration write.
    """
    import asyncio
    from fastapi import HTTPException
    from modules.flow_gate import process_service
    from modules.flow_gate.workflow.routers import workflow as wf

    gid = "flowgate.default.0079"
    root_id = "flowgate.default.0079.0001-R"
    _seed_group_with_root(gid, root_id)

    monkeypatch.setattr(wf, "_get_user_permissions", lambda u: {"document.update"})
    body = wf.DocumentTransitionRequest()
    user = {"user_id": "u-x"}

    assert process_service.dispose_group(gid, reason_detail="obsolete")["status"] == "success"

    # Disposed group: register_result is rejected at the server with 409, before the
    # register_workflow_result + rejected->revised transition mutation.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wf.register_document_result_endpoint(root_id, body, user))
    assert exc.value.status_code == 409
    assert "group_disposed" in str(exc.value.detail)
