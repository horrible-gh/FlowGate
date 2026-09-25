"""WorkPlan instruction materialization regression after 0611 T0011.

A WorkPlan-backed N/T is expanded from the step's own instruction document (pre-instruction
file and/or text). 0614 T0004 overrides the 0611 rej_01M3AVQVHD6PSTBE fallback (historical
contract, still pinned in test_work_plan_instruction_semantics_0611.py's module docstring):
a step without an instruction document now falls back to server-materializing steps[].note
under manual/auto_approved, and only falls back further to the legacy generated instruction
when the note is empty too. ai_direct is unaffected -- it still never server-expands a
WorkPlan step, document or not.
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


def test_workplan_materializer_delegates_with_descriptor_and_review_policy(monkeypatch):
    descriptor = {
        "source_wp_doc_id": WP_ID, "source_wp_revision_no": 7,
        "source_wp_step_key": "T#1", "idempotency_key": f"{WP_ID}:7:T#1",
    }
    monkeypatch.setattr(docs, "_work_plan_instruction_descriptor", lambda _sid, _head: descriptor)
    seen = {}
    monkeypatch.setattr(
        docs, "create_next_approved_core",
        lambda **kwargs: seen.update(kwargs) or {"doc_id": "wp-T"},
    )
    result = docs.materialize_work_plan_instruction(
        project_id="flowgate", group_id="flowgate.default.0600", module="default",
        prev_doc_id="flowgate.default.0600.0001-B", sequence_id=9,
        head={"type": "T", "source_doc_id": WP_ID, "source_revision_no": 7, "review_count": 2},
        actor_user_id="pm", approver_perms={"document.approve"},
    )
    assert seen["_work_plan_materialization"] == descriptor
    assert seen["_approve_immediately"] is False
    assert result["content_source"] == docs.WORK_PLAN_INSTRUCTION_CONTENT_SOURCE
    assert result["idempotent_reuse"] is False


def test_instruction_body_is_file_then_text_and_never_the_note():
    body = docs._work_plan_instruction_body(
        "작업지시 — T#1",
        {"text": "written half", "attachment": None, "attachment_markdown": "# Real\n\n- do it"},
        "ko",
    )
    assert body == "# Real\n\n- do it\n\n## 추가 지시\n\nwritten half\n"
    assert docs._work_plan_instruction_body(
        "작업지시 — T#1", {"text": "only text", "attachment": None, "attachment_markdown": ""}, "en",
    ) == "# 작업지시 — T#1\n\nonly text\n"


def test_instruction_document_is_none_without_text_or_file():
    assert docs._work_plan_instruction_document(
        {"note": "one-line message", "pre_instruction_text": "  ",
         "pre_instruction_attachment_json": None, "source_doc_id": WP_ID}
    ) is None


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


def test_auto_approved_workplan_head_without_instruction_document_is_now_server_materialized(
    monkeypatch,
):
    """0614 T0004 (human override of the 0611 rej_01M3AVQVHD6PSTBE final contract this file
    used to pin as `..._is_authoring_hop`): a WorkPlan-backed N/T with no instruction
    document is no longer left as an authoring hop under auto_approved -- it is
    server-materialized from steps[].note (or the legacy generated instruction when the note
    is empty too). ai_direct is unaffected -- see test_work_plan_instruction_semantics_0611.py
    for the mode-split coverage."""
    head = {
        "id": 11, "item_seq": 1, "type": "T", "source_doc_id": WP_ID,
        "source_revision_no": 7, "result_doc_id": None, "note": "write it",
    }
    heads = iter([head, None])
    monkeypatch.setattr(workflow.db_wfseq, "get_effective_head", lambda _sid: next(heads, None))
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.workflow.routers import workflow as workflow_router
    monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})
    monkeypatch.setattr(
        workflow_router, "_get_user_permissions", lambda _user: {"document.approve"},
    )
    calls = []

    def materialize(**kwargs):
        calls.append(kwargs["head"])
        return {"doc_id": "wp-T", "content_source": docs.WORK_PLAN_STEP_NOTE_CONTENT_SOURCE}

    monkeypatch.setattr(docs, "materialize_work_plan_instruction", materialize)
    completed = workflow._auto_complete_instruction_heads(
        spine_doc={"doc_id": "root", "project_id": "flowgate", "group_id": "g",
                   "module": "default"},
        seq={"id": 9}, actor_user_id="pm", locale="ko", target_seq=2,
        instruction_mode="auto_approved",
    )
    assert completed == [1]
    assert calls == [head]
