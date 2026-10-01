"""AI Group persistence (flowgate.v02.0017 T0002 §4).

A group is a team: members (AI connections), user-defined roles and a many-to-many
member/role mapping, plus an optional Supervisor member. The concrete runtime slots
(work_executor / reviewer / reworker) live in a separate layer, ai_group_runtime_bindings.
Provider ids are logical ids without FKs so a removed connection stays visible as broken.
The legacy ai_groups.*_provider_id columns (migration 124) are kept but never written.
Migration 130 adds team ON/OFF (`enabled`), the default team (`default_key`, UNIQUE) and
per-role evaluation criteria (`criteria`, JSON). Migration 135 (flowgate.v02.0017 CH0006 (4))
takes groups out of the project: one list for the whole system, one default team
(`default_key` = DEFAULT_KEY).
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

RUNTIME_SLOTS = ("work_executor", "reviewer", "reworker")
# default_key of the default team; NULL on every other team, so the UNIQUE index keeps one.
DEFAULT_KEY = "default"

_COLUMNS = ("ai_group_id, name, name_key, name_key_hash, description, "
            "supervisor_member_id, enabled, default_key, created_at, updated_at")
_ROLE_COLUMNS = ("role_id, ai_group_id, name, name_key, name_key_hash, description, criteria, "
                 "position, created_at, updated_at")


# ── Groups ───────────────────────────────────────────────────────────────────

def list_all() -> list[dict]:
    return get_store()._fetch_all(
        f"SELECT {_COLUMNS} FROM ai_groups ORDER BY updated_at DESC, ai_group_id", [])


def get(ai_group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        f"SELECT {_COLUMNS} FROM ai_groups WHERE ai_group_id = ?", [ai_group_id])


def get_by_name_hash(name_key_hash: str) -> Optional[dict]:
    return get_store()._fetch_one(
        f"SELECT {_COLUMNS} FROM ai_groups WHERE name_key_hash = ?", [name_key_hash])


def create(row: dict) -> None:
    now = now_iso()
    get_store()._execute(
        "INSERT INTO ai_groups (ai_group_id, name, name_key, name_key_hash, "
        "description, supervisor_member_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
        [row['ai_group_id'], row['name'], row['name_key'],
         row['name_key_hash'], row['description'], now, now])


def update(ai_group_id: str, row: dict) -> None:
    get_store()._execute(
        "UPDATE ai_groups SET name = ?, name_key = ?, name_key_hash = ?, description = ?, "
        "updated_at = ? WHERE ai_group_id = ?",
        [row['name'], row['name_key'], row['name_key_hash'], row['description'],
         now_iso(), ai_group_id])


def set_supervisor(ai_group_id: str, member_id: Optional[str]) -> None:
    get_store()._execute(
        "UPDATE ai_groups SET supervisor_member_id = ?, updated_at = ? WHERE ai_group_id = ?",
        [member_id, now_iso(), ai_group_id])


def set_state(ai_group_id: str, *, enabled: Optional[bool] = None,
              is_default: Optional[bool] = None) -> None:
    """Team ON/OFF and default-team flag. Turning a team OFF also drops its default flag;
    making a team the default moves the flag off the previous default in one transaction."""
    store = get_store()
    now = now_iso()
    with store.transaction():
        if enabled is not None:
            store._execute(
                "UPDATE ai_groups SET enabled = ?, updated_at = ? WHERE ai_group_id = ?",
                [1 if enabled else 0, now, ai_group_id])
            if not enabled:
                is_default = False
        if is_default is True:
            store._execute("UPDATE ai_groups SET default_key = NULL, updated_at = ? "
                           "WHERE default_key = ? AND ai_group_id <> ?",
                           [now, DEFAULT_KEY, ai_group_id])
            store._execute("UPDATE ai_groups SET default_key = ?, updated_at = ? WHERE ai_group_id = ?",
                           [DEFAULT_KEY, now, ai_group_id])
        elif is_default is False:
            store._execute("UPDATE ai_groups SET default_key = NULL, updated_at = ? WHERE ai_group_id = ?",
                           [now, ai_group_id])


def touch(ai_group_id: str) -> None:
    get_store()._execute("UPDATE ai_groups SET updated_at = ? WHERE ai_group_id = ?",
                         [now_iso(), ai_group_id])


def delete(ai_group_id: str) -> None:
    # Children are removed explicitly as well so a connection without FK enforcement
    # (SQLite with foreign_keys off) never leaves orphans behind.
    store = get_store()
    with store.transaction():
        for table in ("ai_group_member_roles", "ai_group_runtime_bindings",
                      "ai_group_roles", "ai_group_members"):
            store._execute(f"DELETE FROM {table} WHERE ai_group_id = ?", [ai_group_id])
        store._execute("DELETE FROM ai_groups WHERE ai_group_id = ?", [ai_group_id])


# ── Members ──────────────────────────────────────────────────────────────────

def list_members(ai_group_ids: list[str]) -> list[dict]:
    if not ai_group_ids:
        return []
    marks = ", ".join("?" for _ in ai_group_ids)
    return get_store()._fetch_all(
        "SELECT member_id, ai_group_id, provider_id, created_at FROM ai_group_members "
        f"WHERE ai_group_id IN ({marks}) ORDER BY created_at, member_id", list(ai_group_ids))


def get_member(ai_group_id: str, member_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT member_id, ai_group_id, provider_id, created_at FROM ai_group_members "
        "WHERE ai_group_id = ? AND member_id = ?", [ai_group_id, member_id])


def get_member_by_provider(ai_group_id: str, provider_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT member_id, ai_group_id, provider_id, created_at FROM ai_group_members "
        "WHERE ai_group_id = ? AND provider_id = ?", [ai_group_id, provider_id])


def add_member(ai_group_id: str, member_id: str, provider_id: str) -> None:
    get_store()._execute(
        "INSERT INTO ai_group_members (member_id, ai_group_id, provider_id, created_at) "
        "VALUES (?, ?, ?, ?)", [member_id, ai_group_id, provider_id, now_iso()])
    touch(ai_group_id)


def remove_member(ai_group_id: str, member_id: str, provider_id: str) -> None:
    """Drop the member, its role mappings, the runtime slots it held and, if it was the
    Supervisor, the Supervisor designation -- in one transaction."""
    store = get_store()
    with store.transaction():
        store._execute("DELETE FROM ai_group_member_roles WHERE member_id = ?", [member_id])
        store._execute("DELETE FROM ai_group_runtime_bindings WHERE ai_group_id = ? AND provider_id = ?",
                       [ai_group_id, provider_id])
        store._execute("UPDATE ai_groups SET supervisor_member_id = NULL "
                       "WHERE ai_group_id = ? AND supervisor_member_id = ?", [ai_group_id, member_id])
        store._execute("DELETE FROM ai_group_members WHERE ai_group_id = ? AND member_id = ?",
                       [ai_group_id, member_id])
        store._execute("UPDATE ai_groups SET updated_at = ? WHERE ai_group_id = ?",
                       [now_iso(), ai_group_id])


# ── Roles ────────────────────────────────────────────────────────────────────

def list_roles(ai_group_ids: list[str]) -> list[dict]:
    if not ai_group_ids:
        return []
    marks = ", ".join("?" for _ in ai_group_ids)
    return get_store()._fetch_all(
        f"SELECT {_ROLE_COLUMNS} FROM ai_group_roles WHERE ai_group_id IN ({marks}) "
        "ORDER BY position, created_at, role_id", list(ai_group_ids))


def get_role(ai_group_id: str, role_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        f"SELECT {_ROLE_COLUMNS} FROM ai_group_roles WHERE ai_group_id = ? AND role_id = ?",
        [ai_group_id, role_id])


def get_role_by_name_hash(ai_group_id: str, name_key_hash: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT role_id, name_key FROM ai_group_roles WHERE ai_group_id = ? AND name_key_hash = ?",
        [ai_group_id, name_key_hash])


def next_role_position(ai_group_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COALESCE(MAX(position), 0) AS max_position FROM ai_group_roles WHERE ai_group_id = ?",
        [ai_group_id])
    return int(row["max_position"] if row else 0) + 1


def create_role(row: dict) -> None:
    now = now_iso()
    get_store()._execute(
        "INSERT INTO ai_group_roles (role_id, ai_group_id, name, name_key, name_key_hash, "
        "description, criteria, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [row['role_id'], row['ai_group_id'], row['name'], row['name_key'], row['name_key_hash'],
         row['description'], row['criteria'], row['position'], now, now])
    touch(row['ai_group_id'])


def update_role(ai_group_id: str, role_id: str, row: dict) -> None:
    get_store()._execute(
        "UPDATE ai_group_roles SET name = ?, name_key = ?, name_key_hash = ?, description = ?, "
        "criteria = ?, updated_at = ? WHERE ai_group_id = ? AND role_id = ?",
        [row['name'], row['name_key'], row['name_key_hash'], row['description'], row['criteria'],
         now_iso(), ai_group_id, role_id])
    touch(ai_group_id)


def delete_role(ai_group_id: str, role_id: str) -> None:
    store = get_store()
    with store.transaction():
        store._execute("DELETE FROM ai_group_member_roles WHERE role_id = ?", [role_id])
        store._execute("DELETE FROM ai_group_roles WHERE ai_group_id = ? AND role_id = ?",
                       [ai_group_id, role_id])
        store._execute("UPDATE ai_groups SET updated_at = ? WHERE ai_group_id = ?",
                       [now_iso(), ai_group_id])


# ── Member <-> role mapping (many-to-many) ─────────────────────────────────────

def list_mappings(ai_group_ids: list[str]) -> list[dict]:
    if not ai_group_ids:
        return []
    marks = ", ".join("?" for _ in ai_group_ids)
    return get_store()._fetch_all(
        "SELECT member_id, role_id, ai_group_id FROM ai_group_member_roles "
        f"WHERE ai_group_id IN ({marks}) ORDER BY created_at, member_id, role_id",
        list(ai_group_ids))


def set_member_roles(ai_group_id: str, member_id: str, role_ids: list[str]) -> None:
    store = get_store()
    now = now_iso()
    with store.transaction():
        store._execute("DELETE FROM ai_group_member_roles WHERE member_id = ?", [member_id])
        for role_id in role_ids:
            store._execute(
                "INSERT INTO ai_group_member_roles (member_id, role_id, ai_group_id, created_at) "
                "VALUES (?, ?, ?, ?)", [member_id, role_id, ai_group_id, now])
        store._execute("UPDATE ai_groups SET updated_at = ? WHERE ai_group_id = ?", [now, ai_group_id])


# ── Runtime slot binding (separate from team roles) ─────────────────────────────

def list_bindings(ai_group_ids: list[str]) -> list[dict]:
    if not ai_group_ids:
        return []
    marks = ", ".join("?" for _ in ai_group_ids)
    return get_store()._fetch_all(
        "SELECT ai_group_id, slot, provider_id, updated_at FROM ai_group_runtime_bindings "
        f"WHERE ai_group_id IN ({marks})", list(ai_group_ids))


def replace_bindings(ai_group_id: str, bindings: dict[str, Optional[str]]) -> None:
    store = get_store()
    now = now_iso()
    with store.transaction():
        store._execute("DELETE FROM ai_group_runtime_bindings WHERE ai_group_id = ?", [ai_group_id])
        for slot in RUNTIME_SLOTS:
            provider_id = bindings.get(slot)
            if provider_id:
                store._execute(
                    "INSERT INTO ai_group_runtime_bindings (ai_group_id, slot, provider_id, updated_at) "
                    "VALUES (?, ?, ?, ?)", [ai_group_id, slot, provider_id, now])
        store._execute("UPDATE ai_groups SET updated_at = ? WHERE ai_group_id = ?", [now, ai_group_id])
