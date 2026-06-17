"""Group/subgroup CRUD."""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Any
from .connection import get_store, now_iso


def get_by_id(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM groups WHERE group_id = ?", [group_id]
    )


def list_groups(
    project_id: str,
    module: str | None = None,
    status: str | None = None,
) -> list[dict]:
    store = get_store()
    sql = "SELECT * FROM groups WHERE project_id = ? AND deleted_at IS NULL"
    params: list = [project_id]
    if module is not None:
        sql += " AND module = ?"
        params.append(module)
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY group_id"
    return store._fetch_all(sql, params)


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO groups (group_id, project_id, module, parent_id, title, priority, "
        "status, created_at, updated_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            data["group_id"], data["project_id"], data.get("module", "none"),
            data.get("parent_id"), data["title"], data.get("priority"),
            data.get("status", "OPEN"), data.get("created_at", now),
            data.get("updated_at", now), data.get("closed_at"),
        ],
    )
    return get_by_id(data["group_id"])  # type: ignore[return-value]


def update(group_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("group_id", "created_at")}
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE groups SET {set_clause} WHERE group_id = ?",
        [*updates.values(), group_id],
    )
    return get_by_id(group_id)


def delete(group_id: str) -> None:
    get_store()._execute("DELETE FROM groups WHERE group_id = ?", [group_id])


# ── Subgroups ────────────────────────────────────────────────────────────────

def get_sub_group(sub_group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM sub_groups WHERE sub_group_id = ?", [sub_group_id]
    )


def list_sub_groups(group_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM sub_groups WHERE group_id = ? ORDER BY sub_group_id", [group_id]
    )


def create_sub_group(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO sub_groups (sub_group_id, group_id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            data["sub_group_id"], data["group_id"], data["title"],
            data.get("status", "OPEN"), data.get("created_at", now),
            data.get("updated_at", now),
        ],
    )
    return get_sub_group(data["sub_group_id"])  # type: ignore[return-value]


def update_sub_group(sub_group_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("sub_group_id", "created_at")}
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE sub_groups SET {set_clause} WHERE sub_group_id = ?",
        [*updates.values(), sub_group_id],
    )
    return get_sub_group(sub_group_id)


def delete_sub_group(sub_group_id: str) -> None:
    get_store()._execute("DELETE FROM sub_groups WHERE sub_group_id = ?", [sub_group_id])


# ─────────────────────────────────────────────────────────────────────────────
# Legacy compatibility API (migrated from store.py, phase 'groups').
# Ports the store.FlowGateStore group methods with identical SQL and return shapes.
# ─────────────────────────────────────────────────────────────────────────────

_VALID_GROUP_STATUSES: set = {"OPEN", "CLOSED", "DISCARDED"}


def get_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM groups WHERE group_id = ?", [group_id]
    )


def get_all_groups(status: str = None) -> list[dict]:
    store = get_store()
    if status:
        return store._fetch_all(
            "SELECT * FROM groups WHERE status = ? ORDER BY created_at DESC", [status]
        )
    return store._fetch_all("SELECT * FROM groups ORDER BY created_at DESC")


def _trailing_number(group_id: str) -> int:
    """Return the trailing decimal run of a group_id as an int (0 if none)."""
    i = len(group_id)
    while i > 0 and group_id[i - 1].isdigit():
        i -= 1
    tail = group_id[i:]
    return int(tail) if tail else 0


def get_groups_by_projects(project_ids: list) -> list[dict]:
    """Return groups by project for get_group_tree, sorted by the trailing group_id number descending."""
    if not project_ids:
        return []
    placeholders = ",".join(["?"] * len(project_ids))
    rows = get_store()._fetch_all(
        f"SELECT * FROM groups WHERE project_id IN ({placeholders})"
        f" AND deleted_at IS NULL",
        list(project_ids),
    )
    # Natural-sort by (module, trailing group_id number desc) in Python: the SQLite
    # RTRIM(x, charset) + CAST AS INTEGER form is not portable to MariaDB (0088).
    return sorted(
        rows,
        key=lambda r: (r.get("module") or "", -_trailing_number(r.get("group_id") or "")),
    )


def insert_group(group_id: str, project: str, module: str,
                 title: str, priority: str = None) -> None:
    """Create a group with status OPEN."""
    now = datetime.now().isoformat()
    get_store()._execute(
        "INSERT INTO groups"
        " (group_id, project_id, module, title, priority, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [group_id, project, module, title, priority, "OPEN", now, now],
    )


def update_group_status(group_id: str, new_status: str) -> bool:
    now = datetime.now().isoformat()
    closed_at = now if new_status in ("CLOSED", "DISCARDED") else None
    get_store()._execute(
        "UPDATE groups SET status = ?, closed_at = ?, updated_at = ? WHERE group_id = ?",
        [new_status, closed_at, now, group_id],
    )
    return True


def close_group(group_id: str) -> bool:
    return update_group_status(group_id, "CLOSED")


def update_group_status_validated(group_id: str, new_status: str) -> tuple:
    target_status = (new_status or "").strip().upper()
    if target_status not in _VALID_GROUP_STATUSES:
        return False, f"Invalid group status: {new_status}"
    group = get_group(group_id)
    if group is None:
        return False, f"Group not found: {group_id}"
    current_status = (group["status"] or "OPEN").upper()
    if current_status == target_status:
        return False, f"Already in status {target_status}: {group_id}"
    if current_status in {"CLOSED", "DISCARDED"}:
        return False, f"Cannot modify groups in a terminal state: {group_id} ({current_status})"
    update_group_status(group_id, target_status)
    return True, f"{group_id}: {current_status} → {target_status}"


def update_group_updated_at(group_id: str) -> None:
    now = datetime.now().isoformat()
    get_store()._execute(
        "UPDATE groups SET updated_at = ? WHERE group_id = ?", [now, group_id]
    )


def issue_group_id(project: str, module: str) -> str:
    """Reserve a group_id through numbering_service.reserve_group using the D013 canonical format."""
    from ..numbering import numbering_service as _ns
    group_code = _ns.reserve_group(project, module or "none")
    return f"{project}.{module or 'none'}.{group_code}"
