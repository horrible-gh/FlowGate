"""Project role calculator and permission lookup (design 0004-D §2.2, §2.4, §3.3).

The final permission of a person on a project:

* a disabled account holds nothing;
* a system administrator (``users.is_admin``) holds every registered permission;
* anyone else holds the ``project.*`` permissions of exactly one role on that project: the
  highest of their direct role (``user_project_roles``), their teams' roles
  (``team_project_roles`` through ``team_members``) and the workspace value (the member's
  per-project exception, else the default of their workspace role; nothing in a disabled
  workspace). Order: role_admin > role_developer > role_reviewer > role_viewer.
  No role means not a member: nothing on that project. A ``__SYSTEM__`` row is never added to
  any project, and ``system.*`` belongs to system administrators only.

The four roles are fixed (deploy SQL 214 holds their permissions). ``role_manager`` and
``role_worker`` are read as ``role_admin`` and ``role_developer``. Any other role id is not a
project role and gives nothing.

Until the one-time grant migration (rbac/migration_0090.py) has recorded ``completed``, the
stored rows are still in the old shape, so nobody but a system administrator is allowed
anything (design 7.1). A fresh install is recorded as completed by the deploy SQL.

Cache: one entry per person holding their role per project, keyed by the person's
``users.authz_version``, the install's ``rbac_state.global_version`` and the store. Both
versions are read from the database on every judgment, so a change committed by another
process applies from the next judgment. There is no TTL. Every change that alters what a
person may do raises their version in the same transaction (``bump_user_versions``); a change
of the role -> permission mapping raises the global version (``bump_global_version``).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Iterable, Optional

from modules.flow_gate.db import connection as _connection

SYSTEM_PROJECT = "__SYSTEM__"
MIGRATION_ID = "rbac_0090"

ROLE_ADMIN = "role_admin"
ROLE_DEVELOPER = "role_developer"
ROLE_REVIEWER = "role_reviewer"
ROLE_VIEWER = "role_viewer"
FIXED_ROLES = (ROLE_ADMIN, ROLE_DEVELOPER, ROLE_REVIEWER, ROLE_VIEWER)
ROLE_RANK = {ROLE_ADMIN: 4, ROLE_DEVELOPER: 3, ROLE_REVIEWER: 2, ROLE_VIEWER: 1}
# Old role ids that are read as a fixed role.
ROLE_ALIASES = {"role_manager": ROLE_ADMIN, "role_worker": ROLE_DEVELOPER}
# Workspace access value <-> project role. The viewer role has no access value.
ACCESS_TO_ROLE = {"admin": ROLE_ADMIN, "dev": ROLE_DEVELOPER, "review": ROLE_REVIEWER}
ROLE_TO_ACCESS = {ROLE_ADMIN: "admin", ROLE_DEVELOPER: "dev", ROLE_REVIEWER: "review"}

SOURCE_DIRECT = "direct"
SOURCE_TEAM = "team"
SOURCE_WORKSPACE = "workspace"

# No TTL: an entry is valid exactly as long as the versions it was computed under.
CACHE_TTL = None
CACHE_SIZE = 4096

_lock = threading.Lock()
# user_id -> ((store_key, user_version, global_version), {project_id: {source: role}})
_cache: "OrderedDict[str, tuple[tuple, dict]]" = OrderedDict()
# (store_key, global_version) -> {role_id: frozenset(project permissions)}
_role_permission_cache: dict[tuple, dict] = {}
# (store_key, global_version) -> frozenset(every registered permission id)
_registered_cache: dict[tuple, frozenset] = {}
# store_key -> {"teams": bool, "workspaces": bool, "versions": bool, "migrations": bool}
_capabilities: dict[int, dict] = {}
# store keys whose migration record says completed (terminal, so it is remembered)
_migration_completed: set[int] = set()


def get_store():
    """The store, looked up at call time (a test may swap it after import)."""
    return _connection.get_store()


def _store_key(store) -> int:
    return id(getattr(store, "_db", None) or store)


def fixed_role(role_id: Optional[str]) -> Optional[str]:
    """The fixed role a stored role id reads as, or None for anything else."""
    if role_id in ROLE_RANK:
        return role_id
    return ROLE_ALIASES.get(role_id or "")


def higher_role(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if a is None:
        return b
    if b is None:
        return a
    return a if ROLE_RANK[a] >= ROLE_RANK[b] else b


# ── schema capabilities (a hand-built test schema may predate deploy SQL 214) ──

def _table_exists(store, name: str) -> bool:
    probe = getattr(store, "table_exists", None)
    if callable(probe):
        return bool(probe(name))
    # a minimal store double (tests) without the helper: they are all SQLite
    try:
        return store._fetch_one(
            "SELECT 1 AS ok FROM sqlite_master WHERE type = 'table' AND name = ?", [name]
        ) is not None
    except Exception:  # noqa: BLE001 -- unknown store: treat the optional table as absent
        return False


def _caps(store) -> dict:
    key = _store_key(store)
    caps = _capabilities.get(key)
    if caps is None:
        caps = {
            "teams": _table_exists(store, "team_project_roles"),
            "workspaces": _table_exists(store, "workspace_members"),
            "versions": _table_exists(store, "rbac_state"),
            "migrations": _table_exists(store, "rbac_migrations"),
        }
        _capabilities[key] = caps
    return caps


# ── migration gate (design 7.1) ───────────────────────────────────────────────

def migration_status(store=None) -> Optional[str]:
    """The stored status of the grant migration ('running' / 'completed' / 'failed'), or None."""
    store = store or get_store()
    if not _caps(store)["migrations"]:
        return "completed"
    row = store._fetch_one(
        "SELECT status FROM rbac_migrations WHERE migration_id = ?", [MIGRATION_ID]
    )
    return (row or {}).get("status")


def migration_ready(store=None) -> bool:
    """True once the grant migration has completed. Before that only administrators pass."""
    store = store or get_store()
    key = _store_key(store)
    if key in _migration_completed:
        return True
    ready = migration_status(store) == "completed"
    if ready:
        _migration_completed.add(key)
    return ready


def reset_migration_gate() -> None:
    _migration_completed.clear()


# ── versions ──────────────────────────────────────────────────────────────────

def _user_state(store, user_id: str) -> Optional[dict]:
    """is_admin, is_active and both versions of one person, in one query."""
    if _caps(store)["versions"]:
        row = store._fetch_one(
            "SELECT u.is_admin, u.is_active, u.authz_version, s.global_version FROM users u"
            " LEFT JOIN rbac_state s ON s.state_key = 'global' WHERE u.user_id = ?",
            [user_id],
        )
    else:
        row = store._fetch_one("SELECT is_admin, is_active FROM users WHERE user_id = ?", [user_id])
    if row is None:
        return None
    return {
        "is_admin": bool(row.get("is_admin")),
        "is_active": bool(row.get("is_active")),
        "user_version": int(row.get("authz_version") or 0),
        "global_version": int(row.get("global_version") or 0),
    }


def _chunks(values: list, size: int = 500):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def bump_user_versions(user_ids: Iterable[Optional[str]], store=None) -> None:
    """Raise the permission version of these people.

    Runs on the caller's open transaction when there is one, so the change and the version
    commit (or roll back) together. The local cache entries are dropped as well; other
    processes see the new version on their next judgment.
    """
    ids = sorted({uid for uid in user_ids if uid})
    if not ids:
        return
    store = store or get_store()
    with _lock:
        for uid in ids:
            _cache.pop(uid, None)
    if not _caps(store)["versions"]:
        return
    for chunk in _chunks(ids):
        marks = ", ".join("?" for _ in chunk)
        store._execute(
            f"UPDATE users SET authz_version = authz_version + 1 WHERE user_id IN ({marks})", chunk
        )


def bump_global_version(store=None) -> None:
    """Raise the install-wide version: every cached role and mapping is recomputed."""
    store = store or get_store()
    with _lock:
        _cache.clear()
        _role_permission_cache.clear()
        _registered_cache.clear()
    if not _caps(store)["versions"]:
        return
    store._execute(
        "UPDATE rbac_state SET global_version = global_version + 1, updated_at = ?"
        " WHERE state_key = 'global'",
        [_connection.now_iso()],
    )


# ── role calculator (design 2.2-C) ───────────────────────────────────────────

def _offer(grants: dict, project_id: Optional[str], role_id: Optional[str], source: str) -> None:
    role = fixed_role(role_id)
    if not role or not project_id or project_id == SYSTEM_PROJECT:
        return
    per_project = grants.setdefault(project_id, {})
    per_project[source] = higher_role(per_project.get(source), role)


def _workspace_grants(store, user_id: str, grants: dict) -> None:
    memberships = store._fetch_all(
        "SELECT m.workspace_id, m.role, w.status, w.is_default, w.default_access_owner,"
        " w.default_access_admin, w.default_access_member FROM workspace_members m"
        " JOIN workspaces w ON w.workspace_id = m.workspace_id WHERE m.user_id = ?",
        [user_id],
    )
    active = {m["workspace_id"]: m for m in memberships if (m.get("status") or "active") == "active"}
    if not active:
        return
    default_member = next((wid for wid, m in active.items() if m.get("is_default")), None)
    ids = sorted(active)
    marks = ", ".join("?" for _ in ids)
    sql = (
        "SELECT project_id, workspace_id FROM projects WHERE project_id <> ?"
        f" AND (workspace_id IN ({marks})"
        + (" OR workspace_id IS NULL" if default_member else "")
        + ")"
    )
    projects = store._fetch_all(sql, [SYSTEM_PROJECT, *ids])
    exceptions = {
        row["project_id"]: row["access"]
        for row in store._fetch_all(
            "SELECT project_id, access FROM workspace_project_access WHERE user_id = ?", [user_id]
        )
    }
    for project in projects:
        workspace_id = project.get("workspace_id") or default_member
        member = active.get(workspace_id)
        if not member:
            continue
        value = exceptions.get(project["project_id"]) or member.get(f"default_access_{member['role']}") or "none"
        _offer(grants, project["project_id"], ACCESS_TO_ROLE.get(value), SOURCE_WORKSPACE)


def compute_grants(user_id: str, store=None) -> dict[str, dict[str, str]]:
    """``{project_id: {source: role}}`` from the three sources, without the cache."""
    store = store or get_store()
    grants: dict[str, dict[str, str]] = {}
    for row in store._fetch_all(
        "SELECT project_id, role_id FROM user_project_roles WHERE user_id = ? AND project_id <> ?",
        [user_id, SYSTEM_PROJECT],
    ):
        _offer(grants, row["project_id"], row["role_id"], SOURCE_DIRECT)
    caps = _caps(store)
    if caps["teams"]:
        for row in store._fetch_all(
            "SELECT tpr.project_id, tpr.role_id FROM team_project_roles tpr"
            " JOIN team_members tm ON tm.team_id = tpr.team_id WHERE tm.user_id = ?",
            [user_id],
        ):
            _offer(grants, row["project_id"], row["role_id"], SOURCE_TEAM)
    if caps["workspaces"]:
        _workspace_grants(store, user_id, grants)
    return grants


def _grants(store, user_id: str, state: dict) -> dict[str, dict[str, str]]:
    stamp = (_store_key(store), state["user_version"], state["global_version"])
    with _lock:
        entry = _cache.get(user_id)
        if entry is not None and entry[0] == stamp:
            _cache.move_to_end(user_id)
            return entry[1]
    grants = compute_grants(user_id, store)
    with _lock:
        _cache[user_id] = (stamp, grants)
        _cache.move_to_end(user_id)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return grants


def _best(per_project: Optional[dict]) -> Optional[str]:
    best = None
    for role in (per_project or {}).values():
        best = higher_role(best, role)
    return best


def _role_permissions(store, global_version: int) -> dict[str, frozenset]:
    key = (_store_key(store), global_version)
    cached = _role_permission_cache.get(key)
    if cached is not None:
        return cached
    marks = ", ".join("?" for _ in FIXED_ROLES)
    mapping: dict[str, set] = {role: set() for role in FIXED_ROLES}
    for row in store._fetch_all(
        f"SELECT role_id, permission_id FROM role_permissions WHERE role_id IN ({marks})",
        list(FIXED_ROLES),
    ):
        if str(row["permission_id"]).startswith("project."):
            mapping[row["role_id"]].add(row["permission_id"])
    frozen = {role: frozenset(perms) for role, perms in mapping.items()}
    with _lock:
        _role_permission_cache[key] = frozen
    return frozen


def role_permissions(role_id: str, store=None) -> frozenset:
    """The project permissions of one fixed role (aliases resolved)."""
    store = store or get_store()
    role = fixed_role(role_id)
    if not role:
        return frozenset()
    state_version = 0
    if _caps(store)["versions"]:
        row = store._fetch_one("SELECT global_version FROM rbac_state WHERE state_key = 'global'")
        state_version = int((row or {}).get("global_version") or 0)
    return _role_permissions(store, state_version).get(role, frozenset())


def _registered(store, global_version: int) -> frozenset:
    key = (_store_key(store), global_version)
    cached = _registered_cache.get(key)
    if cached is not None:
        return cached
    ids = frozenset(row["permission_id"] for row in store._fetch_all("SELECT permission_id FROM permissions"))
    with _lock:
        _registered_cache[key] = ids
    return ids


# ── public lookups ────────────────────────────────────────────────────────────

def project_roles(user_id: Optional[str]) -> dict[str, str]:
    """``{project_id: role}`` of a non-administrator (empty when disabled or gated)."""
    if not user_id:
        return {}
    store = get_store()
    state = _user_state(store, user_id)
    if not state or not state["is_active"] or not migration_ready(store):
        return {}
    return {pid: role for pid, per in _grants(store, user_id, state).items() if (role := _best(per))}


def effective_role(user_id: Optional[str], project_id: Optional[str]) -> Optional[str]:
    """The one role a person holds on a project (None: not a member). Administrators are not
    given a role here: ``is_admin`` is judged before any role."""
    if not project_id or project_id == SYSTEM_PROJECT:
        return None
    return project_roles(user_id).get(project_id)


def role_detail(user_id: Optional[str], project_id: Optional[str]) -> dict:
    """The role and the sources that gave it, e.g. for capabilities (card 4).

    ``{"role": "role_developer", "sources": ["team"], "grants": {"direct": "role_reviewer",
    "team": "role_developer"}}``. ``role`` is None for a non-member.
    """
    empty = {"role": None, "sources": [], "grants": {}}
    if not user_id or not project_id or project_id == SYSTEM_PROJECT:
        return empty
    store = get_store()
    state = _user_state(store, user_id)
    if not state or not state["is_active"] or not migration_ready(store):
        return empty
    per = _grants(store, user_id, state).get(project_id) or {}
    role = _best(per)
    if role is None:
        return empty
    return {
        "role": role,
        "sources": sorted(source for source, value in per.items() if value == role),
        "grants": dict(per),
    }


def get_user_permissions(user_id: Optional[str], project_id: Optional[str]) -> set[str]:
    """Every permission the person holds on the project (see the module docstring)."""
    if not user_id:
        return set()
    store = get_store()
    state = _user_state(store, user_id)
    if not state or not state["is_active"]:
        return set()
    if state["is_admin"]:
        return set(_registered(store, state["global_version"]))
    if not project_id or project_id == SYSTEM_PROJECT or not migration_ready(store):
        return set()
    role = _best(_grants(store, user_id, state).get(project_id))
    if role is None:
        return set()
    return set(_role_permissions(store, state["global_version"]).get(role, frozenset()))


def has_permission(user_id: Optional[str], project_id: Optional[str], permission_id: str) -> bool:
    """Whether the person holds one permission on the project."""
    return permission_id in get_user_permissions(user_id, project_id)


def permitted_projects(user_id: Optional[str], permission_id: Optional[str] = None) -> set[str]:
    """Projects on which a non-administrator holds ``permission_id`` (any role when None)."""
    roles = project_roles(user_id)
    if not roles:
        return set()
    if permission_id is None:
        return set(roles)
    store = get_store()
    state = _user_state(store, user_id) or {"global_version": 0}
    mapping = _role_permissions(store, state["global_version"])
    return {pid for pid, role in roles.items() if permission_id in mapping.get(role, frozenset())}


def invalidate_cache(user_id: str, project_id: Optional[str] = None) -> None:
    """Drop this process's entry for one person (the version decides validity anyway)."""
    with _lock:
        _cache.pop(user_id, None)


def invalidate_all() -> None:
    """A role -> permission mapping changed: raise the global version for every process.

    Without a database behind the store (TESTING without a store) only this process forgets."""
    store = get_store()
    if getattr(store, "_db", True) is None:
        clear_all_cache()
        return
    bump_global_version(store)


def clear_all_cache() -> None:
    """Forget everything this process remembered (tests, restart)."""
    with _lock:
        _cache.clear()
        _role_permission_cache.clear()
        _registered_cache.clear()
        _capabilities.clear()
    reset_migration_gate()
