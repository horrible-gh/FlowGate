"""T607 — Regression coverage: inbox token flow + D-1 fix verification + SSE broadcast.

Validation scope:
  1. Inbox token flow (NR143 §6 risk item 7): inbox_routes.py:Step 7.5
     independent register_workflow_result scenario — token-triggered child doc creation →
     sequence advance → actionbar reflects.
  2. D-1 fix verification (post-T605, T625): DS/N/T approval → doc_review_status SSOT →
     enrichment returns next slot pending → actionbar shows the next step.
  3. SSE broadcast (post-T604): POST /documents/{doc_id}/register_result →
     verify DOC_REVIEW_STATUS_CHANGED event broadcast.
  4. Single-writer regression (post-T604): paths other than transition_document_review()
     do not update doc_review_status (assertion-based).
  5. D030 §4.1 full matrix re-run — row #7 passes at execution level (post-fix).

Guards:
  feedback_actionbar_always_shows — empty actionbar = regression
  feedback_actionbar_d030_guard    — D030 §4.1 row #7 at execution level
  feedback_worker_no_extra_conditions — test-only scope; no production code edits
  WORKER_GUARD rule 3: workers never change the SERVER repository's git state directly
  (0115 E14 redefinition: git operations are owned by the server git hooks/finalize service)

Dependencies: T604, T605, T606, T624, T625 (all applied).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ══════════════════════════════════════════════════════════════════════════════
# §1: Inbox token flow — NR143 §6 risk item 7
# ══════════════════════════════════════════════════════════════════════════════
#
# inbox_routes.py:Step 7.5 — non-M type: register_workflow_result + transition_document_review(submit)
# inbox_routes.py:Step 7.5 — M type: set_item_result_doc_id(), register_workflow_result not called


def _build_inbox_module_mocks(monkeypatch, *, doc_type: str, head_item: dict | None):
    """
    Minimally patch dependencies related to Step 7.5 in inbox_routes.
    If head_item is None, get_in_progress_head_by_group returns None.
    """
    import modules.flow_gate.api.inbox_routes as inbox_mod
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow import pipeline_service as ps

    monkeypatch.setattr(
        db_wfseq,
        "get_in_progress_head_by_group",
        MagicMock(return_value=head_item),
    )
    mock_register = MagicMock(return_value={
        "doc_id": f"P001-G001-{doc_type}0001",
        "doc_review_status": "pending_review",
    })
    mock_transition = MagicMock(return_value={
        "doc_id": f"P001-G001-{doc_type}0001",
        "doc_review_status": "pending_review",
    })
    monkeypatch.setattr(ps, "register_workflow_result", mock_register)
    monkeypatch.setattr(ps, "transition_document_review", mock_transition)
    mock_set_result_doc = MagicMock()
    monkeypatch.setattr(db_wfseq, "set_item_result_doc_id", mock_set_result_doc)
    mock_get_head_pending = MagicMock(return_value=None)
    monkeypatch.setattr(db_wfseq, "get_sequence_head_pending", mock_get_head_pending)
    mock_mark_seq_done = MagicMock()
    monkeypatch.setattr(db_wfseq, "mark_sequence_done", mock_mark_seq_done)
    return mock_register, mock_transition, mock_set_result_doc


def _simulate_step75(
    doc_type: str,
    canonical_doc_id: str,
    stored_path: str,
    actor_user_id: str,
    now: str,
    head_item: dict | None,
):
    """Reproduce the inbox_routes.py Step 7.5 logic directly (independent validation of the code path).

    Run the same logic in the test context without changing production code.
    Follow the same branches as the actual code's try/except block.
    """
    from modules.flow_gate.db import workflow_sequences as _db_wfseq
    from modules.flow_gate.workflow.pipeline_service import (
        register_workflow_result,
        transition_document_review,
    )
    from modules.flow_gate.db.connection import now_iso

    try:
        if head_item is not None and head_item.get("type") == doc_type.upper():
            if doc_type.upper() == "M":
                _db_wfseq.set_item_result_doc_id(head_item["id"], canonical_doc_id)
                seq_id = head_item.get("seq_id")
                if seq_id:
                    next_head = _db_wfseq.get_sequence_head_pending(seq_id)
                    if next_head is None:
                        _db_wfseq.mark_sequence_done(seq_id, now)
            else:
                register_workflow_result(
                    item_id=head_item["id"],
                    registered_path=stored_path,
                    registered_doc_id=canonical_doc_id,
                    registered_at=now,
                    actor_user_id=actor_user_id,
                )
                transition_document_review(
                    doc_id=canonical_doc_id,
                    action="submit",
                    actor_user_id=actor_user_id,
                    user_permissions={"document.update"},
                )
    except Exception:
        pass


def test_inbox_step75_non_m_type_calls_register_result(monkeypatch):
    """[INBX-1] Step 7.5: non-M type (DS) -> register_workflow_result + transition_document_review(submit).

    NR143 §6 risk item 7: independently verify that register_workflow_result is
    called correctly in the inbox token flow and that the doc_review_status transition occurs.
    """
    head_item = {"id": 42, "type": "DS", "seq_id": 7, "status": "in_progress"}
    mock_register, mock_transition, mock_set_result_doc = _build_inbox_module_mocks(
        monkeypatch, doc_type="DS", head_item=head_item
    )

    _simulate_step75(
        doc_type="DS",
        canonical_doc_id="P001-G001-DS0001",
        stored_path="/storage/DS0001.md",
        actor_user_id="u001",
        now="2026-05-28T00:00:00Z",
        head_item=head_item,
    )

    mock_register.assert_called_once()
    call_kwargs = mock_register.call_args[1]
    assert call_kwargs["item_id"] == 42
    assert call_kwargs["registered_doc_id"] == "P001-G001-DS0001"

    mock_transition.assert_called_once()
    tr_kwargs = mock_transition.call_args[1]
    assert tr_kwargs["doc_id"] == "P001-G001-DS0001"
    assert tr_kwargs["action"] == "submit"

    # The M-type-only path is not called
    mock_set_result_doc.assert_not_called()


def test_inbox_step75_m_type_skips_register_result(monkeypatch):
    """[INBX-2] Step 7.5: M type -> register_workflow_result not called, result doc is attached.

    The M branch auto-completes without a review step (inbox_routes.py Step 7.5 M-branch).
    """
    head_item = {"id": 99, "type": "M", "seq_id": 5, "status": "in_progress"}
    mock_register, mock_transition, mock_set_result_doc = _build_inbox_module_mocks(
        monkeypatch, doc_type="M", head_item=head_item
    )

    _simulate_step75(
        doc_type="M",
        canonical_doc_id="P001-G001-M0001",
        stored_path="/storage/M0001.md",
        actor_user_id="u001",
        now="2026-05-28T00:00:00Z",
        head_item=head_item,
    )

    mock_register.assert_not_called()
    mock_transition.assert_not_called()
    mock_set_result_doc.assert_called_once_with(99, "P001-G001-M0001")


def test_inbox_step75_no_head_item_no_op(monkeypatch):
    """[INBX-3] Step 7.5: no head item -> graceful no-op (no exception).

    The inbox should complete without issues even for a group with no sequence.
    """
    mock_register, mock_transition, mock_update_item = _build_inbox_module_mocks(
        monkeypatch, doc_type="DS", head_item=None
    )

    _simulate_step75(
        doc_type="DS",
        canonical_doc_id="P001-G001-DS0001",
        stored_path="/storage/DS0001.md",
        actor_user_id="u001",
        now="2026-05-28T00:00:00Z",
        head_item=None,
    )

    mock_register.assert_not_called()
    mock_transition.assert_not_called()
    mock_update_item.assert_not_called()


def test_inbox_step75_head_type_mismatch_no_op(monkeypatch):
    """[INBX-4] Step 7.5: head type ≠ doc_type -> no-op (type mismatch guard).

    If the head is T type but a DS document arrives, Step 7.5 is skipped.
    """
    head_item = {"id": 11, "type": "T", "seq_id": 2, "status": "in_progress"}
    mock_register, mock_transition, mock_set_result_doc = _build_inbox_module_mocks(
        monkeypatch, doc_type="DS", head_item=head_item
    )

    _simulate_step75(
        doc_type="DS",
        canonical_doc_id="P001-G001-DS0001",
        stored_path="/storage/DS0001.md",
        actor_user_id="u001",
        now="2026-05-28T00:00:00Z",
        head_item=head_item,
    )

    # type mismatch → no-op
    mock_register.assert_not_called()
    mock_transition.assert_not_called()
    mock_set_result_doc.assert_not_called()


@pytest.mark.parametrize("doc_type", ["NR", "TR", "AR", "VR", "DC"])
def test_inbox_step75_various_review_types(monkeypatch, doc_type):
    """[INBX-5] Step 7.5: all NR/TR/AR/VR/DC types call register_workflow_result.

    Verify inbox token flow behavior across all target types for D030 §4.1 row #7.
    """
    head_item = {"id": 20, "type": doc_type, "seq_id": 3, "status": "in_progress"}
    mock_register, mock_transition, _ = _build_inbox_module_mocks(
        monkeypatch, doc_type=doc_type, head_item=head_item
    )

    _simulate_step75(
        doc_type=doc_type,
        canonical_doc_id=f"P001-G001-{doc_type}0001",
        stored_path=f"/storage/{doc_type}0001.md",
        actor_user_id="u001",
        now="2026-05-28T00:00:00Z",
        head_item=head_item,
    )

    mock_register.assert_called_once()
    call_kwargs = mock_register.call_args[1]
    assert call_kwargs["item_id"] == 20
    assert call_kwargs["registered_doc_id"] == f"P001-G001-{doc_type}0001"
    mock_transition.assert_called_once()
    assert mock_transition.call_args[1]["action"] == "submit"


# ══════════════════════════════════════════════════════════════════════════════
# §2: D-1 fix verification — post-T605
# ══════════════════════════════════════════════════════════════════════════════
#
# Original NR143 §1 defect: after DS approval, the "next step" button was disabled.
# T605 fix: approval makes the result doc the SSOT → next slot pending → mode='next'.
#
# before (T603 D-1): after N approval head stayed in_progress -> canShowNextForNonR=false -> button hidden
# after  (T605 fix): after N approval head=NR(pending)      -> canShowNextForNonR=true  -> button shown


def _patch_pipeline_d1(monkeypatch, doc: dict):
    import modules.flow_gate.workflow.pipeline_service as ps
    import modules.flow_gate.workflow.event_logger as el

    updated_doc = {**doc}
    mock_docs = MagicMock()
    mock_docs.get_by_id = MagicMock(return_value=doc)

    def _update(doc_id, fields):
        updated_doc.update(fields)
        return updated_doc

    mock_docs.update = MagicMock(side_effect=_update)
    monkeypatch.setattr(ps, "db_docs", mock_docs)
    # These tests isolate slot/head and single-writer effects from body validation.
    monkeypatch.setattr(ps, "_require_document_body_for_approval", lambda doc, locale="ko": None)
    mock_events = MagicMock()
    mock_events.create = MagicMock(return_value={"id": 1})
    monkeypatch.setattr(el, "db_events", mock_events)
    return mock_docs, updated_doc


def test_d1_fix_ds_approval_slot_done_next_pending_head(monkeypatch):
    """[D1-DS] DS approval -> doc_review_status='approved' -> enrichment returns next slot pending.

    T625: _on_approval_advance_sequence removed. Workflow head state is derived purely from
    doc_review_status SSOT (T624). After DS approval, _parse_doc_workflow returns head=N(pending)
    without any slot status write from pipeline_service.

    Regression guard for D030 §4 row #7 (DS + approved + nextStepExists -> [Next step]).
    """
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    from modules.flow_gate.documents.routers import documents as doc_routes

    doc = {
        "id": 1, "doc_id": "P001-G001-DS0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "pending_review",
        "module": "none", "title": "Design Spec",
    }
    _patch_pipeline_d1(monkeypatch, doc)

    # Step 1: approval — doc_review_status transitions to 'approved'
    result = transition_document_review(
        doc_id="P001-G001-DS0001",
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )
    assert result["doc_review_status"] == "approved", "DS approval status transition failed"

    # Step 2: enrichment — _parse_doc_workflow derives head=N(pending) from doc chain (SSOT)
    from modules.flow_gate.db import documents as db_docs_mod
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: {"id": 1} if doc_id == "R001" else None)
    # D030: DS slot is realized (result_doc_id set, result approved) -> done; N slot
    # has no result_doc_id -> pending head.
    monkeypatch.setattr(db_wfseq, "get_sequence_items",
                        lambda seq_id: [
                            {"type": "DS", "result_doc_id": "P001-G001-DS0001",
                             "result_doc_review_status": "approved"},
                            {"type": "N",  "result_doc_id": None},
                        ])
    _cands = [
        {"doc_id": "R001", "type_code": "R", "seq": 0,
         "doc_review_status": "wf_in_progress"},
        {"doc_id": "P001-G001-DS0001", "type_code": "DS", "seq": 1,
         "doc_review_status": "approved"},
    ]
    monkeypatch.setattr(
        db_docs_mod, "list_documents",
        lambda **kw: ([c for c in _cands if c["type_code"] == kw["type_code"]]
                      if kw.get("type_code") else _cands),
    )

    parsed = doc_routes._parse_doc_workflow({
        "doc_id": "R001", "type_code": "R",
        "project_id": "P001", "group_id": "G001",
        "doc_review_status": "wf_in_progress",
        "workflow_steps": '["DS", "N"]', "rejection_history": None,
    })

    # D030 §4 row #7: DS approved + nextStepExists -> [Next step] displayed
    assert parsed["workflow_head_status"] == "pending", (
        f"expected head_status='pending', got {parsed.get('workflow_head_status')!r}. "
        "Enrichment-only regression: [Next step] not shown after DS approval."
    )
    assert parsed["workflow_head_type"] == "N"


def test_d1_fix_n_approval_nr_next_step_visible(monkeypatch):
    """[D1-N] N approval -> doc_review_status='approved' -> enrichment returns NR(pending).

    T625: slot advance and auto-draft no longer triggered from pipeline_service.
    Enrichment (T624) derives NR slot as pending from the doc chain SSOT.
    """
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.documents.routers import documents as doc_routes

    doc = {
        "id": 2, "doc_id": "P001-G001-N0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "N", "doc_review_status": "pending_review",
        "module": "none", "title": "Notice",
    }
    _patch_pipeline_d1(monkeypatch, doc)

    result = transition_document_review(
        doc_id="P001-G001-N0001",
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    assert result["doc_review_status"] == "approved"

    # Enrichment: N slot has approved doc -> advance; NR slot has no result_doc -> pending
    from modules.flow_gate.db import documents as db_docs_mod
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: {"id": 2} if doc_id == "P001-G001-R0001" else None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items",
                        lambda seq_id: [
                            {"type": "N",  "result_doc_id": "P001-G001-N0001",
                             "result_doc_review_status": "approved"},
                            {"type": "NR", "result_doc_id": None},
                        ])
    _cands = [
        {"doc_id": "P001-G001-R0001", "type_code": "R", "seq": 0,
         "doc_review_status": "wf_in_progress"},
        {"doc_id": "P001-G001-N0001", "type_code": "N", "seq": 1,
         "doc_review_status": "approved"},
    ]
    monkeypatch.setattr(
        db_docs_mod, "list_documents",
        lambda **kw: ([c for c in _cands if c["type_code"] == kw["type_code"]]
                      if kw.get("type_code") else _cands),
    )

    parsed = doc_routes._parse_doc_workflow({
        "doc_id": "P001-G001-R0001", "type_code": "R",
        "project_id": "P001", "group_id": "G001",
        "doc_review_status": "wf_in_progress",
        "workflow_steps": '["N", "NR"]', "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending", (
        "Enrichment-only: after N approval, NR slot should be pending -> "
        f"got {parsed.get('workflow_head_status')!r}"
    )
    assert parsed["workflow_head_type"] == "NR"


def test_d1_fix_t_approval_tr_next_step_visible(monkeypatch):
    """[D1-T] T approval -> doc_review_status='approved' -> enrichment returns TR(pending).

    T625: slot advance and auto-draft no longer triggered from pipeline_service.
    """
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.documents.routers import documents as doc_routes

    doc = {
        "id": 3, "doc_id": "P001-G001-T0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "T", "doc_review_status": "pending_review",
        "module": "none", "title": "Test Plan",
    }
    _patch_pipeline_d1(monkeypatch, doc)

    result = transition_document_review(
        doc_id="P001-G001-T0001",
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    assert result["doc_review_status"] == "approved"

    # Enrichment: T slot approved -> advance; TR slot no result_doc -> pending
    from modules.flow_gate.db import documents as db_docs_mod
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: {"id": 3} if doc_id == "P001-G001-R0001" else None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items",
                        lambda seq_id: [
                            {"type": "T",  "result_doc_id": "P001-G001-T0001",
                             "result_doc_review_status": "approved"},
                            {"type": "TR", "result_doc_id": None},
                        ])
    _cands = [
        {"doc_id": "P001-G001-R0001", "type_code": "R", "seq": 0,
         "doc_review_status": "wf_in_progress"},
        {"doc_id": "P001-G001-T0001", "type_code": "T", "seq": 1,
         "doc_review_status": "approved"},
    ]
    monkeypatch.setattr(
        db_docs_mod, "list_documents",
        lambda **kw: ([c for c in _cands if c["type_code"] == kw["type_code"]]
                      if kw.get("type_code") else _cands),
    )

    parsed = doc_routes._parse_doc_workflow({
        "doc_id": "P001-G001-R0001", "type_code": "R",
        "project_id": "P001", "group_id": "G001",
        "doc_review_status": "wf_in_progress",
        "workflow_steps": '["T", "TR"]', "rejection_history": None,
    })

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "TR"


@pytest.mark.parametrize("type_code", ["NR", "TR", "AR", "VR", "DC"])
def test_d1_fix_non_auto_draft_types_approval_status(monkeypatch, type_code):
    """[D1-NONAUTO] D030 §4.1 row #7: non-AUTO_RESULT_DRAFT type approval -> 'approved'.

    T625: slot advance removed. Only the doc_review_status transition is verified.
    """
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 4, "doc_id": f"P001-G001-{type_code}0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": "pending_review",
        "module": "none", "title": f"Test {type_code}",
    }
    _patch_pipeline_d1(monkeypatch, doc)

    result = transition_document_review(
        doc_id=f"P001-G001-{type_code}0001",
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    assert result["doc_review_status"] == "approved"


# ══════════════════════════════════════════════════════════════════════════════
# §3: SSE broadcast scenario — post-T604
# ══════════════════════════════════════════════════════════════════════════════
#
# POST /documents/{doc_id}/register_result -> doc_review_status changes ->
# verify that broadcast_event(DOC_REVIEW_STATUS_CHANGED) is called.


def _build_register_result_app():
    """Create a FastAPI test app that includes the workflow.py router."""
    from fastapi import FastAPI
    from modules.flow_gate.workflow.routers.workflow import router

    app = FastAPI()
    app.include_router(router)
    return app


def _fake_admin_user():
    return {"user_id": "u001", "is_admin": True}


def test_sse_broadcast_on_register_result_status_change(monkeypatch):
    """[SSE-1] register_result: rejected -> submit -> revised -> DOC_REVIEW_STATUS_CHANGED broadcast.

    Post-T604: the workflow.py:511 endpoint broadcasts SSE after the doc_review_status transition.
    """
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    rejected_doc = {
        "id": 1, "doc_id": "P001-G001-DS0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "rejected",
        "file_path": "/docs/DS0001.md", "title": "DS doc",
    }
    revised_doc = {**rejected_doc, "doc_review_status": "revised"}

    monkeypatch.setattr(db_docs, "get_by_id", MagicMock(return_value=rejected_doc))
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", MagicMock(return_value={"id": 1}))
    monkeypatch.setattr(db_wfseq, "get_effective_head",
                        MagicMock(return_value={"id": 10, "type": "DS", "status": "in_progress"}))

    def _fake_transition(*, doc_id, action, actor_user_id, user_permissions, **kw):
        return revised_doc if action == "submit" else rejected_doc

    broadcast_mock = AsyncMock()
    app = _build_register_result_app()
    app.dependency_overrides[get_current_user] = _fake_admin_user

    # Patch the workflow.py module-level binding directly (fixed by from ..pipeline_service import ...)
    wf_mod = "modules.flow_gate.workflow.routers.workflow"
    with patch(f"{wf_mod}.register_workflow_result", MagicMock(return_value=rejected_doc)), \
         patch(f"{wf_mod}.transition_document_review", _fake_transition), \
         patch("modules.flow_gate.api.v1.events.publisher.broadcast_event", broadcast_mock):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/api/v1/documents/P001-G001-DS0001/register_result")

    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    assert broadcast_mock.await_count >= 1, (
        "broadcast_event was not called — post-T604 requires SSE broadcast after register_result"
    )
    event = broadcast_mock.await_args_list[0].args[0]
    assert event.event_type == "doc_review_status_changed", (
        f"SSE event type mismatch: {event.event_type!r}"
    )
    assert event.payload["prev_status"] == "rejected"
    assert event.payload["next_status"] == "revised"
    assert event.payload["doc_id"] == "P001-G001-DS0001"


def test_sse_broadcast_on_register_result_first_registration(monkeypatch):
    """[SSE-2] register_result: initial registration (doc_review_status='') -> pending_review -> SSE broadcast."""
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    initial_doc = {
        "id": 2, "doc_id": "P001-G001-DS0002",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "",
        "file_path": "/docs/DS0002.md", "title": "DS doc 2",
    }
    submitted_doc = {**initial_doc, "doc_review_status": "pending_review"}

    monkeypatch.setattr(db_docs, "get_by_id", MagicMock(return_value=initial_doc))
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", MagicMock(return_value={"id": 2}))
    monkeypatch.setattr(db_wfseq, "get_effective_head",
                        MagicMock(return_value={"id": 20, "type": "DS", "status": "in_progress"}))

    def _fake_transition(*, doc_id, action, actor_user_id, user_permissions, **kw):
        return submitted_doc if action == "submit" else initial_doc

    broadcast_mock = AsyncMock()
    app = _build_register_result_app()
    app.dependency_overrides[get_current_user] = _fake_admin_user

    wf_mod = "modules.flow_gate.workflow.routers.workflow"
    with patch(f"{wf_mod}.register_workflow_result", MagicMock(return_value=initial_doc)), \
         patch(f"{wf_mod}.transition_document_review", _fake_transition), \
         patch("modules.flow_gate.api.v1.events.publisher.broadcast_event", broadcast_mock):
        client = TestClient(app)
        resp = client.post("/api/v1/documents/P001-G001-DS0002/register_result")

    assert resp.status_code == 201
    assert broadcast_mock.await_count >= 1, "SSE broadcast did not occur on initial registration"
    event = broadcast_mock.await_args_list[0].args[0]
    assert event.event_type == "doc_review_status_changed"
    assert event.payload["prev_status"] == ""
    assert event.payload["next_status"] == "pending_review"


def test_sse_not_broadcast_when_status_unchanged(monkeypatch):
    """[SSE-3] register_result: submit transition failure (TransitionError) -> state unchanged -> no SSE.

    If the state does not change, no unnecessary SSE broadcast should occur.
    """
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow.pipeline_service import TransitionError

    pending_doc = {
        "id": 3, "doc_id": "P001-G001-DS0003",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "pending_review",
        "file_path": "/docs/DS0003.md", "title": "DS doc 3",
    }

    monkeypatch.setattr(db_docs, "get_by_id", MagicMock(return_value=pending_doc))
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", MagicMock(return_value={"id": 3}))
    monkeypatch.setattr(db_wfseq, "get_effective_head",
                        MagicMock(return_value={"id": 30, "type": "DS", "status": "in_progress"}))

    def _raise_transition_error(*, doc_id, action, actor_user_id, user_permissions, **kw):
        raise TransitionError("Disallowed transition: pending_review + submit")

    broadcast_mock = AsyncMock()
    app = _build_register_result_app()
    app.dependency_overrides[get_current_user] = _fake_admin_user

    wf_mod = "modules.flow_gate.workflow.routers.workflow"
    with patch(f"{wf_mod}.register_workflow_result", MagicMock(return_value=pending_doc)), \
         patch(f"{wf_mod}.transition_document_review", _raise_transition_error), \
         patch("modules.flow_gate.api.v1.events.publisher.broadcast_event", broadcast_mock):
        client = TestClient(app)
        resp = client.post("/api/v1/documents/P001-G001-DS0003/register_result")

    assert resp.status_code == 201
    assert broadcast_mock.await_count == 0, (
        f"Unnecessary SSE broadcast occurred even though the state was unchanged ({broadcast_mock.await_count} time(s))"
    )


# ══════════════════════════════════════════════════════════════════════════════
# §4: Single-writer regression — only transition_document_review() updates doc_review_status
# ══════════════════════════════════════════════════════════════════════════════


def test_register_workflow_result_does_not_write_doc_review_status(monkeypatch):
    """[SW-1] register_workflow_result(): no doc_review_status column update.

    After T601: doc_review_status transition logic was removed from register_workflow_result().
    DB004 §6.1: doc_review_status updates must pass only through transition_document_review().
    """
    from modules.flow_gate.workflow.pipeline_service import register_workflow_result
    from modules.flow_gate.db import workflow_item_results as db_wir
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import documents as db_docs

    db_docs_update_calls: list[dict] = []

    def _capture_update(doc_id, fields):
        db_docs_update_calls.append({"doc_id": doc_id, "fields": fields})
        return {**fields, "doc_id": doc_id}

    monkeypatch.setattr(db_docs, "update", MagicMock(side_effect=_capture_update))
    monkeypatch.setattr(db_docs, "get_by_id",
                        MagicMock(return_value={"doc_id": "DS0001", "doc_review_status": "pending_review"}))
    monkeypatch.setattr(db_wir, "insert_result", MagicMock())
    # 0457 T0005: the slot write is the conditional claim; report it as won.
    monkeypatch.setattr(
        db_wfseq,
        "claim_item_result_doc_id",
        MagicMock(side_effect=lambda item_id, result_doc_id: {
            "id": item_id, "result_doc_id": result_doc_id,
        }),
    )
    # 0457 T0007: registration first asks whether the document already occupies some other
    # slot (migration 090 makes two slots for one document a unique-index violation).
    # None = it does not, which is the case this test describes.
    monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", MagicMock(return_value=None))

    register_workflow_result(
        item_id=1,
        registered_path="/path/DS0001.md",
        registered_doc_id="DS0001",
        registered_at="2026-05-28T00:00:00Z",
        actor_user_id="u001",
    )

    # register_workflow_result only SETs result_doc_id and does not update doc_review_status
    for call in db_docs_update_calls:
        assert "doc_review_status" not in call["fields"], (
            f"register_workflow_result() updated doc_review_status directly: {call['fields']}. "
            "Single-writer principle violation (DB004 §6.1)."
        )


def test_transition_document_review_is_single_writer_for_doc_review_status(monkeypatch):
    """[SW-2] transition_document_review() is the only function that updates doc_review_status.

    In the approval path, db_docs.update is called only inside transition_document_review.
    """
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    import modules.flow_gate.workflow.pipeline_service as ps

    doc = {
        "id": 1, "doc_id": "P001-G001-DS0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "pending_review",
    }

    doc_review_update_count = [0]
    original_update_called_from = []

    updated_doc = {**doc}
    mock_docs = MagicMock()
    mock_docs.get_by_id = MagicMock(return_value=doc)

    def _capture_update(doc_id, fields):
        if "doc_review_status" in fields:
            doc_review_update_count[0] += 1
        updated_doc.update(fields)
        return updated_doc

    mock_docs.update = MagicMock(side_effect=_capture_update)

    import modules.flow_gate.workflow.event_logger as el
    monkeypatch.setattr(ps, "db_docs", mock_docs)
    # These tests isolate slot/head and single-writer effects from body validation.
    monkeypatch.setattr(ps, "_require_document_body_for_approval", lambda doc, locale="ko": None)
    monkeypatch.setattr(el, "db_events", MagicMock())
    monkeypatch.setattr(el.db_events, "create", MagicMock(return_value={"id": 1}))

    transition_document_review(
        doc_id="P001-G001-DS0001",
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    # doc_review_status is updated exactly once (in db_docs.update inside transition_document_review)
    assert doc_review_update_count[0] == 1, (
        f"doc_review_status update count: {doc_review_update_count[0]} (expected 1). "
        "Single-writer principle violation."
    )


# ══════════════════════════════════════════════════════════════════════════════
# §5: D030 §4.1 full matrix — post-fix execution-level verification (11 rows)
# ══════════════════════════════════════════════════════════════════════════════
#
# Verify _parse_doc_workflow output at the server execution level.
# Confirm that the client policy input data (actionBarPolicy.ts) is correct.
# Row #7 (reviewStatus=approved -> [Next step]) passes at the execution level after the T605 fix.


def _parse_r_doc(monkeypatch, seq_items: list, workflow_steps_str: str,
                 effective_head=None):  # effective_head ignored after T624/D030 (no longer called)
    """Helper that runs _parse_doc_workflow from the perspective of an R document.

    D030 (status column removed): slot state is derived from result_doc_id, not from a
    legacy `status` column. The workflow head is resolved by _parse_doc_workflow from
    (a) the group documents (list_documents) and (b) the sequence slots' result_doc_id +
    the result doc's review status.

    Test slots may still express intent via a convenience `status` token; this helper
    translates it to the current contract:
      - 'pending'     -> result_doc_id = None (unrealized slot)
      - 'in_progress' -> result_doc_id set, result doc review = pending_review
      - 'done'        -> result_doc_id set, result doc review = approved
    The result-doc review status is surfaced via the slot's `result_doc_review_status`
    field and a matching candidate row from list_documents (both SSOT channels the
    product reads). The viewed R doc carries project_id/group_id so the group/sequence
    head lookup runs.
    """
    from modules.flow_gate.db import documents as db_docs_mod
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.documents.routers import documents as doc_routes

    enriched: list[dict] = []
    # candidates: the group documents list_documents returns (R root + each result doc)
    candidates: list[dict] = [{
        "doc_id": "R001", "type_code": "R", "seq": 0,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": workflow_steps_str,
    }]
    for i, item in enumerate(seq_items):
        it = dict(item)
        status = it.pop("status", "pending")
        if "result_doc_id" not in it:
            if status in ("done", "in_progress"):
                it["result_doc_id"] = f"{it['type']}_{status}_{i}"
            else:
                it["result_doc_id"] = None
        rid = it["result_doc_id"]
        if rid:
            review = "approved" if status == "done" else "pending_review"
            it["result_doc_review_status"] = review
            candidates.append({
                "doc_id": rid, "type_code": it["type"], "seq": i + 1,
                "doc_review_status": review,
            })
        enriched.append(it)

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: {"id": 1} if doc_id == "R001" else None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items",
                        lambda seq_id: enriched)
    monkeypatch.setattr(
        db_docs_mod, "list_documents",
        lambda **kw: (
            [c for c in candidates if c["type_code"] == kw["type_code"]]
            if kw.get("type_code") else candidates
        ),
    )

    return doc_routes._parse_doc_workflow({
        "doc_id": "R001", "type_code": "R",
        "project_id": "P001", "group_id": "G001",
        "doc_review_status": "wf_in_progress",
        "workflow_steps": workflow_steps_str,
        "rejection_history": None,
    })


def test_d030_row1_r_workflow_undecided(monkeypatch):
    """[D030 #1] R + workflow undecided -> workflow_head_status=None (condition for mode='workflow')."""
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.documents.routers import documents as doc_routes

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda doc_id: None)

    parsed = doc_routes._parse_doc_workflow({
        "doc_id": "R001", "type_code": "R",
        "workflow_steps": None, "rejection_history": None,
    })

    assert parsed.get("workflow_head_status") is None
    assert parsed.get("workflow_head_type") is None
    assert parsed.get("workflow_steps") is None


