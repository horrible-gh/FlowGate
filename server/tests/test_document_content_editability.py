from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


def _r_document(tmp_path: Path, review_status: str) -> dict:
    return {
        "doc_id": "flowgate.default.0007.0001-R",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0007",
        "type_code": "R",
        "status": "closed",
        "doc_review_status": review_status,
        "file_path": str(tmp_path / "R0001.md"),
    }


def test_closed_document_content_is_editable_before_final_approval(
    monkeypatch,
    tmp_path,
):
    from modules.flow_gate.documents.routers import documents as routes

    doc = _r_document(tmp_path, "wf_in_progress")
    update_mock = MagicMock(return_value=doc)
    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: doc)
    monkeypatch.setattr(routes.document_service, "update_document", update_mock)
    monkeypatch.setattr(routes, "_document_file_path", lambda _doc: Path(doc["file_path"]))

    result = routes.update_document_content(
        doc["doc_id"],
        routes.DocumentContentUpdate(content="updated"),
        {"user_id": "user-1"},
    )

    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "updated"
    assert result["content"] == "updated"
    update_mock.assert_called_once()


def test_document_content_is_locked_after_final_approval(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as routes

    doc = _r_document(tmp_path, "wf_done")
    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: doc)

    with pytest.raises(HTTPException) as exc_info:
        routes.update_document_content(
            doc["doc_id"],
            routes.DocumentContentUpdate(content="updated"),
            {"user_id": "user-1"},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Modification not allowed after final approval."
    assert not Path(doc["file_path"]).exists()


def test_document_metadata_is_editable_before_final_approval(monkeypatch):
    from modules.flow_gate.documents import document_service

    doc = {
        "doc_id": "flowgate.default.0007.0001-R",
        "status": "closed",
        "doc_review_status": "wf_in_progress",
    }
    monkeypatch.setattr(document_service.db_docs, "get_by_id", lambda _doc_id: doc)
    monkeypatch.setattr(
        document_service.db_docs,
        "update",
        lambda _doc_id, updates: {**doc, **updates},
    )

    updated = document_service.update_document(
        doc["doc_id"],
        {"title": "updated"},
        actor_user_id="user-1",
    )

    assert updated["title"] == "updated"


def test_document_metadata_is_locked_after_final_approval(monkeypatch):
    from modules.flow_gate.documents import document_service

    doc = {
        "doc_id": "flowgate.default.0007.0001-R",
        "status": "closed",
        "doc_review_status": "wf_done",
    }
    monkeypatch.setattr(document_service.db_docs, "get_by_id", lambda _doc_id: doc)

    with pytest.raises(HTTPException) as exc_info:
        document_service.update_document(
            doc["doc_id"],
            {"title": "updated"},
            actor_user_id="user-1",
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Modification not allowed after final approval."


def test_child_document_is_locked_when_group_r_is_final_approved(monkeypatch):
    from modules.flow_gate.documents import document_service

    child = {
        "doc_id": "flowgate.default.0007.0002-N",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0007",
        "type_code": "N",
        "status": "draft",
        "doc_review_status": "approved",
    }
    monkeypatch.setattr(
        document_service.db_docs,
        "list_documents",
        lambda **_kwargs: [{"type_code": "R", "doc_review_status": "wf_done"}],
    )

    assert document_service.is_final_approved(child) is True
    assert document_service.is_document_editable(child) is False


@pytest.mark.parametrize(
    ("r_review_status", "expected_editable"),
    [
        ("wf_in_progress", True),
        ("wf_done", False),
    ],
)
def test_document_detail_exposes_group_editability(
    monkeypatch,
    r_review_status,
    expected_editable,
):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import workflow_sequences
    from modules.flow_gate.documents.routers import documents as routes

    r_doc = {
        "doc_id": "flowgate.default.0007.0001-R",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0007",
        "type_code": "R",
        "status": "closed",
        "doc_review_status": r_review_status,
        "workflow_steps": None,
        "rejection_history": None,
        "seq": 1,
    }
    child = {
        "doc_id": "flowgate.default.0007.0002-N",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0007",
        "type_code": "N",
        "status": "draft",
        "doc_review_status": "approved",
        "workflow_steps": None,
        "rejection_history": None,
        "seq": 2,
    }

    def list_documents(*, type_code=None, **_kwargs):
        docs = [r_doc, child]
        if type_code is not None:
            docs = [doc for doc in docs if doc["type_code"] == type_code]
        return docs

    monkeypatch.setattr(db_documents, "list_documents", list_documents)
    monkeypatch.setattr(
        workflow_sequences,
        "get_sequence_by_doc_id",
        lambda _doc_id: None,
    )

    r_detail = routes._parse_doc_workflow(r_doc)
    child_detail = routes._parse_doc_workflow(child)

    assert r_detail["is_final_approved"] is (not expected_editable)
    assert r_detail["is_editable"] is expected_editable
    assert child_detail["is_final_approved"] is (not expected_editable)
    assert child_detail["is_editable"] is expected_editable
