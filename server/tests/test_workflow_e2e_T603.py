"""T603 — Regression: 8 doctypes approval flow + actionbar policy.

Validation scope (task spec §Task 1–3):
  1. 8-doctype (DS/N/T/TR/NR/DC/VR/AR) approval flow:
     creation → pending_review → approved → next-step actionbar
  2. R self-approval: workflow seq initialized → first slot actionbar
  3. M auto-complete: M creation -> auto-complete -> actionbar completion
  4. AUTO_RESULT_DRAFT_TYPES: N approval -> next NR actionbar; T approval -> next TR actionbar
  5. Stranded sequence (pre-consolidation legacy): old data shape →
     accurate handling by the new derive logic (without recovery code)
  6. feedback_actionbar_always_shows: no valid scenario yields an empty/null actionbar

Guards:
  feedback_actionbar_always_shows — empty actionbar = regression
  feedback_actionbar_d030_guard    — based on the D030 §4 matrix
  feedback_worker_no_extra_conditions — test-only scope; no production code changes
  WORKER_GUARD rule 3              — workers never change the SERVER repository's git state
                                     directly (0115 E14: git operations are owned by the server
                                     git hooks/finalize service, not by workers)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# D030 §5: DC/VR/AR are outside the official D014 list of 21 types (legacy). Transition rules are generic.
_EIGHT_DOCTYPES = ["DS", "N", "T", "TR", "NR", "DC", "VR", "AR"]

# Official review types specified in D030 §4.1 #7 (based on D014)
_OFFICIAL_REVIEW_DOCTYPES = ["DS", "N", "T", "TR", "NR"]


# ── Shared fixture helper ─────────────────────────────────────────────────────


def _patch_ps(monkeypatch, doc: dict):
    """Patch DB/event dependencies of pipeline_service.

    Returns: (mock_docs, updated_doc_dict)
    updated_doc_dict is updated with the result of calling db_docs.update().

    T625: db_wfseq mocks are harmless no-ops (hook removed, these are never called).
    """
    import modules.flow_gate.workflow.pipeline_service as ps
    import modules.flow_gate.workflow.event_logger as el
    from modules.flow_gate.db import workflow_sequences as db_wfseq

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

    # T625: these mocks are harmless no-ops after hook removal
    monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", MagicMock(return_value=None))

    return mock_docs, updated_doc


def _parse_r_workflow(monkeypatch, seq_items, workflow_steps_str, *, r_doc_id="R001"):
    """Run _parse_doc_workflow for an R doc under the current D030 slot contract.

    D030 (status column removed): the workflow head is derived from result_doc_id +
    the result doc's review status — NOT a legacy slot `status` column. _parse_doc_workflow
    resolves the head from (a) the group documents (list_documents) and (b) the sequence
    slots. Tests express each slot via a convenience `status` token which is translated:
      - 'pending'     -> result_doc_id = None
      - 'in_progress' -> result_doc_id set, result review = pending_review
      - 'done'        -> result_doc_id set, result review = approved
    Both SSOT channels (the slot's result_doc_review_status and a matching candidate from
    list_documents) are populated, and the R doc carries project_id/group_id so the lookup
    runs. An explicit result_doc_id / result_doc_review_status on a slot overrides the token.
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
# §1: doc_review_status transition consistency for 8 doctypes
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("type_code", _EIGHT_DOCTYPES)
def test_doctype_pending_review_to_approved(monkeypatch, type_code):
    """[S-{type_code}-1] pending_review → approve → approved."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": f"P001-G001-{type_code}0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": "pending_review",
    }
    _, updated = _patch_ps(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc["doc_id"],
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )
    assert result.get("doc_review_status") == "approved", (
        f"[{type_code}] pending_review → approve: "
        f"expected 'approved', got {result.get('doc_review_status')!r}"
    )


@pytest.mark.parametrize("type_code", _EIGHT_DOCTYPES)
def test_doctype_pending_review_to_rejected(monkeypatch, type_code):
    """[S-{type_code}-2] pending_review → reject → rejected."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": f"P001-G001-{type_code}0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": "pending_review",
    }
    _, updated = _patch_ps(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc["doc_id"],
        action="reject",
        actor_user_id="u001",
        user_permissions={"document.reject"},
        comment="Reason for rejection",
    )
    assert result.get("doc_review_status") == "rejected", (
        f"[{type_code}] pending_review → reject: "
        f"expected 'rejected', got {result.get('doc_review_status')!r}"
    )


