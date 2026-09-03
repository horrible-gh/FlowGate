"""Durable last-result snapshot for project terminal-slot cleanup."""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

from .connection import get_store, now_iso

_memory: dict[str, dict] = {}
_lock = threading.RLock()


def _using_memory() -> bool:
    # Exercise the durable SQL path whenever a test configured a database.
    # Only direct unit tests with no store connection use the process-local mirror.
    return getattr(get_store(), "_db", None) is None


def empty() -> dict:
    return {"last_run_at": None, "last_run_status": None,
            "last_cleaned_count": 0, "pending": []}


def get(project_id: str) -> dict:
    if _using_memory():
        with _lock:
            return dict(_memory.get(project_id) or empty())
    row = get_store()._fetch_one(
        "SELECT * FROM git_terminal_cleanup_snapshots WHERE project_id = ?", [project_id]
    )
    if not row:
        return empty()
    try:
        pending = json.loads(row.get("pending_json") or "[]")
    except (TypeError, ValueError):
        pending = []
    return {"last_run_at": row.get("last_run_at"),
            "last_run_status": row.get("last_run_status"),
            "last_cleaned_count": int(row.get("last_cleaned_count") or 0),
            "pending": pending if isinstance(pending, list) else []}


def put(project_id: str, status: str, cleaned_count: int, pending: list[dict]) -> dict:
    if status not in ("ok", "partial", "failed"):
        raise ValueError("invalid terminal cleanup status")
    snapshot = {"last_run_at": now_iso(), "last_run_status": status,
                "last_cleaned_count": max(0, int(cleaned_count)),
                "pending": [dict(row) for row in pending]}
    if _using_memory():
        with _lock:
            _memory[project_id] = dict(snapshot)
        return snapshot
    payload = json.dumps(snapshot["pending"], ensure_ascii=False, separators=(",", ":"))
    store = get_store()
    with store.transaction():
        store._execute(
            "INSERT INTO git_terminal_cleanup_snapshots "
            "(project_id,last_run_at,last_run_status,last_cleaned_count,pending_json) "
            "VALUES (?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
            "last_run_at=excluded.last_run_at,last_run_status=excluded.last_run_status,"
            "last_cleaned_count=excluded.last_cleaned_count,pending_json=excluded.pending_json",
            [project_id, snapshot["last_run_at"], status,
             snapshot["last_cleaned_count"], payload],
        )
    return snapshot

