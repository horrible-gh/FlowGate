"""The `attachments` table — every query DB0013 §4 lists, and nothing else.

flowgate.default.0060 DB0013 §4. Same shape as the other db submodules
(``get_store()._fetch_one/_fetch_all/_execute`` with ``?`` placeholders), so the dialect
translator rewrites these for MySQL/PostgreSQL exactly as it does everywhere else.

This is the only persistent store for attachment metadata (L0012 preamble). If a query is
not in DB0013 §4 it is not here.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db.connection import get_store, now_iso


def registry_get(doc_id: str, filename: str) -> Optional[dict]:
    """§2-6 J2 — exact match, case included. Uses ux_attachments_doc_filename."""
    return get_store()._fetch_one(
        "SELECT * FROM attachments WHERE doc_id = ? AND filename = ?",
        [doc_id, filename],
    )


def registry_has(doc_id: str, filename: str) -> bool:
    """§2-4 D4 — cheap pre-exclusion during dedupe."""
    row = get_store()._fetch_one(
        "SELECT 1 AS present FROM attachments WHERE doc_id = ? AND filename = ? LIMIT 1",
        [doc_id, filename],
    )
    return row is not None


def registry_has_same(doc_id: str, content_sha256: str) -> bool:
    """§2-11 M4 — makes re-running the legacy migration idempotent."""
    row = get_store()._fetch_one(
        "SELECT 1 AS present FROM attachments WHERE doc_id = ? AND content_sha256 = ? LIMIT 1",
        [doc_id, content_sha256],
    )
    return row is not None


def registry_count(doc_id: str) -> int:
    """§2-5 A5 — per-document ceiling."""
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM attachments WHERE doc_id = ?", [doc_id]
    )
    return int(row["cnt"]) if row else 0


def registry_list(doc_id: str) -> list[dict]:
    """P0011 §3 list order: uploaded_at ASC, then filename ASC.

    That is exactly ``idx_attachments_doc_uploaded``'s column order, so the index returns
    the rows already sorted.
    """
    return get_store()._fetch_all(
        "SELECT * FROM attachments WHERE doc_id = ? ORDER BY uploaded_at ASC, filename ASC",
        [doc_id],
    )


def registry_insert(
    *,
    doc_id: str,
    original_filename: str,
    filename: str,
    file_path: str,
    size: int,
    content_type: str,
    content_sha256: str,
    uploaded_by: Optional[str],
    uploaded_at: Optional[str] = None,
    is_legacy_migrated: bool = False,
    store=None,
) -> None:
    """§2-5 A8 / §2-11 M6.

    ``store`` lets the upload service pass the connection it already opened a transaction
    on, so a whole request's rows commit or vanish together (DB0013 §4, transaction boundary).
    """
    st = store or get_store()
    now = now_iso()
    st._execute(
        "INSERT INTO attachments "
        "(doc_id, original_filename, filename, file_path, size, content_type, "
        " content_sha256, uploaded_by, uploaded_at, is_legacy_migrated, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            doc_id,
            original_filename,
            filename,
            file_path,
            int(size),
            content_type,
            content_sha256,
            uploaded_by,
            uploaded_at or now,
            1 if is_legacy_migrated else 0,
            now,
        ],
    )


def registry_delete(doc_id: str, filename: str) -> None:
    """§2-8 X5."""
    get_store()._execute(
        "DELETE FROM attachments WHERE doc_id = ? AND filename = ?", [doc_id, filename]
    )


def attachment_registry_paths(project_id: str) -> set[str]:
    """§2-12 W2 — every registered attachment path in one project.

    The numbering scan joins this in so a registered attachment is an owned file rather
    than an orphan. Batch query, never on a hot path.
    """
    rows = get_store()._fetch_all(
        "SELECT a.file_path AS file_path FROM attachments a "
        "JOIN documents d ON d.doc_id = a.doc_id WHERE d.project_id = ?",
        [project_id],
    )
    return {r["file_path"] for r in rows if r.get("file_path")}
