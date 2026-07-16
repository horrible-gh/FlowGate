"""question_items CRUD — follows the sqloader.load pattern.

Inline SQL is prohibited. Only use SQL registered in queries.json.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store


def get_by_pk(pk: int) -> Optional[dict]:
    """Return the question_items row by PK(id)."""
    store = get_store()
    return store._fetch_one(store._sql("question_items.get_question_item_by_pk"), [pk])


def list_by_question(question_pk: int) -> list[dict]:
    """List all items for the question PK (seq ASC)."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.get_question_items"), [question_pk])


def list_unanswered(question_pk: int) -> list[dict]:
    """List unanswered items where answer_count = 0 (seq ASC)."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.get_unanswered_items"), [question_pk])


def get_max_seq(question_pk: int) -> int:
    """Return the maximum seq for the question (0 if there are no items)."""
    store = get_store()
    row = store._fetch_one(store._sql("question_items.get_max_seq"), [question_pk])
    return row.get("max_seq", 0) if row else 0


def insert(
    question_pk: int,
    seq: int,
    body: str,
    title: Optional[str] = None,
    asker_kind: str = "human",
    options: str = "[]",
) -> None:
    """question_items INSERT (DB0006 §3.3 — title + asker_kind; DB0007 §4 — options).

    ``options`` is the serialized [{"id", "label"}] JSON array (DB0007 §2); the caller
    validates and serializes it (L0008 §2.2/§2.3).
    """
    store = get_store()
    store._execute(
        store._sql("question_items.insert_question_item"),
        [question_pk, seq, title, body, asker_kind, options],
    )


def increment_answer_count(pk: int) -> None:
    """answer_count += 1."""
    store = get_store()
    store._execute(store._sql("question_items.increment_answer_count"), [pk])


def list_by_doc(doc_id: str) -> list[dict]:
    """List question_items for a document's container (seq ASC) — DB0006 §5.2."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.list_by_doc"), [doc_id])


def qa_bundle_by_doc(doc_id: str) -> list[dict]:
    """Flattened question + answer rows for a document (ment 조립 데이터 제공자, L0007 §6).

    Each row: {seq, title, body, asker_kind, options, author_kind, answer_body,
    answer_selected_options}. A question with no answer yields one row with
    answer_body/author_kind = NULL (LEFT JOIN).
    """
    store = get_store()
    return store._fetch_all(store._sql("question_items.qa_bundle_by_doc"), [doc_id])
