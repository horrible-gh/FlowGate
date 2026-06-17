"""Tests for POST /documents/next-approved — auto-approved instruction docs.

R0001 #2 (group 0048) / P0005 / L0006-L0007. Mirrors test_next_empty_document.py:
the route function is called directly with monkeypatched dependencies.
"""
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


class _FakeHeaders:
    def __init__(self, data: dict):
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key, default=None):
        return self._data.get(key.lower(), default)


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = _FakeHeaders(headers or {})


def _wire_common(monkeypatch, *, head, group_id, prev_doc_id, tmp_path,
                 created_doc, refreshed_doc, perms, locale_label="조사"):
    """Patch the full happy-path dependency surface; individual tests override pieces."""
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import document_type_labels
    from modules.flow_gate.workflow.routers import workflow as wf_router
    from modules.flow_gate.workflow import pipeline_service

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: {
        "doc_id": prev_doc_id, "project_id": "proj", "group_id": group_id,
    })
    monkeypatch.setattr(routes.numbering_service, "reserve_document", lambda **_k: "0005-N")
    monkeypatch.setattr(routes.storage_paths, "document_path", lambda **_k: tmp_path / "document.md")
    monkeypatch.setattr(routes, "_get_project_branch", lambda _p: "main")
    monkeypatch.setattr(routes, "_try_close_parent_on_child_created", lambda *_a, **_k: None)
    monkeypatch.setattr(routes.document_service, "create_document", MagicMock(return_value=created_doc))
    monkeypatch.setattr(db_connection, "get_store", lambda: _Store())
    monkeypatch.setattr(db_connection, "now_iso", lambda: "2026-06-13T00:00:00+09:00")
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _d: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_effective_head", lambda _s: head)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _s: [head])
    monkeypatch.setattr(db_documents, "get_by_id", lambda _d: refreshed_doc)
    monkeypatch.setattr(document_type_labels, "get_type_name", lambda _tc, _loc: locale_label)
    # Approve perms now resolve via the SAME stub the live approve button uses
    # (workflow router _get_user_permissions), not the unpopulated RBAC DB path.
    monkeypatch.setattr(wf_router, "_get_user_permissions", lambda _u: set(perms))

    reg = MagicMock(return_value=created_doc)
    trans = MagicMock()
    monkeypatch.setattr(pipeline_service, "register_workflow_result", reg)
    monkeypatch.setattr(pipeline_service, "transition_document_review", trans)
    return reg, trans


