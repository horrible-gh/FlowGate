"""Durable AI attachment upload requests (migration 181, flowgate.chat.0017 CH0002).

One row per file an AI run handed over for a person's approval. The row is the single
source of truth for the decision: the browser renders it and posts a decision, nothing
else. Every status change is a compare-and-set (``WHERE status IN (...)``), so two people
deciding at once, an expiry sweep and a startup recovery can never both win.

The row is deliberately NOT tied to the AI run's lifecycle: ``ai_run_id`` / ``graph_run_id``
/ ``token_id`` are audit columns, no run hook reads or closes rows here, and a decision
only uses columns copied in at request time.

Timestamps use ``now_iso()`` (JST, seconds) everywhere, so lexicographic comparison of
``expires_at`` / ``backup_expires_at`` against ``now_iso()`` is a time comparison.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from .connection import get_store, now_iso

STATUS_PENDING = "pending"
STATUS_APPROVING = "approving"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"

STATUSES = (STATUS_PENDING, STATUS_APPROVING, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED, STATUS_FAILED)
TERMINAL_STATUSES = (STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED, STATUS_FAILED)
OPEN_STATUSES = (STATUS_PENDING, STATUS_APPROVING)

_COLUMNS = (
    "request_id", "project_id", "group_id", "origin", "source_doc_id", "target_flow_id",
    "target_node_key", "target_doc_id", "target_title", "ai_run_id", "graph_run_id", "token_id", "requested_by",
    "provider_name", "original_filename", "filename", "size", "content_type", "content_sha256",
    "staging_path", "note", "status", "run_start_seq", "expires_at", "created_at", "updated_at",
)


def create(**values) -> dict:
    now = now_iso()
    row = {key: values.get(key) for key in _COLUMNS}
    row["status"] = STATUS_PENDING
    row["created_at"] = row["updated_at"] = now
    row["size"] = int(row["size"] or 0)
    if row.get("run_start_seq") is not None:
        row["run_start_seq"] = int(row["run_start_seq"])
    columns = ", ".join(_COLUMNS)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    get_store()._execute(
        f"INSERT INTO attachment_upload_requests ({columns}) VALUES ({placeholders})",
        [row[key] for key in _COLUMNS],
    )
    return get(row["request_id"])


def get(request_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM attachment_upload_requests WHERE request_id = ?", [request_id]
    )


def transition(request_id: str, from_statuses: Iterable[str], to_status: str, **fields) -> Optional[dict]:
    """CAS one row from any of ``from_statuses`` to ``to_status``; None when it lost."""
    allowed = list(from_statuses)
    if not allowed:
        return None
    sets: dict = {"status": to_status, "updated_at": now_iso()}
    sets.update(fields)
    columns = ", ".join(f"{key} = ?" for key in sets)
    placeholders = ", ".join("?" for _ in allowed)
    affected = get_store()._execute_affected(
        f"UPDATE attachment_upload_requests SET {columns} "
        f"WHERE request_id = ? AND status IN ({placeholders})",
        [*sets.values(), request_id, *allowed],
    )
    return get(request_id) if affected else None


def update_fields(request_id: str, expected_status: str, **fields) -> bool:
    """Set progress fields of a row that is still in ``expected_status`` (no status move)."""
    if not fields:
        return False
    sets = dict(fields)
    sets["updated_at"] = now_iso()
    columns = ", ".join(f"{key} = ?" for key in sets)
    affected = get_store()._execute_affected(
        f"UPDATE attachment_upload_requests SET {columns} WHERE request_id = ? AND status = ?",
        [*sets.values(), request_id, expected_status],
    )
    return bool(affected)


def list_for_source_doc(doc_id: str, limit: int = 500) -> list[dict]:
    """Requests a chat (its CH document) made, oldest first."""
    rows = get_store()._fetch_all(
        "SELECT * FROM attachment_upload_requests WHERE source_doc_id = ? AND origin = 'chat' "
        "ORDER BY created_at DESC, request_id DESC LIMIT ?",
        [doc_id, int(limit)],
    )
    return list(reversed(rows))


def list_for_node(flow_id: str, node_key: str, statuses: Optional[Iterable[str]] = None,
                  limit: int = 200) -> list[dict]:
    """Requests aimed at one card (Flow node), oldest first."""
    params: list = [flow_id, node_key]
    where = "target_flow_id = ? AND target_node_key = ?"
    wanted = list(statuses or [])
    if wanted:
        where += f" AND status IN ({', '.join('?' for _ in wanted)})"
        params.extend(wanted)
    rows = get_store()._fetch_all(
        f"SELECT * FROM attachment_upload_requests WHERE {where} "
        "ORDER BY created_at DESC, request_id DESC LIMIT ?",
        [*params, int(limit)],
    )
    return list(reversed(rows))


def count_pending_by_node(flow_id: str) -> dict[str, int]:
    rows = get_store()._fetch_all(
        "SELECT target_node_key AS node_key, COUNT(*) AS cnt FROM attachment_upload_requests "
        "WHERE target_flow_id = ? AND status IN ('pending', 'approving') GROUP BY target_node_key",
        [flow_id],
    )
    return {row["node_key"]: int(row["cnt"]) for row in rows}


def count_pending_for_graph_run(graph_run_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM attachment_upload_requests "
        "WHERE graph_run_id = ? AND status IN ('pending', 'approving')",
        [graph_run_id],
    )
    return int(row["cnt"]) if row else 0


def count_pending_for_flow(flow_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM attachment_upload_requests "
        "WHERE target_flow_id = ? AND status IN ('pending', 'approving')",
        [flow_id],
    )
    return int(row["cnt"]) if row else 0


def count_open_for_node(flow_id: str, node_key: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM attachment_upload_requests "
        "WHERE target_flow_id = ? AND target_node_key = ? AND status IN ('pending', 'approving')",
        [flow_id, node_key],
    )
    return int(row["cnt"]) if row else 0


def count_for_run(ai_run_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM attachment_upload_requests WHERE ai_run_id = ?", [ai_run_id]
    )
    return int(row["cnt"]) if row else 0


def pending_bytes_for_project(project_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COALESCE(SUM(size), 0) AS total FROM attachment_upload_requests "
        "WHERE project_id = ? AND status IN ('pending', 'approving')",
        [project_id],
    )
    return int((row or {}).get("total") or 0)


def list_expired_pending(now: str, limit: int = 200) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM attachment_upload_requests WHERE status = 'pending' AND expires_at <= ? "
        "ORDER BY expires_at ASC LIMIT ?",
        [now, int(limit)],
    )


def list_by_status(status: str, limit: int = 500) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM attachment_upload_requests WHERE status = ? ORDER BY updated_at ASC LIMIT ?",
        [status, int(limit)],
    )


def list_expired_backups(now: str, limit: int = 200) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM attachment_upload_requests WHERE backup_path IS NOT NULL "
        "AND backup_expires_at IS NOT NULL AND backup_expires_at <= ? "
        "ORDER BY backup_expires_at ASC LIMIT ?",
        [now, int(limit)],
    )


def clear_backup(request_id: str, backup_path: str) -> bool:
    affected = get_store()._execute_affected(
        "UPDATE attachment_upload_requests SET backup_path = NULL, backup_expires_at = NULL, updated_at = ? "
        "WHERE request_id = ? AND backup_path = ?",
        [now_iso(), request_id, backup_path],
    )
    return bool(affected)


def known_staging_ids(project_id: str) -> set[str]:
    """Request ids of a project whose staged file must stay (open rows, and failed rows
    kept for a person to look at until they expire)."""
    rows = get_store()._fetch_all(
        "SELECT request_id FROM attachment_upload_requests WHERE project_id = ? "
        "AND status IN ('pending', 'approving')",
        [project_id],
    )
    return {row["request_id"] for row in rows}


def known_backup_paths(project_id: str) -> set[str]:
    rows = get_store()._fetch_all(
        "SELECT backup_path FROM attachment_upload_requests WHERE project_id = ? AND backup_path IS NOT NULL",
        [project_id],
    )
    return {row["backup_path"] for row in rows if row.get("backup_path")}


# ── per-project settings overrides ──────────────────────────────────────────

def get_project_settings(project_id: str) -> Optional[dict]:
    row = get_store()._fetch_one(
        "SELECT * FROM project_attachment_request_settings WHERE project_id = ?", [project_id]
    )
    if row is None:
        return None
    try:
        values = json.loads(row.get("settings_json") or "{}")
    except (TypeError, ValueError):
        values = {}
    return {
        "project_id": project_id,
        "values": values if isinstance(values, dict) else {},
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


def set_project_settings(project_id: str, values: dict, updated_by: Optional[str]) -> dict:
    store = get_store()
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True)
    now = now_iso()
    with store.transaction():
        existing = store._fetch_one(
            "SELECT project_id FROM project_attachment_request_settings WHERE project_id = ?", [project_id]
        )
        if not values:
            if existing:
                store._execute("DELETE FROM project_attachment_request_settings WHERE project_id = ?", [project_id])
        elif existing:
            store._execute(
                "UPDATE project_attachment_request_settings SET settings_json = ?, updated_at = ?, updated_by = ? "
                "WHERE project_id = ?",
                [payload, now, updated_by, project_id],
            )
        else:
            store._execute(
                "INSERT INTO project_attachment_request_settings (project_id, settings_json, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?)",
                [project_id, payload, now, updated_by],
            )
    return get_project_settings(project_id) or {"project_id": project_id, "values": {}, "updated_at": now,
                                                  "updated_by": updated_by}
