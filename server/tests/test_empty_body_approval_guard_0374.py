"""Regression tests for flowgate.default.0374 T0010.

M/CH documents are approved directly by their creation paths: next-empty updates
``doc_review_status`` itself and inbox skips ``transition_document_review`` for
``AUTO_COMPLETE_TYPES``.  The whitelist case below is therefore defensive; no
production M/CH approval call path through this function was found.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from modules.flow_gate.workflow import pipeline_service as ps


APPROVAL_MESSAGE = (
    "본문이 비어 있어 승인할 수 없습니다. 문서 내용을 채운 뒤 다시 승인하십시오."
)


def _install_doc(monkeypatch, doc: dict):
    current = dict(doc)
    mock_docs = MagicMock()
    mock_docs.get_by_id.return_value = current

    def update(_doc_id, fields):
        current.update(fields)
        return dict(current)

    mock_docs.update.side_effect = update
    monkeypatch.setattr(ps, "db_docs", mock_docs)
    monkeypatch.setattr(ps, "log_state_changed", MagicMock())
    return current, mock_docs


def _doc(*, status: str = "pending_review", type_code: str = "TR") -> dict:
    return {
        "id": 10,
        "doc_id": "flowgate.default.0374.9999-TR",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0374",
        "branch": "main",
        "type_code": type_code,
        "file_path": "documents/flowgate/main/default/0374/9999-TR_document.md",
        "doc_review_status": status,
    }


@pytest.mark.parametrize(
    "content",
    [
        "---\nproject: flowgate\ntype: TR\n---\n",
        "---\nproject: flowgate\ntype: TR\n---\n\n \t\n\r\n",
    ],
    ids=["frontmatter-only", "whitespace-only-body"],
)
def test_approve_rejects_empty_body_without_updating_status(
    tmp_path, monkeypatch, content
):
    path = tmp_path / "document.md"
    path.write_text(content, encoding="utf-8")
    current, mock_docs = _install_doc(monkeypatch, _doc())
    monkeypatch.setattr(ps.storage_paths, "resolve_storage_path", lambda *a, **k: path)

    with pytest.raises(ps.TransitionError, match=APPROVAL_MESSAGE):
        ps.transition_document_review(
            doc_id=current["doc_id"],
            action="approve",
            actor_user_id="reviewer",
            user_permissions={"document.approve"},
        )

    assert current["doc_review_status"] == "pending_review"
    mock_docs.update.assert_not_called()


def test_approve_allows_non_empty_body(tmp_path, monkeypatch):
    path = tmp_path / "document.md"
    path.write_text(
        "---\nproject: flowgate\ntype: TR\n---\n\n# 작업 결과\n\n구현 완료\n",
        encoding="utf-8",
    )
    current, mock_docs = _install_doc(monkeypatch, _doc())
    monkeypatch.setattr(ps.storage_paths, "resolve_storage_path", lambda *a, **k: path)

    result = ps.transition_document_review(
        doc_id=current["doc_id"],
        action="approve",
        actor_user_id="reviewer",
        user_permissions={"document.approve"},
    )

    assert result["doc_review_status"] == "approved"
    mock_docs.update.assert_called_once()


@pytest.mark.parametrize(
    ("action", "status", "permissions", "comment", "expected"),
    [
        ("reject", "pending_review", {"document.reject"}, "본문을 채우세요", "rejected"),
        ("mark_revised", "rejected", {"document.update"}, None, "pending_review"),
    ],
)
def test_non_approval_transitions_do_not_read_or_block_empty_body(
    monkeypatch, action, status, permissions, comment, expected
):
    current, mock_docs = _install_doc(monkeypatch, _doc(status=status))
    resolver = MagicMock(side_effect=AssertionError("non-approval must not read the file"))
    monkeypatch.setattr(ps.storage_paths, "resolve_storage_path", resolver)

    result = ps.transition_document_review(
        doc_id=current["doc_id"],
        action=action,
        actor_user_id="reviewer",
        user_permissions=permissions,
        comment=comment,
    )

    assert result["doc_review_status"] == expected
    resolver.assert_not_called()
    mock_docs.update.assert_called_once()


def test_auto_complete_type_defensively_skips_body_guard(monkeypatch):
    current, mock_docs = _install_doc(monkeypatch, _doc(type_code="M"))
    resolver = MagicMock(side_effect=AssertionError("M must skip body inspection"))
    monkeypatch.setattr(ps.storage_paths, "resolve_storage_path", resolver)

    result = ps.transition_document_review(
        doc_id=current["doc_id"],
        action="approve",
        actor_user_id="reviewer",
        user_permissions={"document.approve"},
    )

    assert result["doc_review_status"] == "approved"
    resolver.assert_not_called()
    mock_docs.update.assert_called_once()