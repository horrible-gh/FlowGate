"""T605 — D-1 Fix: approval triggers sequence advance for non-M types.

Validation scope:
  1. DS/NR/TR/AR/VR/DC approval -> doc_review_status transitions to 'approved'
  2. N/T approval -> doc_review_status transitions to 'approved'
  3. M approval -> doc_review_status transitions to 'approved' (no sequence side-effect)
  4. No sequence item -> graceful no-op
  5. D-1 fix verification: after approval, effective head returns the next pending slot
     (enrichment via doc_review_status SSOT — T624)
  6. _create_next_empty_document_for_auto_draft: head mismatch -> return None

Note (T625): _on_approval_advance_sequence hook removed. Workflow head state is now
derived entirely from doc_review_status SSOT (T624 enrichment). Slot status writes
from pipeline_service are no longer performed on approval.

Guards:
  feedback_actionbar_d030_guard — D030 §4.1 row #7 (reviewStatus=approved → [next-step]) pass
  NR143 §0 — no direct writer added to transition_document_review()
  feedback_worker_no_extra_conditions
  WORKER_GUARD rule 3: no git state change
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Shared fixture helper ─────────────────────────────────────────────────────


def _patch_pipeline_service(monkeypatch, doc: dict):
    """Patch DB/event dependencies of pipeline_service."""
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

    mock_events = MagicMock()
    mock_events.create = MagicMock(return_value={"id": 1})
    monkeypatch.setattr(el, "db_events", mock_events)

    return mock_docs, updated_doc


def _parse_r_workflow(monkeypatch, seq_items, workflow_steps_str, *, r_doc_id="R001"):
    """Run _parse_doc_workflow for an R doc under the current D030 slot contract.

    D030 (status column removed): the workflow head is derived from result_doc_id + the
    result doc's review status, resolved by _parse_doc_workflow from (a) the group
    documents (list_documents) and (b) the sequence slots. Test slots may carry a
    convenience `status` token ('pending' -> result_doc_id None; 'in_progress' ->
    result_doc_id set + pending_review; 'done' -> result_doc_id set + approved). Both SSOT
    channels (slot result_doc_review_status + a candidate from list_documents) are populated
    and the R doc carries project_id/group_id so the lookup runs. Explicit result_doc_id /
    result_doc_review_status on a slot override the token.
    """
    from modules.flow_gate.db import documents as db_docs_mod
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.documents.routers import documents as doc_routes

    enriched = []
    candidates = [{
        "doc_id": r_doc_id, "type_code": "R", "seq": 0,
        "doc_review_status": "wf_in_progress", "workflow_steps": workflow_steps_str,
    }]
    for i, item in enumerate(seq_items):
        it = dict(item)
        status = it.pop("status", "pending")
        if "result_doc_id" not in it:
            it["result_doc_id"] = (
                f"{it['type']}_{status}_{i}" if status in ("done", "in_progress") else None
            )
        rid = it.get("result_doc_id")
        if rid:
            review = it.get("result_doc_review_status") or (
                "approved" if status == "done" else "pending_review"
            )
            it["result_doc_review_status"] = review
            candidates.append({
                "doc_id": rid, "type_code": it["type"], "seq": i + 1,
                "doc_review_status": review,
            })
        enriched.append(it)

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        lambda doc_id: {"id": 1} if doc_id == r_doc_id else None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: enriched)
    monkeypatch.setattr(
        db_docs_mod, "list_documents",
        lambda **kw: ([c for c in candidates if c["type_code"] == kw["type_code"]]
                      if kw.get("type_code") else candidates),
    )

    return doc_routes._parse_doc_workflow({
        "doc_id": r_doc_id, "type_code": "R",
        "project_id": "P001", "group_id": "G001",
        "doc_review_status": "wf_in_progress",
        "workflow_steps": workflow_steps_str, "rejection_history": None,
    })


# ══════════════════════════════════════════════════════════════════════════════
# §1: non-AUTO_RESULT_DRAFT type approval -> doc_review_status transitions to 'approved'
# ══════════════════════════════════════════════════════════════════════════════
# T625: _on_approval_advance_sequence removed. Slot advance no longer happens here.


@pytest.mark.parametrize("type_code", ["DS", "NR", "TR", "AR", "VR", "DC"])
def test_non_auto_draft_approval_transitions_status(monkeypatch, type_code):
    """[§1] On non-AUTO_RESULT_DRAFT type approval, doc_review_status transitions to 'approved'."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": f"P001-G001-{type_code}0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": "pending_review",
        "module": "none", "title": f"Test {type_code}",
    }
    _patch_pipeline_service(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc["doc_id"],
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    assert result.get("doc_review_status") == "approved"


def test_ds_approval_no_auto_draft_creation(monkeypatch):
    """[§1-DS] No AUTO_RESULT_DRAFT automatic draft is created on DS approval."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": "P001-G001-DS0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "pending_review",
        "module": "none", "title": "Test DS",
    }
    _patch_pipeline_service(monkeypatch, doc)

    # Patch the auto-draft function to detect if it's called
    auto_draft_called = MagicMock(return_value=None)

    with patch(
        "modules.flow_gate.documents.routers.documents._create_next_empty_document_for_auto_draft",
        auto_draft_called,
    ):
        transition_document_review(
            doc_id=doc["doc_id"],
            action="approve",
            actor_user_id="u001",
            user_permissions={"document.approve"},
        )

    auto_draft_called.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# §2: AUTO_RESULT_DRAFT (N→NR, T→TR) approval -> doc_review_status transitions to 'approved'
# ══════════════════════════════════════════════════════════════════════════════
# T625: _on_approval_advance_sequence removed. Slot advance and auto-draft are no
# longer triggered from pipeline_service on approval.


@pytest.mark.parametrize("type_code,next_type", [("N", "NR"), ("T", "TR")])
def test_auto_result_draft_approval_transitions_status(
    monkeypatch, type_code, next_type
):
    """[§2-AUTO] On N/T approval, doc_review_status transitions to 'approved'."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc_id = f"P001-G001-{type_code}0001"
    doc = {
        "id": 1, "doc_id": doc_id,
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": "pending_review",
        "module": "none", "title": f"Test {type_code} title",
    }
    _patch_pipeline_service(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc_id,
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    assert result.get("doc_review_status") == "approved"


@pytest.mark.parametrize("type_code,next_type", [("N", "NR"), ("T", "TR")])
def test_auto_result_draft_auto_draft_not_triggered_from_pipeline(
    monkeypatch, type_code, next_type
):
    """[§2-NOTRIG] _create_next_empty_document_for_auto_draft is not called from pipeline_service on approval."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": f"P001-G001-{type_code}0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": "pending_review",
        "module": "none", "title": "Test",
    }
    _patch_pipeline_service(monkeypatch, doc)

    auto_draft_called = MagicMock(return_value=None)

    with patch(
        "modules.flow_gate.documents.routers.documents._create_next_empty_document_for_auto_draft",
        auto_draft_called,
    ):
        result = transition_document_review(
            doc_id=doc["doc_id"],
            action="approve",
            actor_user_id="u001",
            user_permissions={"document.approve"},
        )

    assert result.get("doc_review_status") == "approved"
    auto_draft_called.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# §3: M type — approval transitions doc_review_status; no sequence side-effect
# ══════════════════════════════════════════════════════════════════════════════


def test_m_type_approval_no_sequence_advance(monkeypatch):
    """[§3-M] On M approval, no sequence slot writes occur (hook removed — T625)."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    doc = {
        "id": 1, "doc_id": "P001-G001-M0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "M", "doc_review_status": "pending_review",
        "module": "none", "title": "Test M",
    }
    _patch_pipeline_service(monkeypatch, doc)

    get_item = MagicMock(return_value={"id": 99, "sequence_id": 1, "status": "in_progress"})
    monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", get_item)

    transition_document_review(
        doc_id=doc["doc_id"],
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    # T625: hook removed — no sequence slot writes happen for any type
    get_item.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# §4: approval / reject — no sequence slot writes from pipeline_service (T625)
# ══════════════════════════════════════════════════════════════════════════════


def test_no_sequence_item_no_error(monkeypatch):
    """[§4-NO-SEQ] Approving a document -> no-op, no error (slot writes removed T625)."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": "P001-G001-DS9999",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "pending_review",
        "module": "none", "title": "Orphan DS",
    }
    _patch_pipeline_service(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc["doc_id"],
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    assert result.get("doc_review_status") == "approved"


def test_already_done_slot_no_double_transition(monkeypatch):
    """[§4-DONE] Approval when doc has 'revised' status -> transitions to 'approved'."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": "P001-G001-DS0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "revised",
        "module": "none", "title": "Test DS",
    }
    _patch_pipeline_service(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc["doc_id"],
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )

    assert result.get("doc_review_status") == "approved"


def test_reject_action_transitions_status(monkeypatch):
    """[§4-REJECT] Reject action transitions doc_review_status to 'rejected'."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": "P001-G001-DS0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": "DS", "doc_review_status": "pending_review",
        "module": "none", "title": "Test DS",
    }
    _patch_pipeline_service(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc["doc_id"],
        action="reject",
        actor_user_id="u001",
        user_permissions={"document.reject"},
        comment="Reason for rejection",
    )

    assert result.get("doc_review_status") == "rejected"


# ══════════════════════════════════════════════════════════════════════════════
# §5: D-1 fix verification — after approval, effective head returns the next slot
# ══════════════════════════════════════════════════════════════════════════════


def test_d1_fix_n_approval_head_becomes_nr_pending(monkeypatch):
    """[§5-D1-N] D-1 fix: after N approval, effective head becomes NR (pending).

    Defect confirmed in T603 §4: after N approval, head remained N (in_progress),
    so canShowNextForNonR=false -> button hidden.
    T605 fix: N slot done -> NR slot pending returned as the effective head.
    """
    # N slot realized with an approved result -> done; NR slot has no result -> pending head.
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "N", "status": "done", "result_doc_id": "N001",
             "result_doc_review_status": "approved"},
            {"type": "NR", "status": "pending", "result_doc_id": None},
        ],
        workflow_steps_str='["N", "NR"]',
    )

    # T605 fix: NR (pending) is returned as the next-step head
    assert parsed["workflow_head_type"] == "NR"
    assert parsed["workflow_head_status"] == "pending"


def test_d1_fix_t_approval_head_becomes_tr_pending(monkeypatch):
    """[§5-D1-T] D-1 fix: after T approval, effective head becomes TR (pending)."""
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "T", "status": "done", "result_doc_id": "T001",
             "result_doc_review_status": "approved"},
            {"type": "TR", "status": "pending", "result_doc_id": None},
        ],
        workflow_steps_str='["T", "TR"]',
    )

    assert parsed["workflow_head_type"] == "TR"
    assert parsed["workflow_head_status"] == "pending"


def test_d1_fix_ds_approval_head_becomes_next_pending(monkeypatch):
    """[§5-D1-DS] D-1 fix: after DS approval, effective head becomes the next slot (pending)."""
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "DS", "status": "done", "result_doc_id": "DS001",
             "result_doc_review_status": "approved"},
            {"type": "N", "status": "pending", "result_doc_id": None},
        ],
        workflow_steps_str='["DS", "N"]',
    )

    assert parsed["workflow_head_type"] == "N"
    assert parsed["workflow_head_status"] == "pending"


# ══════════════════════════════════════════════════════════════════════════════
# §6: unit tests for _create_next_empty_document_for_auto_draft
# ══════════════════════════════════════════════════════════════════════════════


def test_auto_draft_wrapper_head_mismatch_returns_none(monkeypatch):
    """[§6-MISMATCH] Return None when the head type differs from the requested type."""
    from modules.flow_gate.documents.routers.documents import (
        _create_next_empty_document_for_auto_draft,
    )
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        MagicMock(return_value={"id": 1}))
    monkeypatch.setattr(db_wfseq, "get_effective_head",
                        MagicMock(return_value={"id": 10, "type": "TR", "status": "pending"}))

    result = _create_next_empty_document_for_auto_draft(
        project_id="P001",
        group_id="G001",
        r_doc_id="P001-G001-R0001",
        type_code="NR",  # mismatch: head is TR, not NR
        title="NR draft",
        module="none",
        actor_user_id="u001",
    )

    assert result is None


def test_auto_draft_wrapper_no_sequence_returns_none(monkeypatch):
    """[§6-NO-SEQ] Return None when there is no sequence."""
    from modules.flow_gate.documents.routers.documents import (
        _create_next_empty_document_for_auto_draft,
    )
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", MagicMock(return_value=None))

    result = _create_next_empty_document_for_auto_draft(
        project_id="P001",
        group_id="G001",
        r_doc_id="NONEXISTENT",
        type_code="NR",
        title="NR draft",
        module="none",
        actor_user_id="u001",
    )

    assert result is None


def test_auto_draft_wrapper_no_head_returns_none(monkeypatch):
    """[§6-NO-HEAD] Return None when there is no sequence item."""
    from modules.flow_gate.documents.routers.documents import (
        _create_next_empty_document_for_auto_draft,
    )
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        MagicMock(return_value={"id": 1}))
    monkeypatch.setattr(db_wfseq, "get_effective_head", MagicMock(return_value=None))

    result = _create_next_empty_document_for_auto_draft(
        project_id="P001",
        group_id="G001",
        r_doc_id="P001-G001-R0001",
        type_code="NR",
        title="NR draft",
        module="none",
        actor_user_id="u001",
    )

    assert result is None


def test_auto_draft_wrapper_head_with_result_doc_returns_none(monkeypatch):
    """[§6-IN-PROG] Return None when the head already has a result doc."""
    from modules.flow_gate.documents.routers.documents import (
        _create_next_empty_document_for_auto_draft,
    )
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        MagicMock(return_value={"id": 1}))
    monkeypatch.setattr(db_wfseq, "get_effective_head",
                        MagicMock(return_value={"id": 10, "type": "NR", "result_doc_id": "P001-G001-NR0001"}))

    result = _create_next_empty_document_for_auto_draft(
        project_id="P001",
        group_id="G001",
        r_doc_id="P001-G001-R0001",
        type_code="NR",
        title="NR draft",
        module="none",
        actor_user_id="u001",
    )

    assert result is None


def test_auto_draft_wrapper_creates_doc_and_registers_result(monkeypatch, tmp_path):
    """[§6-CREATE] Happy path: create an NR draft + register result + pending_review."""
    from contextlib import contextmanager
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.workflow import pipeline_service

    r_doc_id = "P001-G001-R0001"
    nr_doc = {
        "doc_id": "P001-G001.0002-NR",
        "project_id": "P001", "group_id": "P001-G001",
        "type_code": "NR", "doc_review_status": "pending_review",
    }

    class _FakeStore:
        @contextmanager
        def transaction(self):
            yield

    head = {"id": 20, "type": "NR", "result_doc_id": None}
    seq = {"id": 3}

    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id",
                        MagicMock(return_value=seq))
    monkeypatch.setattr(db_wfseq, "get_effective_head", MagicMock(return_value=head))
    monkeypatch.setattr(db_wfseq, "get_sequence_items",
                        MagicMock(return_value=[head, {"id": 21, "type": "DS", "result_doc_id": None}]))
    monkeypatch.setattr(db_connection, "get_store", lambda: _FakeStore())
    monkeypatch.setattr(db_connection, "now_iso", lambda: "2026-01-01T00:00:00Z")

    monkeypatch.setattr(doc_routes.numbering_service, "reserve_document",
                        MagicMock(return_value="0002-NR"))
    monkeypatch.setattr(doc_routes.storage_paths, "document_path",
                        MagicMock(return_value=tmp_path / "nr.md"))
    monkeypatch.setattr(doc_routes, "_get_project_branch", MagicMock(return_value="main"))
    monkeypatch.setattr(doc_routes.document_service, "create_document",
                        MagicMock(return_value=nr_doc))

    register_wf_result = MagicMock(return_value=nr_doc)
    transition_dr = MagicMock(return_value=nr_doc)
    monkeypatch.setattr(pipeline_service, "register_workflow_result", register_wf_result)
    monkeypatch.setattr(pipeline_service, "transition_document_review", transition_dr)

    from modules.flow_gate.db import documents as db_docs
    monkeypatch.setattr(db_docs, "get_by_id", MagicMock(return_value=nr_doc))

    result = doc_routes._create_next_empty_document_for_auto_draft(
        project_id="P001",
        group_id="P001-G001",
        r_doc_id=r_doc_id,
        type_code="NR",
        title="NR auto draft",
        module="none",
        actor_user_id="u001",
    )

    assert result is not None
    assert result["doc_review_status"] == "pending_review"
    # register_workflow_result is called
    register_wf_result.assert_called_once()
    # transition_document_review(submit) is called
    transition_dr.assert_called_once()
    call_kwargs = transition_dr.call_args[1]
    assert call_kwargs["action"] == "submit"


# ══════════════════════════════════════════════════════════════════════════════
# §7: feedback_actionbar_d030_guard — execution-level pass for D030 §4.1 row #7
# ══════════════════════════════════════════════════════════════════════════════


def test_d030_guard_approved_and_next_slot_pending_shows_next_mode(monkeypatch):
    """[§7-D030] D030 §4.1 #7: reviewStatus=approved + head=pending -> mode=next.

    After approval, the sequence slot is done -> next slot pending -> actionbar mode='next'.
    Validate the workflow head from the perspective of the R document (SSOT for sequence state).
    """
    # Post-T605-fix state: N slot done (approved result), NR slot pending.
    # Parse the workflow head from the perspective of the R document (target of D030 §4.1 row #7)
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "N", "status": "done", "result_doc_id": "N001",
             "result_doc_review_status": "approved"},
            {"type": "NR", "status": "pending", "result_doc_id": None},
        ],
        workflow_steps_str='["N", "NR"]',
    )

    # D030 §4.1 #7: R + head_status='pending' -> satisfies the condition for showing the next-step button
    assert parsed.get("workflow_head_status") == "pending", (
        f"expected 'pending', got {parsed.get('workflow_head_status')!r}. "
        "D030 §4.1 row #7 guard: reviewStatus=approved -> show the [next-step] button."
    )
    assert parsed.get("workflow_head_type") == "NR", (
        f"expected 'NR', got {parsed.get('workflow_head_type')!r}."
    )
