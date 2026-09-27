"""document_revisions table CRUD (D020 §5-4)."""
from __future__ import annotations

from typing import Any, Optional

from .connection import get_store, now_iso, iso_days_ago


def get_by_id(revision_id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM document_revisions WHERE id = ?", [revision_id]
    )


class RevisionAmbiguityError(RuntimeError):
    """More than one recovery source exists for one document revision."""


def list_by_doc(doc_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM document_revisions WHERE doc_id = ? "
        "ORDER BY revision_no DESC, id ASC",
        [doc_id],
    )


def list_by_doc_revision(doc_id: str, revision_no: int) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM document_revisions WHERE doc_id = ? AND revision_no = ? "
        "ORDER BY id ASC",
        [doc_id, int(revision_no)],
    )


def get_single_by_doc_revision(doc_id: str, revision_no: int) -> Optional[dict]:
    """Return the one recovery source for a revision, failing closed on ambiguity."""
    rows = list_by_doc_revision(doc_id, revision_no)
    if len(rows) > 1:
        raise RevisionAmbiguityError(
            f"multiple recovery sources for {doc_id} revision {int(revision_no)}"
        )
    return rows[0] if rows else None


def _insert(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO document_revisions "
        "(doc_id, revision_no, backup_path, edit_reason, linked_doc_id, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            data["doc_id"], int(data["revision_no"]), data["backup_path"],
            data["edit_reason"], data.get("linked_doc_id"),
            data["created_by"], data.get("created_at", now),
        ],
    )
    row = get_single_by_doc_revision(data["doc_id"], int(data["revision_no"]))
    if row is None:  # pragma: no cover - a successful INSERT must be observable
        raise RuntimeError("document revision insert was not observable")
    return row


def create_once(data: dict[str, Any]) -> tuple[dict, bool]:
    """Get or create exactly one row for ``(doc_id, revision_no)``.

    The no-op document UPDATE takes a database row lock before the existence check.
    It serializes competing writers across processes without expanding the legacy
    schema or assuming that existing installations are clean enough for a UNIQUE
    migration. Existing ambiguity is surfaced rather than choosing a restore source.
    """
    store = get_store()
    doc_id = data["doc_id"]
    revision_no = int(data["revision_no"])
    with store.transaction():
        store._execute(
            "UPDATE documents SET doc_id = doc_id WHERE doc_id = ?", [doc_id]
        )
        existing = get_single_by_doc_revision(doc_id, revision_no)
        if existing is not None:
            return existing, False
        return _insert(data), True


def create(data: dict[str, Any]) -> dict:
    """Backward-compatible idempotent create used by existing save paths."""
    row, _created = create_once(data)
    return row


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
