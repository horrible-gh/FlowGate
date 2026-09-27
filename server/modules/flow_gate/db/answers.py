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
    selected_options: str = "[]",
    author_ai_run_id: Optional[str] = None,
    author_actual_provider_id: Optional[str] = None,
    author_actual_provider_name: Optional[str] = None,
) -> None:
    """answers INSERT (DB0006 §4.2 — author_kind/author_id; author_id NULL for AI).

    ``selected_options`` is the serialized JSON array of chosen option ids (DB0007 §2);
    the caller validates it against the item's options (L0008 §2.4).

    The three ``author_*`` columns are the AI run/provider snapshot for an AI answer
    (0582 T0005 §4/§D) — always None for a human answer, and None for an AI answer
    whose token carried no bound run (the [Copy Mention] hand-off starts no run by
    design, so it is legitimately unknown rather than dropped).
    """
    store = get_store()
    store._execute(
        store._sql("answers.insert_answer"),
        [
            question_item_id, body, author_kind, author_id, selected_options,
            author_ai_run_id, author_actual_provider_id, author_actual_provider_name,
        ],
    )


def list_by_author(author_id: str) -> list[dict]:
    """List answers authored by a given user (created_at DESC)."""
    store = get_store()
    return store._fetch_all(store._sql("answers.get_answers_by_author"), [author_id])
