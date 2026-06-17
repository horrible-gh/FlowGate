"""T875: regression guard against using a workflow_sequence_items walk.

[feedback_group_head_not_sequence_walk] Core rule:
  - Determine the group head by querying the documents table directly by group_id.
  - workflow_sequence_items walk (get_in_progress_head_by_group /
    get_pending_head_by_group) must not participate in group-head resolution.

T875 uses cases where the sequence-walk and group_id SQL results **can differ**
to prove that the walk is unused. This supplements T874 with divergent inputs.

Scenarios:
  T875-S1  Group without sequence rows: the walk returns None, while group_id SQL finds the head.
  T875-S2  New document with result_doc_id IS NULL: the walk misses it, while group_id SQL finds it.
  T875-S3  Sequence sort_order differs from documents.seq: group_id SQL still selects the correct head.
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


def _arm_walk_sentinels(monkeypatch) -> list[str]:
    """Install fail-fast sentinels on the three sequence-walk functions.

    Returns the call-record list. pytest.fail() runs before a call can be
    recorded, but the list is returned for consistency.
    """
    called: list[str] = []

    def _sentinel_in_progress(group_id, project_id):
        called.append("get_in_progress_head_by_group")
        pytest.fail(
            "[T875] get_in_progress_head_by_group called — "
            "sequence walk 은 group head 결정에 금지됨 "
            "[feedback_group_head_not_sequence_walk]"
        )

    def _sentinel_pending(group_id, project_id):
        called.append("get_pending_head_by_group")
        pytest.fail(
            "[T875] get_pending_head_by_group called — "
            "sequence walk 은 group head 결정에 금지됨 "
            "[feedback_group_head_not_sequence_walk]"
        )

    monkeypatch.setattr(db_wfseq, "get_in_progress_head_by_group", _sentinel_in_progress)
    monkeypatch.setattr(db_wfseq, "get_pending_head_by_group", _sentinel_pending)
    return called


# ── T875-S1: group without sequence rows ──────────────────────────────────────

def test_T875_S1_no_sequence_row_group_id_sql_determines_head(monkeypatch):
    """T875-S1: group with no workflow_sequences rows.

    The sequence-walk functions return None when no workflow_sequences row
    exists. Using that result as the head would miss the in-progress D document.

    group_id SQL (list_documents) finds D as the head regardless of sequence
    existence. The sentinel fails immediately if sequence-walk usage regresses.
    """
    GROUP_ID = "prj-noSeq-0001"

    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved", "seq": 1},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": None,       "seq": 2,
         "title": "시퀀스 없는 설계서"},
    ]})

    # get_sequence_by_doc_id -> None (no sequence row); get_sequence_items -> [].
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _: None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _: [])

    walk_called = _arm_walk_sentinels(monkeypatch)

    # The head must be D001 even though a walk would return None.
    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    # Explicitly confirm that the sentinel was not called.
    assert walk_called == [], f"sequence walk 호출됨: {walk_called}"

    # group_id SQL resolves the correct head.
    assert parsed["workflow_head_doc_id"]  == "D001"
    assert parsed["workflow_head_type"]    == "D"
    assert parsed["workflow_head_status"]  == "in_progress"


# ── T875-S2: new document with result_doc_id IS NULL ──────────────────────────

def test_T875_S2_result_doc_id_null_invisible_to_in_progress_walk(monkeypatch):
    """T875-S2: new result document with result_doc_id IS NULL.

    get_in_progress_head_by_group requires ``result_doc_id IS NOT NULL``, so it
    completely misses a newly created T document that is not linked yet.

    group_id SQL (list_documents) correctly returns T as the head regardless of
    result_doc_id. Walk-based resolution would return None or the wrong head.
    """
    GROUP_ID = "prj-nullRid-0002"

    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "R001", "type_code": "R",  "doc_review_status": "approved", "seq": 1},
        {"doc_id": "T001", "type_code": "T",  "doc_review_status": None,       "seq": 2,
         "title": "작업 결과 보고서 (신규)"},
    ]})

    # Even when get_sequence_items returns real entries, this slot's result_doc_id
    # is NULL because the new document has not been linked yet.
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _: {"id": 42})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: [
        {"type": "T", "sort_order": 1, "result_doc_id": None},
    ])

    walk_called = _arm_walk_sentinels(monkeypatch)

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "R001",
        "type_code": "R",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    # Confirm that the sentinel was not called.
    assert walk_called == [], f"sequence walk 호출됨: {walk_called}"

    # group_id SQL still resolves T as the head with a NULL result_doc_id.
    assert parsed["workflow_head_doc_id"]  == "T001"
    assert parsed["workflow_head_type"]    == "T"
    assert parsed["workflow_head_status"]  == "in_progress"


# ── T875-S3: sequence sort_order ≠ documents.seq ────────────────────────────────

def test_T875_S3_sequence_sort_order_diverges_from_documents_seq(monkeypatch):
    """T875-S3: workflow_sequence_items.sort_order and documents.seq are reversed.

    Scenario:
      - documents table: DS(seq=10, approved), D(seq=20, not approved)
      - workflow_sequence_items: D has sort_order=1, DS has sort_order=2 (reversed)

    A sequence walk ordered by sort_order sees D first. Because D is also
    unapproved in documents, the outcome happens to match group_id SQL.
    The walk still must not participate; the resolution path must use group_id SQL.

    Additional detail: with DS(seq=10), TR(seq=5, approved), and D(seq=20,
    not approved), a D->TR->DS walk may misidentify D as head for the wrong reason.
    group_id SQL plus Python sort(seq) correctly isolates unapproved D(seq=20).
    """
    GROUP_ID = "prj-sortDiv-0003"

    _patch_list_docs(monkeypatch, {GROUP_ID: [
        {"doc_id": "TR001", "type_code": "TR", "doc_review_status": "approved", "seq": 5},
        {"doc_id": "DS001", "type_code": "DS", "doc_review_status": "approved", "seq": 10},
        {"doc_id": "D001",  "type_code": "D",  "doc_review_status": None,       "seq": 20,
         "title": "역전 시퀀스 설계서"},
    ]})

    # Sequence items with reversed sort_order: D(1) -> DS(2) -> TR(3).
    # A walk sees D first, but this path must not be used at all.
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _: {"id": 99})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: [
        {"type": "D",  "sort_order": 1, "result_doc_id": None},
        {"type": "DS", "sort_order": 2, "result_doc_id": "DS001"},
        {"type": "TR", "sort_order": 3, "result_doc_id": "TR001"},
    ])

    walk_called = _arm_walk_sentinels(monkeypatch)

    parsed = document_routes._parse_doc_workflow({
        "doc_id": "DS001",
        "type_code": "DS",
        "project_id": "prj",
        "group_id": GROUP_ID,
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
    })

    # The sentinel remains untouched, confirming that the walk path did not participate.
    assert walk_called == [], f"sequence walk 호출됨: {walk_called}"

    # group_id SQL + Python sort(seq) -> D(seq=20) is the only unapproved document and the head.
    assert parsed["workflow_head_doc_id"]  == "D001"
    assert parsed["workflow_head_type"]    == "D"
    assert parsed["workflow_head_status"]  == "in_progress"
