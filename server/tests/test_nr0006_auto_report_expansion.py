"""NR0006 / T0007 — report steps (NR/TR/TSR) are attached on the decision path.

Previously the N→NR / T→TR / TS→TSR expansion lived only in the client
WorkflowDecisionModal, so a workflow decided by an AI worker (which POSTs a bare
sequence straight to /workflow/decide) stored only the instruction steps and silently
dropped the reports. The expansion now lives in decide_workflow, the single chokepoint
both callers funnel through, and is idempotent so the client modal (which already submits
the reports) is unaffected.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock

os.environ.setdefault("TESTING", "1")

from modules.flow_gate.services import workflow_decision_service as svc


def _types(seq):
    return [it["type"] for it in seq]


# ── pure helper ───────────────────────────────────────────────────────────────

def test_bare_ai_sequence_gets_reports_inserted():
    seq = [
        {"id": 1, "type": "N", "label": "조사지시"},
        {"id": 2, "type": "T", "label": "작업지시"},
        {"id": 3, "type": "TS", "label": "테스트시나리오지시"},
    ]
    out = svc._expand_auto_reports(seq)
    assert _types(out) == ["N", "NR", "T", "TR", "TS", "TSR"]
    # ids are renumbered contiguous so item_seq stays unique after insertion
    assert [it["id"] for it in out] == [1, 2, 3, 4, 5, 6]


def test_client_modal_sequence_is_unchanged_idempotent():
    # The modal already submits the report right after each instruction.
    seq = [
        {"id": 1, "type": "N", "label": "조사지시"},
        {"id": 2, "type": "NR", "label": "조사레포트"},
        {"id": 3, "type": "T", "label": "작업지시"},
        {"id": 4, "type": "TR", "label": "작업레포트"},
    ]
    out = svc._expand_auto_reports(seq)
    assert _types(out) == ["N", "NR", "T", "TR"]  # no duplicate reports


def test_non_instruction_steps_are_untouched():
    seq = [
        {"id": 1, "type": "DS", "label": "설계지시"},
        {"id": 2, "type": "D", "label": "기본설계"},
        {"id": 3, "type": "M", "label": "메모"},
        {"id": 4, "type": "V", "label": "리뷰의뢰"},  # V→VR excluded (VR not a real type)
    ]
    out = svc._expand_auto_reports(seq)
    assert _types(out) == ["DS", "D", "M", "V"]


def test_lowercase_type_is_normalized():
    out = svc._expand_auto_reports([{"id": 1, "type": "n", "label": "조사지시"}])
    assert _types(out) == ["n", "NR"]


# ── decide_workflow integration (store stubbed) ───────────────────────────────

def test_decide_workflow_persists_expanded_sequence(monkeypatch):
    inserted: list[dict] = []

    @contextmanager
    def _txn():
        yield

    fake_store = MagicMock()
    fake_store.transaction.side_effect = _txn

    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: {"doc_id": _id})
    # First lookup (pre-insert) must be None; after insert_sequence, return the row.
    seq_state = {"created": False}
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id",
                        lambda _id: {"id": 99} if seq_state["created"] else None)
    monkeypatch.setattr(svc, "get_store", lambda: fake_store)

    def _insert_seq(_doc_id):
        seq_state["created"] = True
    monkeypatch.setattr(svc.db_wfseq, "insert_sequence", _insert_seq)
    monkeypatch.setattr(svc.db_wfseq, "insert_sequence_item",
                        lambda **kw: inserted.append(kw))
    update = MagicMock()
    monkeypatch.setattr(svc.db_documents, "update", update)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head",
                        lambda _sid: {"id": 1, "type": "N", "label": "조사지시"})

    result = svc.decide_workflow(
        doc_id="flowgate.default.0006.0001-R",
        doc_class="R",
        sequence=[
            {"id": 1, "type": "N", "label": "조사지시"},
            {"id": 2, "type": "T", "label": "작업지시"},
            {"id": 3, "type": "TS", "label": "테스트시나리오지시"},
        ],
    )

    assert [kw["type_"] for kw in inserted] == ["N", "NR", "T", "TR", "TS", "TSR"]
    assert result["sequence_count"] == 6
    # workflow_steps mirror the expanded types
    steps = update.call_args.args[1]["workflow_steps"]
    assert "NR" in steps and "TR" in steps and "TSR" in steps