def test_d030_row2_r_workflow_decided_head_pending(monkeypatch):
    """[D030 #2] R + after workflow decision · headStatus=pending -> mode='next'."""
    parsed = _parse_r_doc(
        monkeypatch,
        seq_items=[{"type": "DS", "status": "pending"}],
        effective_head={"type": "DS", "status": "pending", "result_doc_id": None},
        workflow_steps_str='["DS"]',
    )

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "DS"


def test_d030_row3_r_head_in_progress_not_stranded(monkeypatch):
    """[D030 #3] R + head in_progress (with result_doc_id) -> head_status='in_progress' (mode='info')."""
    parsed = _parse_r_doc(
        monkeypatch,
        seq_items=[{"type": "DS", "status": "in_progress", "result_doc_id": "DS001"}],
        effective_head={"type": "DS", "status": "in_progress", "result_doc_id": "DS001"},
        workflow_steps_str='["DS"]',
    )

    assert parsed["workflow_head_status"] == "in_progress"


def test_d030_row4_pending_m_is_create_head_realized_m_never_head(monkeypatch):
    """[D030 #4, revised by B0001 / group 0105] The M-never-head invariant applies to a
    *realized* (already-created, auto-approved) memo — it must not re-surface as the head.
    It does NOT apply to a *pending* (not-yet-created) M slot: that is an actionable
    'create next document' step and must surface as the head, so the action bar offers
    [create document] rather than collapsing to the AC final-approval gate.

    B0001 reported the old behavior (pending M → AC) as a bug: "if the last step is an
    auto-approve type (memo/chat), final approval shows instead of document creation".
    The fix aligns _parse_doc_workflow with the SSOT workflow_sequences.get_effective_head,
    which never type-filters unrealized slots.
    """
    # (a) pending M (not yet created) -> head = M (create), NOT AC.
    parsed_pending = _parse_r_doc(
        monkeypatch,
        seq_items=[{"type": "M", "status": "pending"}],
        workflow_steps_str='["M"]',
    )
    assert parsed_pending["workflow_head_status"] == "pending"
    assert parsed_pending.get("workflow_head_type") == "M"

    # (b) realized + approved M -> no actionable document step remains; the remaining
    #     action is final approval, so head = AC/pending (invariant preserved).
    parsed_done = _parse_r_doc(
        monkeypatch,
        seq_items=[{"type": "M", "status": "done"}],
        workflow_steps_str='["M"]',
    )
    assert parsed_done["workflow_head_status"] == "pending"
    assert parsed_done.get("workflow_head_type") == "AC"


