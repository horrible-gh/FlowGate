"""WorkPlan N/T instruction materialization contract — flowgate.default.0600 T#1."""
from __future__ import annotations

import pytest

from modules.flow_gate.documents.routers import documents as docs
from modules.flow_gate.services import workflow_decision_service as workflow


WP_ID = "flowgate.default.0600.0004-WP"
ATTACHMENT = {
    "doc_id": WP_ID,
    "filename": "__wp_pre_instruction__T-1__brief.txt",
    "original_filename": "brief.txt",
    "content_sha256": "a" * 64,
}


def _head(type_code="T", *, item_id=11, item_seq=1, result_doc_id=None):
    return {
        "id": item_id,
        "item_seq": item_seq,
        "type": type_code,
        "label": type_code,
        "note": "Implement the materializer from the approved WorkPlan.",
        "source_doc_id": WP_ID,
        "source_revision_no": 7,
        "pre_instruction_text": "Preserve attachment provenance.",
        "pre_instruction_attachment": ATTACHMENT,
        "result_doc_id": result_doc_id,
        "result_doc_review_status": None,
    }


@pytest.mark.parametrize(("type_code", "item_seq", "expected_key"), [
    ("T", 3, "T#2"),
    ("N", 5, "N#1"),
])
def test_descriptor_preserves_wp_revision_step_and_payload(
    monkeypatch, type_code, item_seq, expected_key,
):
    head = _head(type_code, item_id=item_seq + 100, item_seq=item_seq)
    rows = [
        _head("T", item_id=101, item_seq=1),
        {"id": 102, "item_seq": 2, "type": "TR", "source_doc_id": WP_ID,
         "source_revision_no": 7},
        _head("T", item_id=103, item_seq=3),
        {"id": 104, "item_seq": 4, "type": "TR", "source_doc_id": WP_ID,
         "source_revision_no": 7},
        _head("N", item_id=105, item_seq=5),
    ]
    head["id"] = next(row["id"] for row in rows if row["item_seq"] == item_seq)
    monkeypatch.setattr(
        docs.document_service,
        "get_document",
        lambda doc_id: {"doc_id": doc_id, "type_code": "WP"},
    )
    monkeypatch.setattr(docs.db.workflow_sequences, "get_sequence_items", lambda _sid: rows)

    descriptor = docs._work_plan_instruction_descriptor(9, head)

    assert descriptor["source_wp_doc_id"] == WP_ID
    assert descriptor["source_wp_revision_no"] == 7
    assert descriptor["source_wp_step_key"] == expected_key
    assert descriptor["idempotency_key"] == f"{WP_ID}:7:{expected_key}"
    assert descriptor["instruction_note"] == head["note"]
    assert descriptor["pre_instruction_text"] == head["pre_instruction_text"]
    assert descriptor["pre_instruction_attachment"] == ATTACHMENT


def test_canonical_body_contains_instruction_and_all_provenance():
    materialization = {
        "source_wp_doc_id": WP_ID,
        "source_wp_revision_no": 7,
        "source_wp_step_key": "T#1",
        "idempotency_key": f"{WP_ID}:7:T#1",
        "instruction_note": "Implement the materializer.",
        "pre_instruction_text": "Keep the original attachment reference.",
        "pre_instruction_attachment": ATTACHMENT,
    }

    body = docs._build_work_plan_instruction_content(
        project_id="flowgate",
        module="default",
        group_id="flowgate.default.0600",
        type_code="T",
        doc_code="0006-T",
        title="작업지시 — T#1",
        target_id="flowgate.default.0600.0001-B",
        next_type="TR",
        materialization=materialization,
        locale="ko",
    )

    assert "source_wp_doc_id: \"flowgate.default.0600.0004-WP\"" in body
    assert "source_wp_revision_no: 7" in body
    assert "source_wp_step_key: \"T#1\"" in body
    assert f"materialization_key: \"{WP_ID}:7:T#1\"" in body
    assert "Implement the materializer." in body
    assert "Keep the original attachment reference." in body
    assert ATTACHMENT["filename"] in body
    assert ATTACHMENT["content_sha256"] in body


