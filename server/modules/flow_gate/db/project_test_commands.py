"""project_test_commands CRUD — sqloader.load pattern (flowgate.default.0152 L0004).

Inline SQL is prohibited; only use SQL registered in queries.json (`test_commands` namespace).
Mirrors messages.py: get_store()._sql(...) + _fetch_*/_execute + now_iso(), with the
INSERT + last_insert_rowid() recovery wrapped in one store.transaction().

Physical delete never happens (L §2-2): every row carries status 'active' | 'suppressed'.
A suppressed row is a tombstone — it keeps the (project, command) slot so auto-reflection skips
it, and a manual re-add of the same command revives the same row (id preserved). Callers that
must distinguish states do so in the service layer; get_by_id / find_by_command return rows of
either status so revive/patch/delete can read a suppressed row.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


def list_active(project: str) -> list[dict]:
    """Active rows only, ordered last_success_at DESC (nulls last), id ASC (L §2-5)."""
    store = get_store()
    return store._fetch_all(store._sql("test_commands.list_active"), [project])


def count_active(project: str) -> int:
    store = get_store()
    row = store._fetch_one(store._sql("test_commands.count_active"), [project])
    return int(row["cnt"]) if row and row.get("cnt") is not None else 0


def get_by_id(project: str, command_id: int) -> Optional[dict]:
    """Single row by (project, id), any status, or None."""
    store = get_store()
    return store._fetch_one(store._sql("test_commands.get_by_id"), [project, command_id])


def find_by_command(project: str, command: str) -> Optional[dict]:
    """Look up a row by normalized command, INCLUDING suppressed rows (identity check)."""
    store = get_store()
    return store._fetch_one(
        store._sql("test_commands.find_by_command"), [project, command]
    )


def insert(
    project: str,
    command: str,
    description: str,
    origin: str,
    last_success_at: Optional[str],
    status: str = "active",
) -> dict:
    """Insert a new row and return it (INSERT + last_insert_rowid() share one connection)."""
    store = get_store()
    now = now_iso()
    with store.transaction() as s:
        s._execute(
            store._sql("test_commands.insert"),
            [project, command, description, origin, status, last_success_at, now, now],
        )
        row = s._fetch_one("SELECT last_insert_rowid() AS rid")
        new_id = row["rid"] if row else None
        return s._fetch_one(store._sql("test_commands.get_by_id"), [project, new_id])


def update_row(project: str, command_id: int, updates: dict) -> Optional[dict]:
    """Read-modify-write the mutable column set; return the updated row, or None if absent.

    `updates` may carry any subset of
    {command, description, origin, status, last_success_at}; unspecified fields keep their value.
    """
    store = get_store()
    current = get_by_id(project, command_id)
    if current is None:
        return None
    command = updates.get("command", current["command"])
    description = updates.get("description", current["description"])
    origin = updates.get("origin", current["origin"])
    status = updates.get("status", current["status"])
    last_success_at = updates.get("last_success_at", current["last_success_at"])
    store._execute(
        store._sql("test_commands.update_row"),
        [command, description, origin, status, last_success_at, now_iso(), project, command_id],
    )
    return get_by_id(project, command_id)
