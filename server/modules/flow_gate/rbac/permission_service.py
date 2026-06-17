"""Permission service — permission lookup and caching based on user_id + project_id.

Cache TTL: 30 minutes (synchronized with JWT access token expiry, D011 r1 §4 PM decision No.2).
System permissions are queried with project_id='__SYSTEM__'.

Permission check query:
  JOIN user_project_roles to role_permissions.
  Sum project and system roles using project_id IN (target_project_id, '__SYSTEM__').
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from modules.flow_gate.db.connection import get_store

CACHE_TTL = 30 * 60  # 30 minutes (seconds)

_cache: dict[tuple[str, str], tuple[set[str], float]] = {}
_cache_lock = threading.Lock()


def get_user_permissions(user_id: str, project_id: str) -> set[str]:
    """Return the set of permission_id for the given user_id and project_id.

        If the same (user_id, project_id) is called within the cache TTL, the cached value is returned.
        A query with project_id='__SYSTEM__' includes only system role permissions.
        A normal project_id query combines the project's roles with the __SYSTEM__ roles.
    """
    key = (user_id, project_id)
    now = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key)
        if cached and (now - cached[1]) < CACHE_TTL:
            return cached[0]

    perms = _fetch_permissions(user_id, project_id)

    with _cache_lock:
        _cache[key] = (perms, now)

    return perms


def _fetch_permissions(user_id: str, project_id: str) -> set[str]:
    """Fetch the set of permissions from the DB (bypassing cache)."""
    store = get_store()
    rows = store._fetch_all(
        """
        SELECT DISTINCT rp.permission_id
        FROM user_project_roles upr
        JOIN role_permissions rp ON upr.role_id = rp.role_id
        WHERE upr.user_id = ?
          AND upr.project_id IN (?, '__SYSTEM__')
        """,
        [user_id, project_id],
    )
    return {row["permission_id"] for row in rows}


def has_permission(user_id: str, project_id: str, permission_id: str) -> bool:
    """Check whether a single permission is held."""
    return permission_id in get_user_permissions(user_id, project_id)


def invalidate_cache(user_id: str, project_id: Optional[str] = None) -> None:
    """Invalidate cache on permission changes such as role updates.

        If project_id is None, delete all cache entries for the given user_id.
    """
    with _cache_lock:
        if project_id is not None:
            _cache.pop((user_id, project_id), None)
        else:
            keys_to_delete = [k for k in _cache if k[0] == user_id]
            for k in keys_to_delete:
                del _cache[k]


def clear_all_cache() -> None:
    """Clear the entire cache (used for testing or system restart)."""
    with _cache_lock:
        _cache.clear()