def test_same_materialization_key_reuses_the_existing_instruction(monkeypatch):
    materialization = {
        "source_wp_doc_id": WP_ID,
        "source_wp_revision_no": 7,
        "source_wp_step_key": "T#1",
        "idempotency_key": f"{WP_ID}:7:T#1",
    }
    existing = {
        "doc_id": "flowgate.default.0600.0006-T",
        "file_path": "documents/flowgate/main/default/0600/0006-T_document.md",
    }
    monkeypatch.setattr(
        docs, "_work_plan_instruction_descriptor", lambda _sid, _head: materialization,
    )
    monkeypatch.setattr(docs.document_service, "get_document", lambda _doc_id: existing)
    monkeypatch.setattr(
        docs, "_materialized_document_matches", lambda doc, descriptor: True,
    )
    monkeypatch.setattr(
        docs,
        "create_next_approved_core",
        lambda **_kwargs: pytest.fail("same key must not create another document"),
    )

    result = docs.materialize_work_plan_instruction(
        project_id="flowgate",
        group_id="flowgate.default.0600",
        module="default",
        prev_doc_id="flowgate.default.0600.0001-B",
        sequence_id=9,
        head=_head(result_doc_id=existing["doc_id"]),
        actor_user_id="pm",
        approver_perms={"document.approve"},
    )

    assert result["doc_id"] == existing["doc_id"]
    assert result["idempotent_reuse"] is True
    assert result["materialization"]["idempotency_key"] == f"{WP_ID}:7:T#1"


def test_legacy_nt_delegates_to_fixed_template_core(monkeypatch):
    legacy_head = {"id": 11, "item_seq": 1, "type": "N", "note": "legacy"}
    seen = {}
    monkeypatch.setattr(
        docs,
        "create_next_approved_core",
        lambda **kwargs: seen.update(kwargs) or {"doc_id": "legacy-N"},
    )

    result = docs.materialize_work_plan_instruction(
        project_id="flowgate",
        group_id="flowgate.default.0600",
        module="default",
        prev_doc_id="flowgate.default.0600.0001-B",
        sequence_id=9,
        head=legacy_head,
        actor_user_id="pm",
        approver_perms={"document.approve"},
    )

    assert result == {"doc_id": "legacy-N"}
    assert seen["type_code"] == "N"
    assert "_work_plan_materialization" not in seen


def test_wp_instruction_auto_approval_moves_head_to_paired_report(monkeypatch):
    from modules.flow_gate.db import users as db_users

    state = {"head": _head()}
    monkeypatch.setattr(db_users, "get_by_id", lambda user_id: {"user_id": user_id, "is_admin": 1})
    monkeypatch.setattr(
        workflow.db_wfseq, "get_effective_head", lambda _sid: state["head"],
    )
    calls = []

    def materialize(**kwargs):
        calls.append(kwargs)
        state["head"] = {
            "id": 12, "item_seq": 2, "type": "TR", "label": "TR",
            "result_doc_id": None, "result_doc_review_status": None,
        }
        return {"doc_id": "flowgate.default.0600.0006-T", "idempotent_reuse": False}

    monkeypatch.setattr(docs, "materialize_work_plan_instruction", materialize)

    completed = workflow._auto_complete_instruction_heads(
        spine_doc={
            "doc_id": "flowgate.default.0600.0001-B",
            "project_id": "flowgate",
            "group_id": "flowgate.default.0600",
            "module": "default",
        },
        seq={"id": 9},
        actor_user_id="pm",
        locale="ko",
        target_seq=2,
    )

    assert completed == [1]
    assert calls[0]["head"]["source_doc_id"] == WP_ID
    assert calls[0]["sequence_id"] == 9
    assert state["head"]["type"] == "TR"


def test_ts_stays_outside_nt_materializer():
    assert "TS" not in workflow.INSTRUCTION_AUTO_TYPES
    assert docs._work_plan_instruction_descriptor(9, _head("TS")) is None