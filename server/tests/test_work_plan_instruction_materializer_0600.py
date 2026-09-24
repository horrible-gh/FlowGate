"""WorkPlan instruction materialization regression after 0611 T0009.

WorkPlan-backed N/T is authored by its AI worker. The managed server materializer remains
available only for legacy rows and must never expose a one-line canonical WP instruction.
"""
from __future__ import annotations

import pytest

from modules.flow_gate.documents.routers import documents as docs
from modules.flow_gate.services import workflow_decision_service as workflow


WP_ID = "flowgate.default.0600.0004-WP"


@pytest.mark.parametrize(("type_code", "item_seq", "step_key"), [
    ("T", 3, "T#2"),
    ("N", 1, "N#1"),
])
def test_descriptor_preserves_wp_revision_step_without_execution_payload(
    monkeypatch, type_code, item_seq, step_key,
):
    items = [
        {"id": 10, "item_seq": 1, "type": type_code, "source_doc_id": WP_ID,
         "source_revision_no": 7},
        {"id": 11, "item_seq": 3, "type": type_code, "source_doc_id": WP_ID,
         "source_revision_no": 7},
    ]
    monkeypatch.setattr(
        docs.document_service, "get_document",
        lambda doc_id: {"doc_id": doc_id, "type_code": "WP"},
    )
    from modules.flow_gate.db import workflow_sequences as wfseq
    monkeypatch.setattr(wfseq, "get_sequence_items", lambda _sid: items)
    head = next(item for item in items if item["item_seq"] == item_seq)
    descriptor = docs._work_plan_instruction_descriptor(9, head)
    assert descriptor["source_wp_step_key"] == step_key
    assert descriptor["idempotency_key"] == f"{WP_ID}:7:{step_key}"
    assert "note" not in descriptor
    assert "pre_instruction_text" not in descriptor
    assert "pre_instruction_attachment" not in descriptor


def test_workplan_materializer_refuses_placeholder(monkeypatch):
    monkeypatch.setattr(
        docs, "_work_plan_instruction_descriptor",
        lambda _sid, _head: {
            "source_wp_doc_id": WP_ID, "source_wp_revision_no": 7,
            "source_wp_step_key": "T#1", "idempotency_key": f"{WP_ID}:7:T#1",
        },
    )
    with pytest.raises(docs.NextApprovedError) as exc:
        docs.materialize_work_plan_instruction(
            project_id="flowgate", group_id="flowgate.default.0600", module="default",
            prev_doc_id="flowgate.default.0600.0001-B", sequence_id=9,
            head={"type": "T", "source_doc_id": WP_ID, "source_revision_no": 7},
            actor_user_id="pm", approver_perms={"document.approve"},
        )
    assert exc.value.status_code == 409
    assert "AI authoring path" in exc.value.detail


def test_legacy_instruction_still_delegates_to_managed_core(monkeypatch):
    monkeypatch.setattr(docs, "_work_plan_instruction_descriptor", lambda *_args: None)
    monkeypatch.setattr(
        docs, "create_next_approved_core",
        lambda **kwargs: {"doc_id": "legacy-T", "type_code": kwargs["type_code"]},
    )
    result = docs.materialize_work_plan_instruction(
        project_id="flowgate", group_id="legacy", module="default",
        prev_doc_id="legacy.0001-R", sequence_id=1, head={"type": "T"},
        actor_user_id="pm", approver_perms={"document.approve"},
    )
    assert result == {"doc_id": "legacy-T", "type_code": "T"}


def test_auto_approved_workplan_head_is_real_authoring_hop(monkeypatch):
    head = {
        "id": 11, "item_seq": 1, "type": "T", "source_doc_id": WP_ID,
        "source_revision_no": 7, "result_doc_id": None,
    }
    monkeypatch.setattr(workflow.db_wfseq, "get_effective_head", lambda _sid: head)
    monkeypatch.setattr(docs, "materialize_work_plan_instruction", lambda **_kw: pytest.fail(
        "WorkPlan-backed N/T must not be server-materialized"
    ))
    completed = workflow._auto_complete_instruction_heads(
        spine_doc={"doc_id": "root", "project_id": "flowgate", "group_id": "g",
                   "module": "default"},
        seq={"id": 9}, actor_user_id="pm", locale="ko", target_seq=2,
        instruction_mode="auto_approved",
    )
    assert completed == []
