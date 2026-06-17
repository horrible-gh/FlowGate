"""Workflow event CRUD."""
from __future__ import annotations
from typing import Optional, Any
from .connection import get_store, now_iso


def get_by_id(event_id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM workflow_events WHERE id = ?", [event_id]
    )


def list_by_project(project_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM workflow_events WHERE project_id = ? "
        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [project_id, limit, offset],
    )


def list_by_document(document_id: int, limit: int = 100) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM workflow_events WHERE document_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        [document_id, limit],
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO workflow_events "
        "(event_type, project_id, group_id, document_id, actor_user_id, "
        "from_state, to_state, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            data["event_type"], data["project_id"], data.get("group_id"),
            data.get("document_id"), data["actor_user_id"],
            data.get("from_state"), data.get("to_state"),
            data.get("metadata"), data.get("created_at", now),
        ],
    )
    row = store._fetch_one(
        "SELECT * FROM workflow_events ORDER BY id DESC LIMIT 1"
    )
    return row  # type: ignore[return-value]


def delete(event_id: int) -> None:
    get_store()._execute("DELETE FROM workflow_events WHERE id = ?", [event_id])
