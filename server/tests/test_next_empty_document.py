from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


class _Store:
    @contextmanager
    def transaction(self):
        yield


def test_next_empty_non_m_stays_pending_review(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.workflow import pipeline_service

    prev_doc_id = "proj-main-0001-R0001"
    group_id = "proj-main-0001"
    head = {"id": 10, "type": "DS"}
    created_doc = {
        "doc_id": "proj-main-0001.0001-DS",
        "project_id": "proj",
        "group_id": group_id,
        "type_code": "DS",
        "doc_review_status": None,
    }
    # DB004 §6.1: transition_document_review(action='submit') is responsible for doc_review_status transitions.
    # get_by_id returns the refreshed doc after the transition.
    pending_doc = {**created_doc, "doc_review_status": "pending_review"}

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: {
        "doc_id": prev_doc_id,
        "project_id": "proj",
        "group_id": group_id,
    })
    monkeypatch.setattr(routes.numbering_service, "reserve_document", lambda **_kwargs: "0001-DS")
    monkeypatch.setattr(routes.storage_paths, "document_path", lambda **_kwargs: tmp_path / "document.md")
    monkeypatch.setattr(routes, "_get_project_branch", lambda _project_id: "main")
    monkeypatch.setattr(routes, "_try_close_parent_on_child_created", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes.document_service, "create_document", MagicMock(return_value=created_doc))
    monkeypatch.setattr(db_connection, "get_store", lambda: _Store())
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_effective_head", lambda _seq_id: head)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _seq_id: [head])

    mark_sequence_done = MagicMock()
    monkeypatch.setattr(db_wfseq, "mark_sequence_done", mark_sequence_done)
    register_workflow_result = MagicMock(return_value=created_doc)
    monkeypatch.setattr(pipeline_service, "register_workflow_result", register_workflow_result)
    # DB004 §6.1: doc_review_status transitions go through transition_document_review(action='submit').
    # Assume get_by_id returns the refreshed doc (pending_doc) after the transition.
    transition_doc_review = MagicMock()
    monkeypatch.setattr(pipeline_service, "transition_document_review", transition_doc_review)

    from modules.flow_gate.db import documents as db_documents
    monkeypatch.setattr(db_documents, "get_by_id", lambda _doc_id: pending_doc)

    result = routes.create_next_empty_document(
        routes.NextEmptyDocumentCreate(
            project_id="proj",
            group_id=group_id,
            prev_doc_id=prev_doc_id,
            type_code="DS",
            title="Empty DS",
        ),
        current_user={"user_id": "usr_test"},
    )

    assert result["data"]["doc_review_status"] == "pending_review"
    register_workflow_result.assert_called_once()
    transition_doc_review.assert_called_once_with(
        doc_id=created_doc["doc_id"],
        action="submit",
        actor_user_id="usr_test",
        user_permissions={"document.update"},
    )
    mark_sequence_done.assert_not_called()