@pytest.mark.parametrize("type_code", _EIGHT_DOCTYPES)
def test_doctype_revised_to_approved(monkeypatch, type_code):
    """[S-{type_code}-3] revised → approve → approved (re-review approval)."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": f"P001-G001-{type_code}0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": "revised",
    }
    _, updated = _patch_ps(monkeypatch, doc)

    result = transition_document_review(
        doc_id=doc["doc_id"],
        action="approve",
        actor_user_id="u001",
        user_permissions={"document.approve"},
    )
    assert result.get("doc_review_status") == "approved", (
        f"[{type_code}] revised → approve failed"
    )


@pytest.mark.parametrize("type_code", _EIGHT_DOCTYPES)
def test_doctype_approved_parse_workflow_head_is_not_null(monkeypatch, type_code):
    """[S-{type_code}-4] Include workflow information in _parse_doc_workflow results for approved documents.

    feedback_actionbar_always_shows guard: if workflow information is missing, the actionbar risks becoming null.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import documents as db_docs_mod
    from modules.flow_gate.documents.routers import documents as doc_routes

    # Workflow sequence of the parent R doc
    monkeypatch.setattr(
        db_wfseq, "get_sequence_by_doc_id",
        lambda doc_id: {"id": 1} if doc_id == "R001" else None,
    )
    monkeypatch.setattr(
        db_wfseq, "get_sequence_items",
        lambda seq_id: [{"type": type_code, "status": "in_progress"}],
    )
    # Head: in_progress + result_doc_id set (NOT stranded)
    monkeypatch.setattr(
        db_wfseq, "get_effective_head",
        lambda seq_id: {
            "type": type_code, "status": "in_progress",
            "result_doc_id": f"{type_code}001",
        },
    )
    # list_documents: return parent R doc
    monkeypatch.setattr(
        db_docs_mod, "list_documents",
        lambda **kw: [{"doc_id": "R001", "workflow_steps": f'["{type_code}"]'}]
        if kw.get("type_code") == "R" else [],
    )

    parsed = doc_routes._parse_doc_workflow({
        "doc_id": f"R001.{type_code}001",
        "type_code": type_code,
        "project_id": "P001",
        "group_id": "G001",
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    # Must have SOME workflow indicator — never null (feedback_actionbar_always_shows)
    has_workflow = (
        parsed.get("workflow_steps") is not None
        or parsed.get("workflow_head_type") is not None
        or parsed.get("workflow_head_status") is not None
    )
    assert has_workflow, (
        f"[{type_code}] _parse_doc_workflow result has no workflow information — "
        f"actionbar null regression (violates feedback_actionbar_always_shows)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# §2: R self-approval: workflow seq initialized → first slot actionbar
# ══════════════════════════════════════════════════════════════════════════════


def test_r_workflow_decided_first_slot_pending_shows_next(monkeypatch):
    """[R-1] R doc workflow decided -> first slot pending -> workflow_head_status='pending'.

    D030 §4.1 #2: R, after workflow decision, headStatus=pending -> [Proceed to next step].
    """
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "DS", "status": "pending"},
            {"type": "T", "status": "pending"},
        ],
        workflow_steps_str='["DS", "T"]',
    )

    assert parsed["workflow_head_type"] == "DS"
    assert parsed["workflow_head_status"] == "pending"
    assert parsed["workflow_steps"] == ["DS", "T"]


