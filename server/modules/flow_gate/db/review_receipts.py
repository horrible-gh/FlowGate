"""Durable, single-use preflight receipts for AI review submissions."""
from __future__ import annotations

from typing import Optional
from .connection import get_store, now_iso


def supersede_active(token_id: str, superseded_at: str) -> None:
    get_store()._execute(
        "UPDATE review_dry_run_receipts SET superseded_at = ?, updated_at = ? "
        "WHERE token_id = ? AND used_at IS NULL AND superseded_at IS NULL",
        [superseded_at, superseded_at, token_id],
    )


def create(*, receipt_id: str, token_id: str, project_id: str, group_id: Optional[str],
           doc_id: str, revision_no: int, payload_identity: str, issued_at: str,
           expires_at: str, encoding_provenance: Optional[str]) -> dict:
    store = get_store()
    store._execute(
        "INSERT INTO review_dry_run_receipts "
        "(receipt_id, token_id, project_id, group_id, doc_id, revision_no, action_scope, "
        "payload_identity, issued_at, expires_at, used_at, superseded_at, "
        "encoding_provenance, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'review', ?, ?, ?, NULL, NULL, ?, ?, ?)",
        [receipt_id, token_id, project_id, group_id, doc_id, revision_no,
         payload_identity, issued_at, expires_at, encoding_provenance, issued_at, issued_at],
    )
    row = store._fetch_one(
        "SELECT * FROM review_dry_run_receipts WHERE receipt_id = ?", [receipt_id]
    )
    if row is None:
        raise RuntimeError("review receipt readback failed after insert")
    return row


def get(receipt_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM review_dry_run_receipts WHERE receipt_id = ?", [receipt_id]
    )


def claim(*, receipt_id: str, token_id: str, project_id: str, group_id: Optional[str],
          doc_id: str, revision_no: int, payload_identity: str, claimed_at: str) -> bool:
    """CAS claim the exact still-live receipt. Must run in the review transaction."""
    affected = get_store()._execute_affected(
        "UPDATE review_dry_run_receipts SET used_at = ?, updated_at = ? "
        "WHERE receipt_id = ? AND token_id = ? AND project_id = ? "
        "AND ((group_id = ?) OR (group_id IS NULL AND ? IS NULL)) "
        "AND doc_id = ? AND revision_no = ? AND action_scope = 'review' "
        "AND payload_identity = ? AND used_at IS NULL AND superseded_at IS NULL "
        "AND expires_at >= ?",
        [claimed_at, claimed_at, receipt_id, token_id, project_id, group_id, group_id,
         doc_id, revision_no, payload_identity, claimed_at],
    )
    return affected == 1