def test_next_empty_resolves_sequence_from_produced_child(monkeypatch, tmp_path):
    """0048 TR0009: after the FE navigates to a just-created child (openAfter), a
    follow-up "create empty" sends that child as prev_doc_id. The child is not the
    sequence root, so get_sequence_by_doc_id returns None — the handler must fall
    back to the produced-result reverse lookup instead of raising 422.
    """
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.workflow import pipeline_service

    # prev_doc is the produced N child, not the sequence-owning root B.
    prev_doc_id = "proj-main-0001.0002-N"
    group_id = "proj-main-0001"
    head = {"id": 11, "type": "NR"}
    created_doc = {
        "doc_id": "proj-main-0001.0003-NR",
        "project_id": "proj",
        "group_id": group_id,
        "type_code": "NR",
        "doc_review_status": None,
    }
    pending_doc = {**created_doc, "doc_review_status": "pending_review"}

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: {
        "doc_id": prev_doc_id,
        "project_id": "proj",
        "group_id": group_id,
    })
    monkeypatch.setattr(routes.numbering_service, "reserve_document", lambda **_kwargs: "0003-NR")
    monkeypatch.setattr(routes.storage_paths, "document_path", lambda **_kwargs: tmp_path / "document.md")
    monkeypatch.setattr(routes, "_get_project_branch", lambda _project_id: "main")
    monkeypatch.setattr(routes, "_try_close_parent_on_child_created", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes.document_service, "create_document", MagicMock(return_value=created_doc))
    monkeypatch.setattr(db_connection, "get_store", lambda: _Store())
    # Root lookup misses (child is not the sequence root); reverse-by-result resolves it.
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: None)
    monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", lambda _doc_id: {"id": 10, "sequence_id": 1})
    monkeypatch.setattr(db_wfseq, "get_sequence_by_id", lambda _seq_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_effective_head", lambda _seq_id: head)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _seq_id: [head])
    monkeypatch.setattr(pipeline_service, "register_workflow_result", MagicMock(return_value=created_doc))
    monkeypatch.setattr(pipeline_service, "transition_document_review", MagicMock())

    from modules.flow_gate.db import documents as db_documents
    monkeypatch.setattr(db_documents, "get_by_id", lambda _doc_id: pending_doc)

    result = routes.create_next_empty_document(
        routes.NextEmptyDocumentCreate(
            project_id="proj",
            group_id=group_id,
            prev_doc_id=prev_doc_id,
            type_code="NR",
            title="Empty NR",
        ),
        current_user={"user_id": "usr_test"},
    )

    assert result["doc_id"] == "proj-main-0001.0003-NR"
    assert result["data"]["doc_review_status"] == "pending_review"


def test_next_empty_blocks_in_progress_head(monkeypatch):
    from fastapi import HTTPException
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    prev_doc_id = "proj-main-0001-R0001"
    group_id = "proj-main-0001"

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: {
        "doc_id": prev_doc_id,
        "project_id": "proj",
        "group_id": group_id,
    })
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: {"id": 1})
    monkeypatch.setattr(
        db_wfseq,
        "get_effective_head",
        lambda _seq_id: {
            "id": 10,
            "type": "DS",
            "result_doc_id": "proj-main-0001.0001-DS",
            "result_doc_review_status": "pending_review",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        routes.create_next_empty_document(
            routes.NextEmptyDocumentCreate(
                project_id="proj",
                group_id=group_id,
                prev_doc_id=prev_doc_id,
                type_code="DS",
                title="Empty DS",
            ),
            current_user={"user_id": "usr_test"},
        )

    assert exc_info.value.status_code == 409


def test_next_empty_allows_stranded_in_progress_head(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.workflow import pipeline_service

    prev_doc_id = "proj-main-0001-R0001"
    group_id = "proj-main-0001"
    head = {"id": 10, "type": "M", "result_doc_id": None}
    created_doc = {
        "doc_id": "proj-main-0001.0001-M",
        "project_id": "proj",
        "group_id": group_id,
        "type_code": "M",
        "doc_review_status": None,
    }

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: {
        "doc_id": prev_doc_id,
        "project_id": "proj",
        "group_id": group_id,
    })
    monkeypatch.setattr(routes.numbering_service, "reserve_document", lambda **_kwargs: "0001-M")
    monkeypatch.setattr(routes.storage_paths, "document_path", lambda **_kwargs: tmp_path / "document.md")
    monkeypatch.setattr(routes, "_get_project_branch", lambda _project_id: "main")
    monkeypatch.setattr(routes, "_try_close_parent_on_child_created", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes.document_service, "create_document", MagicMock(return_value=created_doc))
    monkeypatch.setattr(db_connection, "get_store", lambda: _Store())
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_effective_head", lambda _seq_id: head)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _seq_id: [head])
    monkeypatch.setattr(db_wfseq, "get_sequence_head_pending", lambda _seq_id: None)
    monkeypatch.setattr(db_documents, "update", MagicMock())

    set_item_result_doc_id = MagicMock()
    mark_sequence_done = MagicMock()
    monkeypatch.setattr(db_wfseq, "set_item_result_doc_id", set_item_result_doc_id)
    monkeypatch.setattr(db_wfseq, "mark_sequence_done", mark_sequence_done)
    monkeypatch.setattr(pipeline_service, "register_workflow_result", MagicMock())

    result = routes.create_next_empty_document(
        routes.NextEmptyDocumentCreate(
            project_id="proj",
            group_id=group_id,
            prev_doc_id=prev_doc_id,
            type_code="M",
            title="Empty M",
        ),
        current_user={"user_id": "usr_test"},
    )

    assert result["doc_id"] == "proj-main-0001.0001-M"
    set_item_result_doc_id.assert_called_once_with(head["id"], created_doc["doc_id"])
