"""q_service answer/status tests — document-bound container model (group 0022 Q/A/V revamp)."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch


def _fake_store():
    store = MagicMock()

    @contextmanager
    def _txn():
        yield store

    store.transaction.side_effect = _txn
    return store


def test_register_answer_human_transitions_done_when_all_answered():
    from modules.flow_gate.services import q_service

    doc_id = "testprj.test.0001.0003-D"
    with (
        patch.object(q_service, "get_store", return_value=_fake_store()),
        patch.object(q_service.db_questions, "get_container_by_doc",
                     return_value={"id": 1, "doc_id": doc_id, "status": "pending"}),
        patch.object(q_service.db_question_items, "get_by_pk",
                     return_value={"id": 10, "question_id": 1}),
        patch.object(q_service.db_answers, "insert") as insert_answer,
        patch.object(q_service.db_question_items, "increment_answer_count") as inc,
        patch.object(q_service.db_question_items, "list_unanswered", return_value=[]),
        patch.object(q_service.db_questions, "update_status") as update_status,
        patch.object(q_service.db_answers, "list_by_question_item", return_value=[{"id": 21}]),
    ):
        result = q_service.register_answer(
            doc_id=doc_id, item_id=10, body="Answer", author_kind="human", author_id="usr_test",
        )

    assert result == {
        "doc_id": doc_id, "item_id": 10, "answer_id": 21,
        "author_kind": "human", "status": "done",
    }
    insert_answer.assert_called_once_with(
        question_item_id=10, body="Answer", author_kind="human", author_id="usr_test",
    )
    inc.assert_called_once_with(pk=10)
    update_status.assert_called_once_with(doc_id, "done")


def test_register_answer_ai_nulls_author_id_and_stays_pending():
    from modules.flow_gate.services import q_service

    doc_id = "testprj.test.0001.0003-D"
    with (
        patch.object(q_service, "get_store", return_value=_fake_store()),
        patch.object(q_service.db_questions, "get_container_by_doc",
                     return_value={"id": 1, "doc_id": doc_id, "status": "pending"}),
        patch.object(q_service.db_question_items, "get_by_pk",
                     return_value={"id": 10, "question_id": 1}),
        patch.object(q_service.db_answers, "insert") as insert_answer,
        patch.object(q_service.db_question_items, "increment_answer_count"),
        # another item is still unanswered → stays pending
        patch.object(q_service.db_question_items, "list_unanswered", return_value=[{"id": 11}]),
        patch.object(q_service.db_questions, "update_status") as update_status,
        patch.object(q_service.db_answers, "list_by_question_item", return_value=[{"id": 22}]),
    ):
        result = q_service.register_answer(
            doc_id=doc_id, item_id=10, body="AI answer", author_kind="ai", author_id="ignored",
        )

    assert result["author_kind"] == "ai"
    assert result["status"] == "pending"
    insert_answer.assert_called_once_with(
        question_item_id=10, body="AI answer", author_kind="ai", author_id=None,
    )
    update_status.assert_not_called()


def test_register_answer_404_when_no_container():
    import pytest
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service

    with (
        patch.object(q_service.db_questions, "get_container_by_doc", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            q_service.register_answer(
                doc_id="x.y.0001.0001-D", item_id=1, body="a",
                author_kind="human", author_id="u",
            )
    assert exc.value.status_code == 404
