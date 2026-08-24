"""Regression tests for orphan-slot detection, recovery, and retained rejection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from modules.flow_gate.api.v1 import document_routes
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.workflow import pipeline_service
from modules.flow_gate.workflow.routers import workflow


DOC_ID = "flowgate.default.0374.9999-TR"
ROOT_ID = "flowgate.default.0374.0001-B"
GROUP_ID = "flowgate.default.0374"


def _doc(type_code: str = "TR", status: str = "pending_review") -> dict:
    return {
        "id": 9999,
        "doc_id": DOC_ID,
        "type_code": type_code,
        "group_id": GROUP_ID,
        "project_id": "flowgate",
        "file_path": "documents/flowgate/main/default/0374/9999-TR_document.md",
        "doc_review_status": status,
    }


def _install_orphan_queries(monkeypatch, *, doc=None, decided=True, attached=False):
    row = doc or _doc()
    monkeypatch.setattr(db_documents, "get_by_id", lambda _doc_id: row)
    monkeypatch.setattr(
        db_documents,
        "get_documents_by_group_id",
        lambda _group_id: [{"doc_id": ROOT_ID, "type_code": "B"}],
    )
    monkeypatch.setattr(
        db_wfseq,
        "get_sequence_by_doc_id",
        lambda root_id: {"id": 10, "doc_id": ROOT_ID}
        if decided and root_id == ROOT_ID
        else None,
    )
    monkeypatch.setattr(
        db_wfseq,
        "get_sequence_for_member_doc",
        lambda _doc_id: {"id": 10, "doc_id": ROOT_ID} if attached else None,
    )


def test_orphan_detection_marks_unattached_member(monkeypatch):
    _install_orphan_queries(monkeypatch)
    assert db_wfseq.is_orphaned_workflow_member(DOC_ID) is True


def test_orphan_detection_is_false_before_workflow_decision(monkeypatch):
    _install_orphan_queries(monkeypatch, decided=False)
    assert db_wfseq.is_orphaned_workflow_member(DOC_ID) is False


@pytest.mark.parametrize("type_code", ["M", "CH", "Q", "A", "AC"])
def test_orphan_detection_excludes_non_slot_types(monkeypatch, type_code):
    _install_orphan_queries(monkeypatch, doc=_doc(type_code=type_code))
    assert db_wfseq.is_orphaned_workflow_member(DOC_ID) is False


def test_orphan_detection_is_false_for_attached_member(monkeypatch):
    _install_orphan_queries(monkeypatch, attached=True)
    assert db_wfseq.is_orphaned_workflow_member(DOC_ID) is False


def test_relations_exposes_orphan_signal(monkeypatch):
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda _doc_id: None)
    monkeypatch.setattr(db_wfseq, "is_orphaned_workflow_member", lambda _doc_id: True)

    result = document_routes._relations_workflow(DOC_ID)

    assert result["decided"] is False
    assert result["orphan"] is True


def test_relations_reports_ac_as_not_orphaned(monkeypatch):
    _install_orphan_queries(monkeypatch, doc=_doc(type_code="AC"))

    result = document_routes._relations_workflow(DOC_ID)

    assert result["decided"] is False
    assert result["orphan"] is False


def _install_recovery(monkeypatch, target: dict, *, orphan=True):
    doc = _doc()
    monkeypatch.setattr(workflow.db_docs, "get_by_id", lambda _doc_id: doc)
    monkeypatch.setattr(workflow.process_service, "is_group_disposed", lambda _group_id: False)
    monkeypatch.setattr(
        workflow.db_wfseq, "is_orphaned_workflow_member", lambda _doc_id: orphan
    )
    monkeypatch.setattr(
        workflow.db_wfseq,
        "get_pending_head_by_group",
        lambda _group_id, _project_id: target,
    )
    monkeypatch.setattr(
        workflow.storage_paths, "to_storage_relative", lambda path, _project_id: path
    )
    register = MagicMock(return_value=doc)
    monkeypatch.setattr(workflow, "register_workflow_result", register)
    return doc, register


def _admin() -> dict:
    return {"user_id": "reviewer", "is_admin": True}


def test_recovery_attaches_to_compatible_empty_head(monkeypatch):
    target = {"id": 77, "item_seq": 4, "type": "TR", "result_doc_id": None}
    doc, register = _install_recovery(monkeypatch, target)

    result = workflow.recover_orphaned_workflow_document_endpoint(
        DOC_ID, workflow.OrphanRecoveryRequest(), _admin()
    )

    assert result == {"document": doc, "item_seq": 4, "recovered": True}
    register.assert_called_once()
    assert register.call_args.kwargs["item_id"] == 77
    assert register.call_args.kwargs["registered_doc_id"] == DOC_ID


def test_recovery_rejects_ac_as_non_slot_type(monkeypatch):
    ac = _doc(type_code="AC")
    ac["file_path"] = None
    monkeypatch.setattr(workflow.db_docs, "get_by_id", lambda _doc_id: ac)
    monkeypatch.setattr(workflow.process_service, "is_group_disposed", lambda _group_id: False)
    orphan_check = MagicMock(return_value=True)
    monkeypatch.setattr(workflow.db_wfseq, "is_orphaned_workflow_member", orphan_check)

    with pytest.raises(HTTPException) as exc:
        workflow.recover_orphaned_workflow_document_endpoint(
            DOC_ID, workflow.OrphanRecoveryRequest(), _admin()
        )

    assert exc.value.status_code == 409
    assert "not a recoverable workflow slot type" in exc.value.detail
    orphan_check.assert_not_called()


def test_recovery_rejects_filled_slot(monkeypatch):
    target = {
        "id": 77,
        "item_seq": 4,
        "type": "TR",
        "result_doc_id": "flowgate.default.0374.0011-TR",
    }
    _install_recovery(monkeypatch, target)

    with pytest.raises(HTTPException) as exc:
        workflow.recover_orphaned_workflow_document_endpoint(
            DOC_ID, workflow.OrphanRecoveryRequest(), _admin()
        )

    assert exc.value.status_code == 409
    assert "already filled" in exc.value.detail


def test_recovery_rejects_type_mismatch_with_expected_type(monkeypatch):
    target = {"id": 77, "item_seq": 4, "type": "NR", "result_doc_id": None}
    _install_recovery(monkeypatch, target)

    with pytest.raises(HTTPException) as exc:
        workflow.recover_orphaned_workflow_document_endpoint(
            DOC_ID, workflow.OrphanRecoveryRequest(), _admin()
        )

    assert exc.value.status_code == 409
    assert "expects type NR" in exc.value.detail
    assert "document type is TR" in exc.value.detail


def test_recovery_requires_document_approve(monkeypatch):
    target = {"id": 77, "item_seq": 4, "type": "TR", "result_doc_id": None}
    _install_recovery(monkeypatch, target)

    with pytest.raises(HTTPException) as exc:
        workflow.recover_orphaned_workflow_document_endpoint(
            DOC_ID,
            workflow.OrphanRecoveryRequest(),
            {"user_id": "worker", "is_admin": False},
        )

    assert exc.value.status_code == 403
    assert "document.approve" in exc.value.detail


def test_orphan_can_be_rejected_without_slot_membership(monkeypatch):
    current = _doc()
    docs = MagicMock()
    docs.get_by_id.return_value = current

    def update(_doc_id, fields):
        current.update(fields)
        return dict(current)

    docs.update.side_effect = update
    monkeypatch.setattr(pipeline_service, "db_docs", docs)
    monkeypatch.setattr(pipeline_service, "log_state_changed", MagicMock())

    result = pipeline_service.transition_document_review(
        doc_id=DOC_ID,
        action="reject",
        actor_user_id="reviewer",
        user_permissions={"document.reject"},
        comment="고아 문서를 보관 반려합니다.",
    )

    assert result["doc_review_status"] == "rejected"
    assert result["rejection_reason"] == "고아 문서를 보관 반려합니다."