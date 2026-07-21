"""Return point snapshot persistence for the reverse time-machine workflow."""
from __future__ import annotations

from typing import Any, Optional

from .connection import get_store, now_iso


def get_by_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM workflow_return_points WHERE group_id = ?",
        [group_id],
    )


def create(group_id: str, front_seq: int, root_prev_status: Optional[str] = None) -> dict:
    now = now_iso()
    store = get_store()
    store._execute(
        "INSERT INTO workflow_return_points"
        "(group_id, front_seq, root_prev_status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [group_id, front_seq, root_prev_status, now, now],
    )
    row = get_by_group(group_id)
    if row is None:
        raise RuntimeError(f"Failed to create return point for group: {group_id}")
    return row


def ensure(group_id: str, front_seq: int, root_prev_status: Optional[str] = None) -> dict:
    existing = get_by_group(group_id)
    if existing is None:
        return create(group_id, front_seq, root_prev_status)
    # A pre-existing return point is only extended (a nested rewind to an earlier step): bump
    # front_seq upward if needed, but never touch root_prev_status. The "home" root status is the
    # one captured when the workflow first left its original state; a nested rewind must preserve
    # it, not overwrite it with the mid-rewind (wf_in_progress) status. Callers that want a fresh
    # capture delete the stale return point first (0158 gate relaxation).
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


def summary(group_id: str) -> Optional[dict]:
    """A group's whole return-point payload in one statement (0291 T2, CH0016).

    문서 응답 하나를 만들 때마다 아래 넷이 통째로 돌았다::

        SELECT * FROM workflow_return_points WHERE group_id = ?        -- get_by_group
        SELECT ... FROM documents WHERE group_id = ? AND seq = ?       -- get_front_doc
        SELECT MIN(d.seq) ... WHERE s.return_point_id = ?              -- current_pending_min_seq
        SELECT COUNT(*) ... WHERE return_point_id = ?                  -- count_docs

    CH0016 의 덤프에 이 4종 세트가 네 번 반복된다(응답 4개 × 4). 뒤의 셋은 전부 첫
    쿼리가 가져온 ``id`` / ``front_seq`` 만 있으면 되는 파생 조회라, 상관 서브쿼리로
    같은 문장 안에서 참조하면 왕복이 하나로 줄어든다. 문서 응답당 4 → 1.

    개별 함수들은 그대로 남는다. 단독으로 부르는 자리가 따로 있고(재개 판정에서
    ``current_pending_min_seq`` 만 본다), 거기서는 좁은 쿼리 하나가 이 합침보다 싸다.

    반환값은 "그 반환점이 없으면 None". 있으면 다음 키를 준다:
    ``id`` / ``front_seq`` / ``front_title`` / ``front_type_code`` /
    ``restorable_count`` / ``current_min_seq``.
    """
    row = get_store()._fetch_one(
        "SELECT rp.id AS id, rp.front_seq AS front_seq, "
        "       fd.title AS front_title, fd.type_code AS front_type_code, "
        "       (SELECT COUNT(*) FROM workflow_return_point_docs s "
        "         WHERE s.return_point_id = rp.id) AS restorable_count, "
        "       (SELECT MIN(d.seq) FROM workflow_return_point_docs s2 "
        "          JOIN documents d ON d.doc_id = s2.doc_id "
        "         WHERE s2.return_point_id = rp.id "
        "           AND d.doc_review_status = 'pending_review') AS current_min_seq "
        "FROM workflow_return_points rp "
        # get_front_doc 과 같은 매칭이다. LEFT JOIN 인 이유: front_seq 가 가리키는
        # 문서가 지워졌어도 반환점 자체는 존재하며, 그때 front_label 만 비어야 한다.
        "LEFT JOIN documents fd ON fd.group_id = rp.group_id AND fd.seq = rp.front_seq "
        "WHERE rp.group_id = ?",
        [group_id],
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "front_seq": row["front_seq"],
        "front_title": row.get("front_title"),
        "front_type_code": row.get("front_type_code"),
        "restorable_count": int(row.get("restorable_count") or 0),
        "current_min_seq": None if row.get("current_min_seq") is None else int(row["current_min_seq"]),
    }


def delete(return_point_id: int) -> None:
    get_store()._execute(
        "DELETE FROM workflow_return_points WHERE id = ?",
        [return_point_id],
    )
