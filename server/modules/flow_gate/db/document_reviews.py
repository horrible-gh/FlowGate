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
    # 0535 T0007 §2: the INSERT and the readback of the row it created are ONE
    # transaction. Split apart they were two autocommitted calls, so a readback (or
    # driver) failure after a successful INSERT left the review row behind while the
    # caller answered 500 -- a durable half-write no retry could clean up.
    with store.transaction() as s:
        s._execute(
            "INSERT INTO document_reviews "
            "(doc_id, revision_no, reviewer_id, verdict, findings, comment, reviewed_at, created_at, updated_at, "
            "review_run_id, requested_provider_id, actual_provider_id, actual_provider_name, "
            "provider_source, attempt_no, fallback_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                doc_id, revision_no, reviewer_id, verdict, findings_json, comment, reviewed_at, now, now,
                review_run_id, requested_provider_id, actual_provider_id, actual_provider_name,
                provider_source, attempt_no,
                # fallback_used binds as a plain Optional[bool] (0535 T0005): the old
                # 1/0-int coercion here was written for SQLite, but PostgreSQL's
                # fallback_used BOOLEAN column rejects an integer parameter outright
                # ("column ... is of type boolean but expression is of type integer").
                # sqlite3/psycopg2/pymysql each bind a native Python bool correctly for
                # their own column type (SQLite INTEGER CHECK(0,1), PostgreSQL/MySQL
                # BOOLEAN), so passing it straight through is the dialect-portable form.
                fallback_used,
            ],
        )
        # Identify the row THIS statement inserted instead of re-reading "the newest
        # row in the table": ORDER BY id DESC LIMIT 1 hands back a concurrent writer's
        # review whenever two reviews land together. last_insert_rowid() is
        # connection-scoped and dialect.translate() rewrites it per backend
        # (PostgreSQL lastval(), MySQL LAST_INSERT_ID()), so reading it on the
        # transaction's own connection is exact on all three -- the same recipe
        # events.insert_event()/messages.create() already use.
        rid_row = s._fetch_one("SELECT last_insert_rowid() AS rid")
        new_id = rid_row.get("rid") if rid_row else None
        row = (
            s._fetch_one("SELECT * FROM document_reviews WHERE id = ?", [new_id])
            if new_id is not None else None
        )
        if row is None:
            # Fail closed inside the transaction: raising here rolls the INSERT back,
            # so "the caller saw an error" and "no review row exists" stay the same fact.
            raise RuntimeError(
                "document_reviews readback failed after insert "
                f"(doc_id={doc_id}, id={new_id!r})"
            )
        return row


def review_provider_payload(row: dict) -> dict:
    """The provider-provenance block every read surface exposes (0535 T0007 §2).

    Both surfaces (api/v1/document_routes._shape_review and
    documents/routers/documents._shape_review) build their review_provider block from
    here so the key set and the fallback_used semantic cannot drift apart.

    fallback_used is Optional[bool] end to end, and the raw value coming back out of a
    driver differs per dialect: SQLite's INTEGER CHECK(0,1) column reads as 0/1, MySQL's
    BOOLEAN/TINYINT as 0/1, PostgreSQL's BOOLEAN as a Python bool -- and NULL as None
    everywhere. The public JSON is true/false/null in all three cases, and null keeps
    meaning "there was no evidence to decide", never "no fallback happened".
    """
    raw = row.get("fallback_used")
    return {
        "run_id": row.get("review_run_id"),
        "requested_provider_id": row.get("requested_provider_id"),
        "actual_provider_id": row.get("actual_provider_id"),
        "actual_provider_name": row.get("actual_provider_name"),
        "provider_source": row.get("provider_source"),
        "attempt_no": row.get("attempt_no"),
        "fallback_used": None if raw is None else bool(raw),
    }


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