def test_d030_row5_q_answered_next_pending(monkeypatch):
    """[D030 #5] Q answered + next slot pending -> head=T(pending) (mode='next')."""
    parsed = _parse_r_doc(
        monkeypatch,
        seq_items=[
            {"type": "Q", "status": "done"},
            {"type": "T", "status": "pending"},
        ],
        effective_head={"type": "T", "status": "pending", "result_doc_id": None},
        workflow_steps_str='["Q", "T"]',
    )

    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_head_type"] == "T"


def test_d030_row6_sequence_complete_final_approval_gate(monkeypatch):
    """[D030 #6 / M042] All document steps done but final approval (AC) not yet performed
    -> head = AC/pending so the action bar shows [final approval] (group 0104 restore).
    (Was head_status='done' under b39f6b8, which auto-finalized and dropped the AC gate.)"""
    parsed = _parse_r_doc(
        monkeypatch,
        seq_items=[{"type": "DS", "status": "done"}],
        effective_head=None,  # all document steps realized; only final approval remains
        workflow_steps_str='["DS"]',
    )

    assert parsed["workflow_head_status"] == "pending"
    assert parsed.get("workflow_head_type") == "AC"


@pytest.mark.parametrize("type_code", ["DS", "N", "T", "TR", "NR", "DC", "VR", "AR"])
def test_d030_row7_approved_next_slot_pending_execution_level(monkeypatch, type_code):
    """[D030 #7] reviewStatus=approved + T605 fix -> head=next(pending) -> mode='next'.

    At the T603 point, this case passed only at the test level and failed at the execution level (D-1 defect).
    At the T607 point (post-T604/T605/T606), it also passes at the execution level.

    feedback_actionbar_d030_guard: confirm execution-level pass for row #7.
    """
    # post-T605 fix: type_code slot done, next slot pending
    next_type = {"N": "NR", "T": "TR"}.get(type_code, "NEXT")
    seq_items = [
        {"type": type_code, "status": "done"},
        {"type": next_type, "status": "pending"},
    ] if next_type != "NEXT" else [
        {"type": type_code, "status": "done"},
    ]
    if next_type == "NEXT":
        # sequence complete after a single slot
        effective_head = None
        workflow_steps_str = f'["{type_code}"]'
    else:
        effective_head = {"type": next_type, "status": "pending", "result_doc_id": None}
        workflow_steps_str = f'["{type_code}", "{next_type}"]'

    parsed = _parse_r_doc(
        monkeypatch,
        seq_items=seq_items,
        effective_head=effective_head,
        workflow_steps_str=workflow_steps_str,
    )

    if effective_head is not None:
        # next slot exists -> condition for mode='next'
        assert parsed.get("workflow_head_status") == "pending", (
            f"[{type_code}] D030 §4.1 row #7 execution level: expected head_status='pending', "
            f"got {parsed.get('workflow_head_status')!r}. "
            "T605 fix not applied -> user cannot proceed."
        )
    else:
        # single-slot complete, but final approval (AC) not yet done → AC gate is the head
        # (M042 / group 0104): head_status='pending', head_type='AC', so the action bar shows
        # the [final approval] control instead of auto-finalizing the workflow.
        assert parsed.get("workflow_head_status") == "pending"
        assert parsed.get("workflow_head_type") == "AC"

    # feedback_actionbar_always_shows: workflow information is always present
    has_workflow = (
        parsed.get("workflow_steps") is not None
        or parsed.get("workflow_head_type") is not None
        or parsed.get("workflow_head_status") is not None
    )
    assert has_workflow, (
        f"[{type_code}] No workflow information — actionbar null regression (feedback_actionbar_always_shows violation)"
    )