def test_r_workflow_undecided_no_head(monkeypatch):
    """[R-2] R doc workflow undecided -> no workflow_head.

    D030 §4.1 #1: R, workflow undecided -> [Decide workflow] (mode='workflow').
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.documents.routers import documents as doc_routes

    monkeypatch.setattr(
        db_wfseq, "get_sequence_by_doc_id",
        lambda doc_id: None,  # no sequence
    )

    parsed = doc_routes._parse_doc_workflow({
        "doc_id": "R001", "type_code": "R",
        "workflow_steps": None, "rejection_history": None,
    })

    assert parsed.get("workflow_head_type") is None
    assert parsed.get("workflow_head_status") is None
    assert parsed.get("workflow_steps") is None


def test_r_workflow_in_progress_slot_guarded(monkeypatch):
    """[R-3] R doc slot in_progress (result_doc_id set) -> head_status='in_progress'.

    D030 §4.1 #3: head≠pending (in progress) -> next-step button disabled + "In progress" badge.
    """
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[{"type": "DS", "status": "in_progress",
                    "result_doc_id": "DS001",
                    "result_doc_review_status": "pending_review"}],
        workflow_steps_str='["DS"]',
    )

    # in_progress + result_doc_id set (result not yet approved) → NOT stranded
    # → head_status stays in_progress
    assert parsed["workflow_head_type"] == "DS"
    assert parsed["workflow_head_status"] == "in_progress"


# ══════════════════════════════════════════════════════════════════════════════
# §3: M auto-complete path
# ══════════════════════════════════════════════════════════════════════════════


def test_m_single_slot_complete_final_approval_gate(monkeypatch):
    """[M-1 / M042] Single M slot complete -> document steps done, but final approval (AC)
    not yet performed -> head = AC/pending so the action bar shows [final approval]
    (group 0104 restore; was 'done' under b39f6b8 which auto-finalized memo workflows).
    """
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[{"type": "M", "status": "done", "result_doc_id": "M001",
                    "result_doc_review_status": "approved"}],
        workflow_steps_str='["M"]',
    )

    assert parsed["workflow_head_status"] == "pending"
    assert parsed.get("workflow_head_type") == "AC"


def test_m_before_next_slot_pending(monkeypatch):
    """[M-2] After M completes, the next slot (T) is pending -> head=T pending.

    D030 §4.1 #4: for M, if there is a next label, show [Next step].
    """
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "M", "status": "done", "result_doc_id": "M001",
             "result_doc_review_status": "approved"},
            {"type": "T", "status": "pending", "result_doc_id": None},
        ],
        workflow_steps_str='["M", "T"]',
    )

    assert parsed["workflow_head_type"] == "T"
    assert parsed["workflow_head_status"] == "pending"


# ══════════════════════════════════════════════════════════════════════════════
# §4: AUTO_RESULT_DRAFT_TYPES: track the N→NR and T→TR sequences
# ══════════════════════════════════════════════════════════════════════════════
#
# T624 SSOT: approval is derived from result-doc review state, so approved N/T slots
# automatically count as done and the next slot becomes the workflow head.


def test_auto_result_draft_n_approval_head_stays_in_progress(monkeypatch):
    """[AUTO-N] After N approval, the workflow head advances to NR (pending).

    T624 fixes the old D-1 behavior by deriving slot completion from doc_review_status.
    """
    # N slot realized with an APPROVED result doc -> treated as done; NR has no
    # result doc -> pending head (T624 SSOT).
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "N", "status": "done", "result_doc_id": "N001",
             "result_doc_review_status": "approved"},
            {"type": "NR", "status": "pending", "result_doc_id": None},
        ],
        workflow_steps_str='["N", "NR"]',
    )

    assert parsed["workflow_head_type"] == "NR"
    assert parsed["workflow_head_status"] == "pending"
    # T624 SSOT: approved N result docs are treated as done, so NR is exposed.


def test_auto_result_draft_t_approval_head_stays_in_progress(monkeypatch):
    """[AUTO-T] After T approval, the workflow head advances to TR (pending)."""
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


# ══════════════════════════════════════════════════════════════════════════════
# §5: Stranded sequence (pre-consolidation legacy) — validate T602 derive logic
# ══════════════════════════════════════════════════════════════════════════════


def test_stranded_in_progress_null_result_doc_reopens_as_pending(monkeypatch):
    """[STR-1] Old data: in_progress + result_doc_id IS NULL -> reopen as pending.

    T602 derive logic (documents.py:_parse_doc_workflow):
    DB004 §5: stranded = in_progress AND result_doc_id IS NULL -> reopen as pending.
    Verify handling without recovery code.
    """
    # Stranded: slot has no result_doc_id (the legacy 'in_progress' status no longer
    # exists). A slot with result_doc_id IS NULL is simply an unrealized (pending) slot
    # under the D030 contract — i.e. it reopens as pending.
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[{"type": "DS", "status": "in_progress", "result_doc_id": None}],
        workflow_steps_str='["DS"]',
    )

    # DB004 §5: stranded → reopen as pending
    assert parsed["workflow_head_type"] == "DS"
    assert parsed["workflow_head_status"] == "pending", (
        "stranded in_progress (result_doc_id=None) -> failed to reopen as pending — "
        "T602 derive logic regression"
    )


def test_non_stranded_in_progress_with_result_doc_stays_in_progress(monkeypatch):
    """[STR-2] in_progress + result_doc_id IS NOT NULL -> not stranded -> remain in_progress.

    Uses DS (a reviewable, non-auto-complete type): a slot whose result doc exists but is
    not yet approved is a genuine in-progress head. (M is excluded here because under the
    current contract M is an AUTO_COMPLETE_TYPE and can never be the actionable head.)
    """
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[{"type": "DS", "status": "in_progress", "result_doc_id": "DS001",
                    "result_doc_review_status": "pending_review"}],
        workflow_steps_str='["DS"]',
    )

    assert parsed["workflow_head_type"] == "DS"
    assert parsed["workflow_head_status"] == "in_progress"


# ══════════════════════════════════════════════════════════════════════════════
# §6: feedback_actionbar_always_shows — completeness of pipeline_service review transitions
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("type_code", _OFFICIAL_REVIEW_DOCTYPES)
@pytest.mark.parametrize("from_status,action,expected", [
    ("pending_review", "approve", "approved"),
    ("pending_review", "reject",  "rejected"),
    ("revised",        "approve", "approved"),
    ("revised",        "reject",  "rejected"),
])
def test_review_transition_matrix_official_types(
    monkeypatch, type_code, from_status, action, expected
):
    """[M-{from_status}-{action}] Cover review-transition rows in the D030 §4 matrix.

    M026 §8-1 DOC_REVIEW_TRANSITIONS rules × the 5 official doctypes.
    """
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    doc = {
        "id": 1, "doc_id": f"P001-G001-{type_code}0001",
        "project_id": "P001", "group_id": "G001",
        "type_code": type_code, "doc_review_status": from_status,
    }
    _patch_ps(monkeypatch, doc)

    comment = "reason" if action == "reject" else None
    result = transition_document_review(
        doc_id=doc["doc_id"],
        action=action,
        actor_user_id="u001",
        user_permissions={"document.approve", "document.reject"},
        comment=comment,
    )
    assert result.get("doc_review_status") == expected, (
        f"[{type_code}] {from_status} → {action}: "
        f"expected {expected!r}, got {result.get('doc_review_status')!r}"
    )


def test_sequence_complete_final_approval_gate_never_null(monkeypatch):
    """[A-1 / M042] When all document steps are approved but final approval (AC) is not yet
    done, head = AC/pending (never null) so the action bar shows [final approval].

    feedback_actionbar_always_shows: head_status=None risks a null actionbar — here it is
    'pending' with head_type='AC'. (Was 'done'/None under b39f6b8's auto-finalize; the
    mandatory-AC gate of M042 supersedes the T602 "no synthetic AC" coupling — group 0104.)
    """
    parsed = _parse_r_workflow(
        monkeypatch,
        seq_items=[
            {"type": "DS", "status": "done", "result_doc_id": "DS001",
             "result_doc_review_status": "approved"},
            {"type": "T", "status": "done", "result_doc_id": "T001",
             "result_doc_review_status": "approved"},
        ],
        workflow_steps_str='["DS", "T"]',
    )

    assert parsed["workflow_head_status"] == "pending", (
        "head_status must be a non-null actionable value (final-approval gate)"
    )
    assert parsed.get("workflow_head_type") == "AC"
