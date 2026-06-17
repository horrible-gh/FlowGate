"""answers CRUD — follows the sqloader.load pattern.

Inline SQL is prohibited. Only use SQL registered in queries.json.
A (answer) has no numbering — only the internal PK(id) is used.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store


def get_by_pk(pk: int) -> Optional[dict]:
    """Return the answers row by PK(id)."""
    store = get_store()
    return store._fetch_one(store._sql("answers.get_answer_by_pk"), [pk])


def list_by_question_item(question_item_id: int) -> list[dict]:
    """List answers for a question_item PK (created_at ASC)."""
    store = get_store()
    return store._fetch_all(
        store._sql("answers.get_answers_by_question_item"), [question_item_id]
    )


def insert(
    question_item_id: int,
    body: str,
    author_kind: str = "human",
    author_id: Optional[str] = None,
) -> None:
    """answers INSERT (DB0006 §4.2 — author_kind/author_id; author_id NULL for AI)."""
    store = get_store()
    store._execute(
        store._sql("answers.insert_answer"),
        [question_item_id, body, author_kind, author_id],
    )


def list_by_author(author_id: str) -> list[dict]:
    """List answers authored by a given user (created_at DESC)."""
    store = get_store()
    return store._fetch_all(store._sql("answers.get_answers_by_author"), [author_id])
