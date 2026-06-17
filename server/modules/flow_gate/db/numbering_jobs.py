"""Numbering job queue CRUD."""
from __future__ import annotations
from typing import Optional, Any
from .connection import get_store, now_iso


def get_by_id(job_id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM numbering_jobs WHERE id = ?", [job_id]
    )


def list_by_project(project_id: str, status: str | None = None) -> list[dict]:
    store = get_store()
    if status:
        return store._fetch_all(
            "SELECT * FROM numbering_jobs WHERE project_id = ? AND status = ? "
            "ORDER BY created_at DESC",
            [project_id, status],
        )
    return store._fetch_all(
        "SELECT * FROM numbering_jobs WHERE project_id = ? ORDER BY created_at DESC",
        [project_id],
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO numbering_jobs "
        "(project_id, requested_by, target, from_width, to_width, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            data["project_id"], data["requested_by"], data["target"],
            data["from_width"], data["to_width"],
            data.get("status", "queued"), data.get("created_at", now),
        ],
    )
    row = store._fetch_one("SELECT * FROM numbering_jobs ORDER BY id DESC LIMIT 1")
    return row  # type: ignore[return-value]


def update(job_id: int, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k != "id"}
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE numbering_jobs SET {set_clause} WHERE id = ?",
        [*updates.values(), job_id],
    )
    return get_by_id(job_id)


def delete(job_id: int) -> None:
    get_store()._execute("DELETE FROM numbering_jobs WHERE id = ?", [job_id])
