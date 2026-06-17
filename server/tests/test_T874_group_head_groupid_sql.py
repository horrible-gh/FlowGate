"""T874: verify that group-head resolution uses only documents.group_id SQL.

[feedback_group_head_not_sequence_walk] Core rule:
  - Determine the group head by querying the documents table directly by group_id.
  - workflow_sequence_items walk (get_in_progress_head_by_group /
    get_pending_head_by_group) must not participate in group-head resolution.

T815/T816/T817 failed three times because group-head resolution attempted a sequence walk.

Scenarios:
  T874-S1  Simple two-document group: DS(approved) + D(null review) -> D is head.
  T874-S2  Mixed group (R/M/Q/DS/D): the unapproved D is exactly the head.
  T874-S3  Regression guard against sequence-walk calls: only list_documents is called.
  T874-S4  A pending_review document is head; anything not approved/wf_done is a candidate.
"""
from __future__ import annotations

import pytest

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.documents.routers import documents as document_routes


# ── shared helpers ────────────────────────────────────────────────────────────

def _patch_list_docs(monkeypatch, docs_by_group: dict):
    """Patch documents.list_documents to intercept calls keyed by group_id."""
    def _fake(project_id, group_id=None, type_code=None, limit=100, **kw):
        rows = docs_by_group.get(group_id, []) if group_id else []
        if type_code is not None:
            rows = [d for d in rows if d.get("type_code") == type_code]
        return rows
    monkeypatch.setattr(db_docs, "list_documents", _fake)


def _patch_no_sequence(monkeypatch):
    """Patch workflow_sequences lookup to return None for a group without a sequence."""
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _: None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _: [])


# ── T874-S1: simple two-document group ────────────────────────────────────────

def test_T874_S1_simple_group_ds_approved_d_null_head(monkeypatch):
    """T874-S1: group containing DS(approved) and D(review_status=None).

    D is the group head, determined only through group_id SQL (list_documents).
    """
    GROUP_ID = "prj-mod-0001"
    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "prj-mod-0001-DS001", "type_code": "DS",
         "doc_review_status": "approved",  "seq": 1},
        {"doc_id": "prj-mod-0001-D001",  "type_code": "D",
         "doc_review_status": None,        "seq": 2,
         "title": "기본 설계서 v1"},
    ]})
    _patch_no_sequence(monkeypatch)

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "prj-mod-0001-DS001",
        "type_code": "DS",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_doc_id"]    == "prj-mod-0001-D001"
    assert parsed["workflow_head_doc_number"] == "prj-mod-0001-D001"
    assert parsed["workflow_head_doc_title"]  == "기본 설계서 v1"
    assert parsed["workflow_head_status"]     == "in_progress"
    assert parsed["workflow_head_type"]       == "D"
    assert parsed.get("workflow_head_doc_review_status") is None


# ── T874-S2: mixed group (R/M/Q/DS/D) ─────────────────────────────────────────

def test_T874_S2_complex_group_non_head_types_excluded(monkeypatch):
    """T874-S2: R, M, and Q are excluded as NON_HEAD_TYPES.

    After excluding the approved DS, the unapproved D is selected as head.
    """
    GROUP_ID = "prj-mod-0002"
    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001",  "type_code": "R",  "doc_review_status": "wf_in_progress", "seq": 1},
        {"doc_id": "M001",  "type_code": "M",  "doc_review_status": None,             "seq": 2},
        {"doc_id": "Q001",  "type_code": "Q",  "doc_review_status": None,             "seq": 3},
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved",       "seq": 4},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": None,             "seq": 5,
         "title": "복합 설계서"},
    ]})
    _patch_no_sequence(monkeypatch)

    # The head remains D001 regardless of which sibling (R) is viewed.
    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "wf_in_progress",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["workflow_head_doc_id"] == "D001"
    assert parsed["workflow_head_type"]   == "D"
    assert parsed["workflow_head_status"] == "in_progress"


# ── T874-S3: regression guard against sequence-walk calls ─────────────────────

def test_T874_S3_sequence_walk_never_called_for_group_head(monkeypatch):
    """T874-S3: [feedback_group_head_not_sequence_walk] regression guard.

    get_in_progress_head_by_group and get_pending_head_by_group must never be
    called by group-head resolution. Any detected call fails immediately.
    """
    GROUP_ID = "prj-mod-0003"

    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved",  "seq": 1},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": None,        "seq": 2,
         "title": "설계서"},
    ]})
    _patch_no_sequence(monkeypatch)

    sequence_walk_called: list[str] = []

    def _fail_if_called_in_prog(group_id, project_id):
        sequence_walk_called.append("get_in_progress_head_by_group")
        pytest.fail(
            "[T874-S3] get_in_progress_head_by_group called — "
            "sequence walk 은 group head 결정에 금지됨 "
            "[feedback_group_head_not_sequence_walk]"
        )

    def _fail_if_called_pending(group_id, project_id):
        sequence_walk_called.append("get_pending_head_by_group")
        pytest.fail(
            "[T874-S3] get_pending_head_by_group called — "
            "sequence walk 은 group head 결정에 금지됨 "
            "[feedback_group_head_not_sequence_walk]"
        )

    monkeypatch.setattr(db_wfseq, "get_in_progress_head_by_group", _fail_if_called_in_prog)
    monkeypatch.setattr(db_wfseq, "get_pending_head_by_group", _fail_if_called_pending)

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    # Also confirm that no sequence-walk function was called.
    assert sequence_walk_called == [], (
        f"sequence walk 함수 호출됨: {sequence_walk_called}"
    )
    # The head is still resolved correctly.
    assert parsed["workflow_head_doc_id"] == "D001"
    assert parsed["workflow_head_status"] == "in_progress"


# ── T874-S4: a pending_review document is head ─────────────────────────────────

def test_T874_S4_pending_review_doc_is_head_not_done(monkeypatch):
    """T874-S4: pending_review is a head candidate because it is not approved/wf_done.

    When the group contains TR(pending_review), TR is the head.
    Changing it to wf_done yields head=None (done).
    """
    GROUP_ID = "prj-mod-0004"

    # Case A: TR pending_review → head = TR
    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved",       "seq": 1},
        {"doc_id": "T001",  "type_code": "T",  "doc_review_status": "approved",       "seq": 2},
        {"doc_id": "TR001", "type_code": "TR", "doc_review_status": "pending_review", "seq": 3,
         "title": "작업 결과 보고서"},
    ]})
    _patch_no_sequence(monkeypatch)

    parsed_a = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed_a["workflow_head_doc_id"]   == "TR001"
    assert parsed_a["workflow_head_type"]     == "TR"
    assert parsed_a["workflow_head_status"]   == "in_progress"
    assert parsed_a["workflow_head_doc_review_status"] == "pending_review"

    # Case B: all documents are approved/wf_done -> head=None, status=done.
    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved", "seq": 1},
        {"doc_id": "T001",  "type_code": "T",  "doc_review_status": "approved", "seq": 2},
        {"doc_id": "TR001", "type_code": "TR", "doc_review_status": "wf_done",  "seq": 3},
    ]})

    parsed_b = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed_b.get("workflow_head_doc_id") is None
    assert parsed_b["workflow_head_status"] == "done"
