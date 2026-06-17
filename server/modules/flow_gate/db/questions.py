"""questions CRUD — follows the sqloader.load pattern.

Inline SQL is prohibited. Only use SQL registered in queries.json.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store


def get_by_q_id(q_id: str) -> Optional[dict]:
    """Return the questions row by q_id (text)."""
    store = get_store()
    return store._fetch_one(store._sql("questions.get_question_by_id"), [q_id])


def get_by_pk(pk: int) -> Optional[dict]:
    """Return the questions row by PK(id)."""
    store = get_store()
    return store._fetch_one(store._sql("questions.get_question_by_pk"), [pk])


def list_by_project(project_id: str) -> list[dict]:
    """List all Q records for the project (created_at DESC)."""
    store = get_store()
    return store._fetch_all(store._sql("questions.get_questions_by_project"), [project_id])


def list_by_project_status(project_id: str, status: str) -> list[dict]:
    """List Q records filtered by project + status (created_at DESC)."""
    store = get_store()
    return store._fetch_all(
        store._sql("questions.get_questions_by_project_status"), [project_id, status]
    )


def insert(
    q_id: str,
    project_id: Optional[str],
    title: str,
    created_by: str,
    pm_id: Optional[str],
    related_doc: Optional[str],
) -> None:
    """questions INSERT (status = pending)."""
    store = get_store()
    store._execute(
        store._sql("questions.insert_question"),
        [q_id, project_id, title, created_by, pm_id, related_doc],
    )


def get_next_count() -> int:
    """Return the current Q record count (for numbering calculation)."""
    store = get_store()
    row = store._fetch_one(store._sql("questions.get_next_q_number"), [])
    return row.get("count", 0) if row else 0


def update_status(q_id: str, status: str) -> None:
    """Update Q status (pending <-> done)."""
    store = get_store()
    store._execute(store._sql("questions.update_question_status"), [status, q_id])


# ── Document-bound container (Q/A/V redesign, DB0006 §3.1/§5.2) ───────────────────────

def get_container_by_doc(doc_id: str) -> Optional[dict]:
    """Return the questions container row bound to a document (doc_id), or None."""
    store = get_store()
    return store._fetch_one(store._sql("questions.get_container_by_doc"), [doc_id])


def insert_container_for_doc(
    doc_id: str,
    project_id: Optional[str],
    title: str,
    created_by: str,
) -> None:
    """Create the per-document questions container (q_id := doc_id, status=pending).

    AI registration path passes created_by='u-system' (reserved system user, §3.3).
    """
    store = get_store()
    store._execute(
        store._sql("questions.insert_container_for_doc"),
        [doc_id, doc_id, project_id, title, created_by],
    )


def list_open_items(project_id: Optional[str] = None) -> list[dict]:
    """List unanswered items across documents (aggregates 'open queries', D0005 §3.7).

    project_id=None → all projects; otherwise scoped to that project.
    Each row: {doc_id, seq, title, type_code}. type_code is the host document's
    type (so the dashboard can open the real document, not a Q-tree viewer) and
    may be NULL if the document row is missing.
    """
    store = get_store()
    return store._fetch_all(
        store._sql("questions.list_open_items_by_project"), [project_id, project_id]
    )
