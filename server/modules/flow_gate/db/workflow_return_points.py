"""Return point snapshot persistence for the reverse time-machine workflow."""
from __future__ import annotations

from typing import Any, Optional

from .connection import get_store, now_iso


def get_by_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM workflow_return_points WHERE group_id = ?",
        [group_id],
    )


def create(group_id: str, front_seq: int) -> dict:
    now = now_iso()
    store = get_store()
    store._execute(
        "INSERT INTO workflow_return_points(group_id, front_seq, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        [group_id, front_seq, now, now],
    )
    row = get_by_group(group_id)
    if row is None:
        raise RuntimeError(f"Failed to create return point for group: {group_id}")
    return row


def ensure(group_id: str, front_seq: int) -> dict:
    existing = get_by_group(group_id)
    if existing is None:
        return create(group_id, front_seq)
    store = get_store()
    store._execute(
        "UPDATE workflow_return_points "
        "SET front_seq = CASE WHEN front_seq < ? THEN ? ELSE front_seq END, updated_at = ? "
        "WHERE group_id = ?",
        [front_seq, front_seq, now_iso(), group_id],
    )
    refreshed = get_by_group(group_id)
    if refreshed is None:
        raise RuntimeError(f"Return point disappeared for group: {group_id}")
    return refreshed


def add_doc_if_absent(
    *,
    return_point_id: int,
    doc_id: str,
    seq: int,
    prev_status: str,
    fingerprint: str,
) -> None:
    store = get_store()
    existing = store._fetch_one(
        "SELECT 1 AS ok FROM workflow_return_point_docs "
        "WHERE return_point_id = ? AND doc_id = ?",
        [return_point_id, doc_id],
    )
    if existing is not None:
        return
    store._execute(
        "INSERT INTO workflow_return_point_docs"
        "(return_point_id, doc_id, seq, prev_status, fingerprint) "
        "VALUES (?, ?, ?, ?, ?)",
        [return_point_id, doc_id, seq, prev_status, fingerprint],
    )


def count_docs(return_point_id: int) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS count FROM workflow_return_point_docs WHERE return_point_id = ?",
        [return_point_id],
    )
    return int(row.get("count") or 0) if row else 0


def current_pending_min_seq(return_point_id: int) -> Optional[int]:
    """Lowest seq still pending among THIS return point's snapshot docs (= the current
    workflow head within the rewound range). Scoped to the return point's own docs so
    unrelated pending documents in the group (phantoms / other work) can't skew it —
    0142 rework."""
    row = get_store()._fetch_one(
        "SELECT MIN(d.seq) AS min_seq FROM workflow_return_point_docs s "
        "JOIN documents d ON d.doc_id = s.doc_id "
        "WHERE s.return_point_id = ? AND d.doc_review_status = 'pending_review'",
        [return_point_id],
    )
    if row is None or row.get("min_seq") is None:
        return None
    return int(row["min_seq"])


def list_candidates(return_point_id: int, destination_seq: int) -> list[dict[str, Any]]:
    return get_store()._fetch_all(
        "SELECT d.doc_id, d.seq, d.type_code, d.title, d.file_path, d.project_id, "
        "       d.branch, d.group_id, s.prev_status, s.fingerprint "
        "FROM workflow_return_point_docs s "
        "JOIN documents d ON d.doc_id = s.doc_id "
        "WHERE s.return_point_id = ? AND s.seq <= ? "
        "ORDER BY s.seq ASC",
        [return_point_id, destination_seq],
    )


def get_front_doc(group_id: str, front_seq: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT doc_id, seq, type_code, title FROM documents WHERE group_id = ? AND seq = ?",
        [group_id, front_seq],
    )


def delete(return_point_id: int) -> None:
    get_store()._execute(
        "DELETE FROM workflow_return_points WHERE id = ?",
        [return_point_id],
    )
