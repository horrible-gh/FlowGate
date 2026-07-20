"""Permission CRUD."""
from __future__ import annotations
from typing import Optional, Any
from .connection import get_store, now_iso


def _invalidate_permission_cache() -> None:
    """Drop rbac.permission_service's cache after a role_permissions change.

    0276 T0009: that cache holds resolved permission sets for 30 minutes and was
    only invalidated on role *assignment*. Editing a role's permissions changes
    what every holder of that role may do, so it has to clear too. Imported
    lazily — the db package must not import rbac at module load time.
    """
    try:
        from modules.flow_gate.rbac import permission_service
    except Exception:
        return
    permission_service.invalidate_all()


def get_by_id(permission_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM permissions WHERE permission_id = ?", [permission_id]
    )


def list_permissions() -> list[dict]:
    return get_store()._fetch_all("SELECT * FROM permissions ORDER BY permission_id")


def list_by_role(role_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT p.* FROM permissions p "
        "JOIN role_permissions rp ON p.permission_id = rp.permission_id "
        "WHERE rp.role_id = ? ORDER BY p.permission_id",
        [role_id],
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO permissions (permission_id, permission_name, description, created_at) "
        "VALUES (?, ?, ?, ?)",
        [
            data["permission_id"], data["permission_name"],
            data.get("description"), data.get("created_at", now),
        ],
    )
    return get_by_id(data["permission_id"])  # type: ignore[return-value]


def delete(permission_id: str) -> None:
    get_store()._execute(
        "DELETE FROM permissions WHERE permission_id = ?", [permission_id]
    )
    _invalidate_permission_cache()


def assign_to_role(role_id: str, permission_id: str) -> None:
    get_store()._execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)"
        " ON CONFLICT DO NOTHING",
        [role_id, permission_id],
    )
    _invalidate_permission_cache()


def revoke_from_role(role_id: str, permission_id: str) -> None:
    get_store()._execute(
        "DELETE FROM role_permissions WHERE role_id = ? AND permission_id = ?",
        [role_id, permission_id],
    )
    _invalidate_permission_cache()