def test_next_approved_happy_creates_approved_doc(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as routes

    group_id = "proj-main-0001"
    prev_doc_id = "proj-main-0001-R0001"
    head = {"id": 10, "type": "N", "result_doc_id": None}
    created_doc = {"doc_id": "proj-main-0001.0005-N", "project_id": "proj",
                   "group_id": group_id, "type_code": "N", "doc_review_status": None}
    refreshed_doc = {**created_doc, "doc_review_status": "approved", "title": "조사 승인"}

    reg, trans = _wire_common(
        monkeypatch, head=head, group_id=group_id, prev_doc_id=prev_doc_id,
        tmp_path=tmp_path, created_doc=created_doc, refreshed_doc=refreshed_doc,
        perms={"document.approve", "document.update", "perm_document_create"},
    )

    result = routes.create_next_approved_document(
        routes.NextApprovedDocumentCreate(
            project_id="proj", group_id=group_id, prev_doc_id=prev_doc_id, type_code="N",
        ),
        request=_FakeRequest({"X-Locale": "ko"}),
        current_user={"user_id": "usr_test"},
    )

    assert result["data"]["doc_review_status"] == "approved"
    assert result["data"]["title"] == "조사 승인"
    # slot fully registered + state machine submit→approve (P0005 §3 D-C)
    reg.assert_called_once()
    assert trans.call_count == 2
    submit_call, approve_call = trans.call_args_list
    assert submit_call.kwargs["action"] == "submit"
    assert submit_call.kwargs["user_permissions"] == {"document.update"}
    assert approve_call.kwargs["action"] == "approve"
    # approve uses the caller's REAL permission set (P0005 §4)
    assert "document.approve" in approve_call.kwargs["user_permissions"]
    # title + body generated server-side from the type label
    content = (tmp_path / "document.md").read_text(encoding="utf-8")
    assert "title: 조사 승인" in content
    assert "조사 가 승인되었습니다." in content


@pytest.mark.parametrize("bad_type", ["D", "P", "L", "DB", "AC"])
def test_next_approved_rejects_non_instruction_types(monkeypatch, bad_type):
    from fastapi import HTTPException
    from modules.flow_gate.documents.routers import documents as routes

    with pytest.raises(HTTPException) as exc:
        routes.create_next_approved_document(
            routes.NextApprovedDocumentCreate(
                project_id="proj", group_id="proj-main-0001",
                prev_doc_id="proj-main-0001-R0001", type_code=bad_type,
            ),
            request=_FakeRequest(),
            current_user={"user_id": "usr_test"},
        )
    assert exc.value.status_code == 422


def test_next_approved_blocks_without_approve_permission(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from modules.flow_gate.documents.routers import documents as routes

    group_id = "proj-main-0001"
    prev_doc_id = "proj-main-0001-R0001"
    head = {"id": 10, "type": "N", "result_doc_id": None}
    created_doc = {"doc_id": "proj-main-0001.0005-N"}
    reserve = MagicMock(return_value="0005-N")

    _wire_common(
        monkeypatch, head=head, group_id=group_id, prev_doc_id=prev_doc_id,
        tmp_path=tmp_path, created_doc=created_doc, refreshed_doc=created_doc,
        perms={"document.update"},  # no document.approve
    )
    # override numbering to assert it is NOT reached (403 fires before reserving a number)
    monkeypatch.setattr(routes.numbering_service, "reserve_document", reserve)

    with pytest.raises(HTTPException) as exc:
        routes.create_next_approved_document(
            routes.NextApprovedDocumentCreate(
                project_id="proj", group_id=group_id, prev_doc_id=prev_doc_id, type_code="N",
            ),
            request=_FakeRequest({"X-Locale": "ko"}),
            current_user={"user_id": "usr_test"},
        )
    assert exc.value.status_code == 403
    reserve.assert_not_called()
    assert not (tmp_path / "document.md").exists()


def test_next_approved_head_type_mismatch(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from modules.flow_gate.documents.routers import documents as routes

    group_id = "proj-main-0001"
    prev_doc_id = "proj-main-0001-R0001"
    head = {"id": 10, "type": "T", "result_doc_id": None}  # head is T, request N
    _wire_common(
        monkeypatch, head=head, group_id=group_id, prev_doc_id=prev_doc_id,
        tmp_path=tmp_path, created_doc={}, refreshed_doc={},
        perms={"document.approve"},
    )

    with pytest.raises(HTTPException) as exc:
        routes.create_next_approved_document(
            routes.NextApprovedDocumentCreate(
                project_id="proj", group_id=group_id, prev_doc_id=prev_doc_id, type_code="N",
            ),
            request=_FakeRequest(),
            current_user={"user_id": "usr_test"},
        )
    assert exc.value.status_code == 409


def test_next_approved_slot_already_occupied(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from modules.flow_gate.documents.routers import documents as routes

    group_id = "proj-main-0001"
    prev_doc_id = "proj-main-0001-R0001"
    head = {"id": 10, "type": "N",
            "result_doc_id": "proj-main-0001.0005-N",
            "result_doc_review_status": "pending_review"}
    _wire_common(
        monkeypatch, head=head, group_id=group_id, prev_doc_id=prev_doc_id,
        tmp_path=tmp_path, created_doc={}, refreshed_doc={},
        perms={"document.approve"},
    )

    with pytest.raises(HTTPException) as exc:
        routes.create_next_approved_document(
            routes.NextApprovedDocumentCreate(
                project_id="proj", group_id=group_id, prev_doc_id=prev_doc_id, type_code="N",
            ),
            request=_FakeRequest(),
            current_user={"user_id": "usr_test"},
        )
    assert exc.value.status_code == 409