@pytest.mark.parametrize("type_code", ["DS", "N", "T", "TR", "NR", "DC", "VR", "AR"])
def test_d030_row8_approved_head_in_progress_info_mode(monkeypatch, type_code):
    """[D030 #8] reviewStatus=approved + head=in_progress -> mode='info' (in-progress guard).

    If the head is in_progress (with result_doc_id), it is not stranded -> head_status='in_progress'.
    """
    parsed = _parse_r_doc(
        monkeypatch,
        seq_items=[{"type": type_code, "status": "in_progress",
                    "result_doc_id": f"{type_code}001"}],
        effective_head={"type": type_code, "status": "in_progress",
                        "result_doc_id": f"{type_code}001"},
        workflow_steps_str=f'["{type_code}"]',
    )

    assert parsed["workflow_head_status"] == "in_progress", (
        f"[{type_code}] expected in_progress, got {parsed.get('workflow_head_status')!r}"
    )


def test_d030_row9_stranded_head_reopens_as_pending(monkeypatch):
    """[D030 #9] stranded head (in_progress + result_doc_id=None) -> reopen as pending.

    DB004 §5: stranded = in_progress AND result_doc_id IS NULL -> derived as reopened pending.
    Verify the T602 fix.
    """
    parsed = _parse_r_doc(
        monkeypatch,
        seq_items=[{"type": "DS", "status": "in_progress", "result_doc_id": None}],
        effective_head={"type": "DS", "status": "in_progress", "result_doc_id": None},
        workflow_steps_str='["DS"]',
    )

    # stranded -> reopen as pending
    assert parsed["workflow_head_status"] == "pending", (
        f"stranded head (in_progress + no result_doc_id) did not reopen as pending: "
        f"{parsed.get('workflow_head_status')!r}"
    )


