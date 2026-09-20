"""document_revisions table CRUD (D020 §5-4)."""
from __future__ import annotations

from typing import Any, Optional

from .connection import get_store, now_iso, iso_days_ago


def get_by_id(revision_id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM document_revisions WHERE id = ?", [revision_id]
    )


def list_by_doc(doc_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM document_revisions WHERE doc_id = ? ORDER BY revision_no DESC",
        [doc_id],
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO document_revisions "
        "(doc_id, revision_no, backup_path, edit_reason, linked_doc_id, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            data["doc_id"], data["revision_no"], data["backup_path"],
            data["edit_reason"], data.get("linked_doc_id"),
            data["created_by"], data.get("created_at", now),
        ],
    )
    row = store._fetch_one(
        "SELECT * FROM document_revisions ORDER BY id DESC LIMIT 1"
    )
    return row  # type: ignore[return-value]


def delete_old(days: int = 30) -> list[dict]:
    """Return records older than days based on created_at; the caller is responsible for deleting files."""
    # Cutoff computed in Python and bound — portable across backends (0088).
    return get_store()._fetch_all(
        "SELECT * FROM document_revisions WHERE created_at < ?",
        [iso_days_ago(days)],
    )


def delete_by_ids(ids: list[int]) -> None:
    if not ids:
        return
    placeholders = ", ".join(["?"] * len(ids))
    get_store()._execute(
        f"DELETE FROM document_revisions WHERE id IN ({placeholders})",
        ids,
    )
