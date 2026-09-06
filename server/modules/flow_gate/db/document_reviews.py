"""CRUD for AI review results in the document_reviews table.

A review is a child record attached to its target document, not a document itself (there is no V document).
Use the same get_store() plus inline SQL pattern as the closest sibling table, document_revisions.py.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


def insert_review(
    doc_id: str,
    revision_no: int,
    reviewer_id: str,
    verdict: str,
    findings_json: str,
    comment: Optional[str],
    reviewed_at: str,
    review_run_id: Optional[str] = None,
    requested_provider_id: Optional[str] = None,
    actual_provider_id: Optional[str] = None,
    actual_provider_name: Optional[str] = None,
    provider_source: Optional[str] = None,
    attempt_no: Optional[int] = None,
    fallback_used: Optional[bool] = None,
) -> dict:
    """Insert one review result and return the inserted row.

    findings_json is a JSON array string such as [{"locus":..,"note":..}].
    The server derives the finding count with len(findings); the AI does not provide it.
    """
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO document_reviews "
        "(doc_id, revision_no, reviewer_id, verdict, findings, comment, reviewed_at, created_at, updated_at, "
        "review_run_id, requested_provider_id, actual_provider_id, actual_provider_name, "
        "provider_source, attempt_no, fallback_used) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            doc_id, revision_no, reviewer_id, verdict, findings_json, comment, reviewed_at, now, now,
            review_run_id, requested_provider_id, actual_provider_id, actual_provider_name,
            provider_source, attempt_no,
            None if fallback_used is None else (1 if fallback_used else 0),
        ],
    )
    row = store._fetch_one(
        "SELECT * FROM document_reviews ORDER BY id DESC LIMIT 1"
    )
    return row  # type: ignore[return-value]


def list_by_doc(doc_id: str) -> list[dict]:
    """Return the document's full review history, newest first."""
    return get_store()._fetch_all(
        "SELECT * FROM document_reviews WHERE doc_id = ? ORDER BY created_at DESC, id DESC",
        [doc_id],
    )


def get_latest_by_doc(doc_id: str) -> Optional[dict]:
    """Return the document's latest review."""
    return get_store()._fetch_one(
        "SELECT * FROM document_reviews WHERE doc_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        [doc_id],
    )