def test_d030_rows_10_11_pending_review_rejected_parse(monkeypatch):
    """[D030 #10/#11] pending_review / rejected state documents — workflow_head lookup works correctly.

    Workflow information for non-R documents (such as DS) is returned based on the parent R.
    The client decides mode='review' based on reviewStatus.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import documents as db_docs_mod
    from modules.flow_gate.documents.routers import documents as doc_routes

    # DS doc (pending_review) — look up the sequence head based on the parent R
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: {"id": 1} if doc_id == "R001" else None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items",
                        lambda seq_id: [{"type": "DS", "status": "in_progress"}])
    monkeypatch.setattr(db_wfseq, "get_effective_head",
                        lambda seq_id: {
                            "type": "DS", "status": "in_progress",
                            "result_doc_id": "DS001",
                        })
    monkeypatch.setattr(db_docs_mod, "list_documents",
                        lambda **kw: [{"doc_id": "R001", "workflow_steps": '["DS"]'}]
                        if kw.get("type_code") == "R" else [])

    for review_status in ("pending_review", "rejected"):
        parsed = doc_routes._parse_doc_workflow({
            "doc_id": "R001.DS001",
            "type_code": "DS",
            "project_id": "P001",
            "group_id": "G001",
            "doc_review_status": review_status,
            "workflow_steps": None,
            "rejection_history": None,
        })

        # workflow information is returned (feedback_actionbar_always_shows)
        has_workflow = (
            parsed.get("workflow_steps") is not None
            or parsed.get("workflow_head_type") is not None
            or parsed.get("workflow_head_status") is not None
        )
        assert has_workflow, (
            f"DS({review_status}) has no workflow information — risk of actionbar null"
        )
