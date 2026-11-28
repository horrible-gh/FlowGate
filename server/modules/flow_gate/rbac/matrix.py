"""Who may do what: the access tables of the grant migration (design 0004-D §7.3).

Two scopes, one function each, three tables:

* project scope   (person, project, ``project.*`` permission) -> value
* workspace scope (person, workspace, ``workspace.*`` permission) -> value

Values are ``"all"`` / ``"partial"`` / ``"none"``. Only table ② can hold ``"partial"``: when
several old permission IDs became one new ID and the person held some of them.

* Table ② ``legacy_*``: old data, old rules -- what the server gave before the migration.
  A system administrator holds everything. Anyone else needs a ``user_project_roles`` row on
  the project; the row's role permissions are added to those of their ``__SYSTEM__`` role.
  Old permission IDs are read through ``LEGACY_SOURCES``. The permission registry and the role
  -> permission rows are the ones the install had before deploy SQL 214 ran
  (``permissions_legacy_0090`` / ``role_permissions_legacy_0090``, copied by 214 before it adds
  anything). A permission ID that was not registered then was held by nobody, so a route
  checking it was for administrators only; a dotted ID the install had already registered and
  granted counts like any other old ID. Workspace scope: the old member-role rules (view: any
  member, member/team management: owner or admin whatever the workspace state,
  administration: owner, project creation: the policy in an active workspace).
* Table ① ``intended_*``: old data, new rules. Project scope: each old row read in the new
  role model (aliases, a viewer row that only repeats the workspace value 'review' becomes a
  reviewer, ``__SYSTEM__`` never counts), no row means no access. Workspace scope: old
  memberships plus the grant boundary (a person holding a role on a project of a workspace is
  a member of it), read with the new state rule (a disabled workspace keeps only viewing and
  the owner's administration, design 2.2-B 4).
* Table ③ ``current_*``: stored data, new rules, through the real judgment
  (``permission_service.compute_grants`` and ``rbac.access.workspace_rule``).

A third pair of tables covers design 7.3 L2: the system and workspace permission IDs a role
carried where it was granted (``role_scope``: (person, project or ``__SYSTEM__``, ``system.*`` /
``workspace.*`` ID)). Table ② reads the role's old IDs through ``SCOPE_OUT_SOURCES``; the new
rules give no such ID through any role (design 2.2-A, 2.2-B 5), and table ③ asks the real entry
``access.can``, which refuses them to anyone but a system administrator before it reads a grant.
Design 7.3 L3 (routes that had no check, closed by card 2) has no table: ``path_losses`` gives
one fixed line per route, with the card 2 test that checks it.

Pass conditions: ① = ③ in every scope (nothing lost or changed while moving), and every
difference between ② and ③ carries a category the design lists in 7.3 (``ALLOWED_CATEGORIES``).
A difference with no category, or with a category the design has not listed yet
(``PROPOSED_CATEGORIES``: named here so the report says what it is), fails the migration.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

from modules.flow_gate.rbac import permission_service as ps

SYSTEM_PROJECT = ps.SYSTEM_PROJECT

PROJECT_PERMISSIONS = (
    "project.view", "project.document.read", "project.document.create", "project.document.update",
    "project.document.delete", "project.document.approve", "project.document.reject",
    "project.group.read", "project.group.manage", "project.run", "project.git.read",
    "project.git.write", "project.git.merge", "project.storage.write", "project.settings.read",
    "project.settings.edit", "project.document_type.manage", "project.numbering.manage",
    "project.member.read", "project.member.manage",
)
WORKSPACE_PERMISSIONS = (
    "workspace.view", "workspace.member.manage", "workspace.team.manage", "workspace.admin",
    "workspace.project.create",
)
SYSTEM_PERMISSIONS = (
    "system.settings.read", "system.settings.manage", "system.user.read", "system.user.create",
    "system.user.update", "system.user.disable", "system.user.assign_admin", "system.workspace.create",
)
# Every ID deploy SQL 214 registers. The old deploy SQL registered none of them, but an install
# may have registered some by hand (the removed role editing API); table ② therefore reads the
# registry kept by 214 and uses this set only for a database without that copy.
NEW_PERMISSION_IDS = frozenset(PROJECT_PERMISSIONS + WORKSPACE_PERMISSIONS + SYSTEM_PERMISSIONS)

# New project permission -> the IDs the code checked before (design 2.3, "current ID" column).
# The Git routes checked project.settings.read / project.settings.edit.
LEGACY_SOURCES: dict[str, tuple[str, ...]] = {
    "project.view": ("perm_project_read",),
    "project.document.read": ("perm_document_read",),
    "project.document.create": ("perm_document_create", "project.document.create"),
    "project.document.update": ("perm_document_update",),
    "project.document.delete": ("perm_document_delete",),
    "project.document.approve": ("perm_document_approve", "perm_tv_approve"),
    "project.document.reject": ("perm_document_reject",),
    "project.group.read": ("perm_group_read",),
    "project.group.manage": ("perm_group_create", "perm_group_update", "perm_group_close", "project.group.manage"),
    "project.run": ("perm_tv_run", "perm_test_run"),
    "project.git.read": ("project.settings.read",),
    "project.git.write": ("project.settings.edit",),
    "project.git.merge": ("project.settings.edit",),
    "project.storage.write": ("perm_storage_manage",),
    "project.settings.read": ("perm_project_settings_read", "perm_settings_read", "project.settings.read"),
    "project.settings.edit": ("perm_project_settings_write", "perm_settings_update", "perm_project_update",
                              "perm_project_delete", "project.settings.edit"),
    "project.document_type.manage": ("perm_document_type_manage", "project.document_type.create",
                                     "project.document_type.update", "project.document_type.delete"),
    "project.numbering.manage": ("perm_numbering_manage",),
    "project.member.read": ("perm_user_read",),
    "project.member.manage": ("perm_role_assign", "perm_role_revoke"),
}

# Old role id -> the workspace access value it was materialised from.
OLD_ROLE_TO_ACCESS = {"role_admin": "admin", "role_manager": "admin", "role_worker": "dev", "role_viewer": "review"}
_REVIEW_PERMISSIONS = frozenset({"project.document.approve", "project.document.reject", "project.git.merge"})

# The intended-change categories of design 7.3. Only these let the migration complete.
CATEGORIES = {
    "L1": "lost: permission that only a __SYSTEM__ role gave (design 7.2: everything a __SYSTEM__ row gave)",
    "L2": "lost: system / workspace permission ID a project role carried",
    "L3": "lost: a route without a check that a non-member, an anonymous caller, a revoked session or a "
          "disabled account could use (card 2 closed it; one line per route)",
    "G1": "gained: role_manager became the administrator role",
    "G2": "gained: workspace value review (stored as viewer) became the reviewer role",
    "G3": "gained: several old IDs became one, 'partial' became 'all'",
    "G4": "gained: new mapping (member.read for every role, storage.write for developer/admin)",
    "G5": "gained: a route checked a dotted ID the old deploy SQL never registered (administrators only)",
}
ALLOWED_CATEGORIES = frozenset(CATEGORIES)
# Differences that follow from a design rule but are not in the design 7.3 list. They are named
# so that the difference rows say what they are, and they fail the migration like a row with no
# category until the design lists them (then they move into CATEGORIES).
PROPOSED_CATEGORIES = {
    "G6": "gained: workspace.view of a direct grant holder made a member (grant boundary, design 7.2); "
          "not in the design 7.3 list",
    "L4": "lost: member and team management of an owner/admin in a disabled workspace "
          "(design 2.2-B 4: only viewing and re-enabling remain); not in the design 7.3 list",
}
_DISABLED_WORKSPACE_LOSSES = frozenset({"workspace.member.manage", "workspace.team.manage"})

# System / workspace permission -> the old IDs a role could carry for it (design 2.3). An empty
# tuple: the old code checked something no role held (workspace membership, is_admin).
SCOPE_OUT_SOURCES: dict[str, tuple[str, ...]] = {
    "workspace.view": (),
    "workspace.member.manage": (),
    "workspace.team.manage": (),
    "workspace.admin": (),
    "workspace.project.create": ("perm_project_create",),
    "system.settings.read": ("perm_system_settings_read",),
    "system.settings.manage": ("perm_system_settings_write", "system.settings.manage"),
    "system.user.read": ("perm_user_read", "system.user.read"),
    "system.user.create": ("perm_user_create", "system.user.create"),
    "system.user.update": ("perm_user_update", "system.user.update"),
    "system.user.disable": ("perm_user_delete", "system.user.delete"),
    "system.user.assign_admin": ("system.user.assign_role",),
    "system.workspace.create": (),
}
SCOPE_OUT_PERMISSIONS = tuple(SCOPE_OUT_SOURCES)

# Design 7.3 L3: the routes reproduced in design 0.2 (a, b, c, f, g) and 0.3, one line each:
# (item, route, who used it, old result, what answers now, card 2 test that checks it).
_GUARD_TESTS = "server/tests/modules/flow_gate/test_server_access_guard_v02test_0090.py"
L3_PATHS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("a", "GET /api/v1/documents/{doc_id}", "non_member", "200 body", "403 project.document.read",
     "TestDirectIdRequests::test_document_detail"),
    ("a", "GET /api/v1/documents?project_id=", "non_member", "200 list", "403 project.document.read",
     "TestDirectIdRequests::test_document_list_of_another_project"),
    ("a", "GET /api/v1/documents/{doc_id}/conversation/turns", "non_member", "200", "403 project.document.read",
     "TestDirectIdRequests::test_conversation"),
    ("a", "POST /api/v1/documents/{doc_id}/conversation/turn", "non_member", "201 write",
     "403 project.document.update (target decorator)",
     "TestTargetDecorator::test_own_project_with_another_projects_document_is_refused"),
    ("a", "POST /api/v1/documents", "non_member", "201 write", "403 project.document.create (target decorator)",
     "TestTargetDecorator::test_own_project_with_another_projects_document_is_refused"),
    ("a", "GET /api/v1/documents/{doc_id}/work-plan", "non_member", "422 (handler reached)",
     "403 project.document.read", "TestDirectIdRequests::test_work_plan"),
    ("a", "GET /api/v1/documents/{doc_id}/tr2", "non_member", "422 (handler reached)",
     "403 project.document.read", "TestDirectIdRequests::test_tr2"),
    ("b", "GET /api/v1/brief", "anonymous", "200", "removed", "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "GET /api/v1/queue", "anonymous", "200", "removed", "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "GET /api/v1/draft?doc_id=", "anonymous", "503 (handler reached)", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "GET /api/v1/draft/{doc_id}", "anonymous", "503 (handler reached)", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "GET /api/v1/detail?doc_id=", "anonymous", "503 (handler reached)", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "GET /api/v1/detail/{doc_id}", "anonymous", "503 (handler reached)", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "GET /api/v1/projects (legacy handler)", "anonymous", "401 (shadowed by the login route)", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "GET /api/v1/groups/{group_id}", "anonymous", "200", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "POST /api/v1/storage/folder", "anonymous / non_member", "200 write",
     "401 login, 403 project.storage.write",
     "TestLegacyRoutes::test_storage_needs_a_login TestLegacyRoutes::test_storage_checks_the_project"),
    ("b", "POST /api/v1/storage/file", "anonymous / non_member", "200 write", "401 login, 403 project.storage.write",
     "TestLegacyRoutes::test_storage_needs_a_login TestLegacyRoutes::test_storage_checks_the_project"),
    ("b", "POST /api/v1/clipboard", "anonymous", "200 (server paths)", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "POST /api/v1/outbox/create", "anonymous", "200 write", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("b", "POST /api/v1/project-settings/pick-folder", "anonymous", "200", "removed",
     "TestLegacyRoutes::test_unused_routes_are_removed"),
    ("c", "GET /api/v1/projects", "non_member", "200 every project", "only visible projects (SQL)",
     "TestLists::test_project_list"),
    ("f", "GET /api/v1/document?doc_id= (outbound user JWT)", "revoked_session", "200 body", "401",
     "TestOutboundJwt::test_revoked_session_is_refused"),
    ("f", "GET /api/v1/document?doc_id= (outbound user JWT)", "disabled_account", "200 body", "401",
     "TestOutboundJwt::test_disabled_account_is_refused"),
    ("g", "GET /api/v1/groups/{group_id}/timeline", "non_member", "200", "403 project.group.read",
     "TestDirectIdRequests::test_workflow"),
    ("g", "GET /api/v1/documents/{doc_id}/prompt", "non_member", "200", "403 project.document.read",
     "TestDirectIdRequests::test_workflow"),
    ("g", "GET /api/v1/groups?project_id=", "non_member", "200", "403 project.group.read",
     "TestDirectIdRequests::test_workflow"),
)


def category_allowed(category: Optional[str]) -> bool:
    """Whether a difference row with this category lets the migration complete (design 7.3)."""
    return category in ALLOWED_CATEGORIES


def path_losses() -> list[dict]:
    """Design 7.3 L3: one difference row per route that had no check (fixed, not computed)."""
    return [
        {"scope": "path", "item": item, "user_id": who, "target": route, "permission": now,
         "before": before, "after": now, "category": "L3",
         "evidence": [f"{_GUARD_TESTS}::{name}" for name in tests.split()]}
        for item, route, who, before, now, tests in L3_PATHS
    ]


# ── data snapshot ─────────────────────────────────────────────────────────────

def snapshot(store) -> dict:
    """Everything the tables are computed from, read once."""
    users = {r["user_id"]: r for r in store._fetch_all("SELECT user_id, is_admin, is_active FROM users")}
    projects = store._fetch_all(
        "SELECT project_id, workspace_id FROM projects WHERE project_id <> ?", [SYSTEM_PROJECT]
    )
    workspaces = {r["workspace_id"]: r for r in store._fetch_all("SELECT * FROM workspaces")}
    default_ws = next((wid for wid, w in workspaces.items() if w.get("is_default")), None)
    project_ws = {p["project_id"]: (p.get("workspace_id") or default_ws) for p in projects}
    members = {
        (r["workspace_id"], r["user_id"]): r["role"]
        for r in store._fetch_all("SELECT workspace_id, user_id, role FROM workspace_members")
    }
    exceptions = {
        (r["project_id"], r["user_id"]): r["access"]
        for r in store._fetch_all("SELECT project_id, user_id, access FROM workspace_project_access")
    }
    rows = store._fetch_all("SELECT user_id, project_id, role_id FROM user_project_roles")
    registered, role_perms, source = legacy_registry(store)
    return {
        "users": users, "projects": [p["project_id"] for p in projects], "project_ws": project_ws,
        "workspaces": workspaces, "default_ws": default_ws, "members": members,
        "exceptions": exceptions, "rows": rows, "role_perms": role_perms,
        "old_registered": registered, "registry_source": source,
    }


def legacy_registry(store) -> tuple[set, dict, str]:
    """The permission registry and the role -> permission rows as they were before deploy SQL
    214: ``(registered ids, {role: {ids}}, source)``.

    214 copies both tables before it adds anything (source ``"kept_before_214"``). A database
    without that copy falls back to today's tables minus the IDs 214 adds (``"derived"``),
    which is exact only when the install had registered none of them.
    """
    role_perms: dict[str, set] = {}
    if ps._table_exists(store, "permissions_legacy_0090") and ps._table_exists(store, "role_permissions_legacy_0090"):
        registered = {r["permission_id"] for r in store._fetch_all("SELECT permission_id FROM permissions_legacy_0090")}
        for r in store._fetch_all("SELECT role_id, permission_id FROM role_permissions_legacy_0090"):
            role_perms.setdefault(r["role_id"], set()).add(r["permission_id"])
        return registered, role_perms, "kept_before_214"
    for r in store._fetch_all("SELECT role_id, permission_id FROM role_permissions"):
        role_perms.setdefault(r["role_id"], set()).add(r["permission_id"])
    registered = {r["permission_id"] for r in store._fetch_all("SELECT permission_id FROM permissions")}
    return registered - NEW_PERMISSION_IDS, role_perms, "derived"


def workspace_value(snap: dict, user_id: str, project_id: str) -> Optional[str]:
    """The workspace access value of a person on a project ('admin'/'dev'/'review'/'none'),
    or None when they are not a member of an active workspace of that project."""
    wid = snap["project_ws"].get(project_id)
    ws = snap["workspaces"].get(wid)
    role = snap["members"].get((wid, user_id))
    if not ws or role is None or (ws.get("status") or "active") != "active":
        return None
    return snap["exceptions"].get((project_id, user_id)) or ws.get(f"default_access_{role}") or "none"


def explained_by_workspace(snap: dict, row: dict) -> bool:
    """The row only repeats the person's workspace value (it was materialised from it)."""
    value = workspace_value(snap, row["user_id"], row["project_id"])
    return value is not None and OLD_ROLE_TO_ACCESS.get(row["role_id"]) == value


def _old_perms(snap: dict, role_id: Optional[str]) -> set:
    return {p for p in snap["role_perms"].get(role_id or "", set()) if p in snap["old_registered"]}


def _people(snap: dict) -> list[str]:
    return sorted(snap["users"])


def _flag(snap: dict, user_id: str, key: str) -> bool:
    return bool((snap["users"].get(user_id) or {}).get(key))


# ── project scope ─────────────────────────────────────────────────────────────

def legacy_project_table(snap: dict) -> dict:
    """Table ②: ``{(user, project): {permission: "all"|"partial"}}`` (absent = none), plus the
    permissions each cell drew only from the ``__SYSTEM__`` role (for L1)."""
    by_user: dict[str, dict] = {}
    for row in snap["rows"]:
        by_user.setdefault(row["user_id"], {})[row["project_id"]] = row["role_id"]
    table: dict = {}
    system_only: dict = {}
    for uid in _people(snap):
        if not _flag(snap, uid, "is_active"):
            continue
        admin = _flag(snap, uid, "is_admin")
        grants = by_user.get(uid, {})
        system_perms = _old_perms(snap, grants.get(SYSTEM_PROJECT))
        for pid in snap["projects"]:
            if admin:
                table[(uid, pid)] = {p: "all" for p in PROJECT_PERMISSIONS}
                continue
            if pid not in grants:
                continue  # not a member: nothing (a __SYSTEM__ role alone never opened a project)
            own = _old_perms(snap, grants[pid])
            held = own | system_perms
            cell = {}
            for perm in PROJECT_PERMISSIONS:
                sources = set(LEGACY_SOURCES[perm])
                got = sources & held
                if not got:
                    continue
                cell[perm] = "all" if got == sources else "partial"
                if not (sources & own):
                    system_only.setdefault((uid, pid), set()).add(perm)
            if cell:
                table[(uid, pid)] = cell
    return {"table": table, "system_only": system_only}


def _role_table(snap: dict, roles: dict, mapping: dict) -> dict:
    table = {}
    for uid in _people(snap):
        if not _flag(snap, uid, "is_active"):
            continue
        for pid in snap["projects"]:
            if _flag(snap, uid, "is_admin"):
                table[(uid, pid)] = {p: "all" for p in PROJECT_PERMISSIONS}
                continue
            role = roles.get((uid, pid))
            perms = mapping.get(role, frozenset()) if role else frozenset()
            cell = {p: "all" for p in PROJECT_PERMISSIONS if p in perms}
            if cell:
                table[(uid, pid)] = cell
    return table


def intended_roles(snap: dict) -> dict:
    """Old rows read in the new role model: ``{(user, project): role}``. A role with no fixed
    counterpart is left out here and reported by ``unmapped_rows``."""
    roles = {}
    for row in snap["rows"]:
        if row["project_id"] == SYSTEM_PROJECT:
            continue
        role = ps.fixed_role(row["role_id"])
        if role is None:
            continue
        if row["role_id"] == ps.ROLE_VIEWER and explained_by_workspace(snap, row):
            role = ps.ROLE_REVIEWER
        roles[(row["user_id"], row["project_id"])] = role
    return roles


def unmapped_rows(snap: dict) -> list[dict]:
    return [
        {"user_id": r["user_id"], "project_id": r["project_id"], "role_id": r["role_id"]}
        for r in snap["rows"]
        if r["project_id"] != SYSTEM_PROJECT and ps.fixed_role(r["role_id"]) is None
    ]


def new_mapping(store) -> dict:
    return {role: ps.role_permissions(role, store) for role in ps.FIXED_ROLES}


def intended_project_table(snap: dict, mapping: dict) -> dict:
    """Table ①."""
    return _role_table(snap, intended_roles(snap), mapping)


def current_project_table(store, snap_after: dict, mapping: dict) -> dict:
    """Table ③: the stored data through the real role calculator."""
    roles = {}
    for uid in _people(snap_after):
        for pid, per in ps.compute_grants(uid, store).items():
            best = None
            for role in per.values():
                best = ps.higher_role(best, role)
            if best:
                roles[(uid, pid)] = best
    return _role_table(snap_after, roles, mapping)


# ── system / workspace IDs carried by a role (design 7.3 L2) ─────────────────

def legacy_role_scope_table(snap: dict) -> dict:
    """Table ② for L2: ``{(user, project or __SYSTEM__): {system/workspace ID: "all"|"partial"}}``.

    The IDs each grant row's role carried, read through ``SCOPE_OUT_SOURCES`` from the registry
    kept before 214. A system administrator is left out (they hold everything before and after
    through ``is_admin``), and so is a disabled account (nothing before or after)."""
    table: dict = {}
    for row in snap["rows"]:
        uid = row["user_id"]
        if not _flag(snap, uid, "is_active") or _flag(snap, uid, "is_admin"):
            continue
        own = _old_perms(snap, row["role_id"])
        cell = {}
        for perm, sources in SCOPE_OUT_SOURCES.items():
            got = set(sources) & own
            if got:
                cell[perm] = "all" if got == set(sources) else "partial"
        if cell:
            table[(uid, row["project_id"])] = cell
    return table


def intended_role_scope_table(snap: dict) -> dict:
    """Table ① for L2: under the new rules no role carries a system or workspace ID (design
    2.2-A: system for system administrators only; 2.2-B 5: project roles are not used in the
    workspace scope), so the table is empty whatever the old rows say."""
    return {}


def current_role_scope_table(store, snap_after: dict, keys: Iterable[tuple]) -> dict:
    """Table ③ for L2: the real judgment entry ``access.can`` on every (person, target) of table
    ② and every project the person now holds a role on. ``can`` decides a system or workspace ID
    before it reads any grant (and before the migration gate), so this is the stored data read
    with the rule the requests use."""
    from modules.flow_gate.rbac import access

    targets = set(keys)
    for uid in _people(snap_after):
        if not _flag(snap_after, uid, "is_active") or _flag(snap_after, uid, "is_admin"):
            continue
        for pid in ps.compute_grants(uid, store):
            targets.add((uid, pid))
    table = {}
    for uid, target in sorted(targets):
        if not _flag(snap_after, uid, "is_active") or _flag(snap_after, uid, "is_admin"):
            continue
        user = {"user_id": uid, "is_admin": 0}
        cell = {p: "all" for p in SCOPE_OUT_PERMISSIONS if access.can(user, target, p)}
        if cell:
            table[(uid, target)] = cell
    return table


# ── workspace scope ───────────────────────────────────────────────────────────

def _active(ws: dict) -> bool:
    return (ws.get("status") or "active") == "active"


def _ws_cell(ws: dict, role: Optional[str], new_rules: bool) -> dict:
    """One person's workspace permissions from their member role.

    Old rules (table ②): member management did not look at the workspace state. New rules
    (table ①, the same as ``access.workspace_rule``): a disabled workspace keeps only viewing
    and the owner's administration (design 2.2-B 4). Both limit project creation to an active
    workspace and to the roles the creation policy names.
    """
    if role is None:
        return {}
    active = _active(ws)
    perms = {"workspace.view"}
    if role in ("owner", "admin") and (active or not new_rules):
        perms |= {"workspace.member.manage", "workspace.team.manage"}
    if role == "owner":
        perms.add("workspace.admin")
    policy = ws.get("project_create_policy") or "owner_admin"
    allowed = {"owner": ("owner",), "owner_admin": ("owner", "admin"), "all": ("owner", "admin", "member")}
    if active and role in allowed.get(policy, ("owner",)):
        perms.add("workspace.project.create")
    return {p: "all" for p in perms}


def _workspace_table(snap: dict, members: dict, new_rules: bool) -> dict:
    table = {}
    for uid in _people(snap):
        if not _flag(snap, uid, "is_active"):
            continue
        for wid, ws in snap["workspaces"].items():
            if _flag(snap, uid, "is_admin"):
                cell = {p: "all" for p in WORKSPACE_PERMISSIONS}
                if not _active(ws):
                    cell.pop("workspace.project.create")
            else:
                cell = _ws_cell(ws, members.get((wid, uid)), new_rules)
            if cell:
                table[(uid, wid)] = cell
    return table


def legacy_workspace_table(snap: dict) -> dict:
    """Table ② (workspace scope): the old member-role rules on the old memberships."""
    return _workspace_table(snap, snap["members"], new_rules=False)


def boundary_members(snap: dict) -> dict:
    """Old memberships plus the grant boundary: a holder of a project row becomes a 'member'
    of that project's workspace."""
    members = dict(snap["members"])
    for row in snap["rows"]:
        if row["project_id"] == SYSTEM_PROJECT or ps.fixed_role(row["role_id"]) is None:
            continue
        wid = snap["project_ws"].get(row["project_id"])
        if wid and (wid, row["user_id"]) not in members:
            members[(wid, row["user_id"])] = "member"
    return members


def intended_workspace_table(snap: dict) -> dict:
    """Table ① (workspace scope): boundary memberships, new rules."""
    return _workspace_table(snap, boundary_members(snap), new_rules=True)


def current_workspace_table(snap_after: dict) -> dict:
    """Table ③ (workspace scope) through the real rule ``access.workspace_rule`` (the judgment
    ``can_workspace`` without its migration gate, which is still closed while this runs)."""
    from modules.flow_gate.rbac import access

    table = {}
    for uid in _people(snap_after):
        if not _flag(snap_after, uid, "is_active"):
            continue
        user = {"user_id": uid, "is_admin": _flag(snap_after, uid, "is_admin")}
        for wid, ws in snap_after["workspaces"].items():
            cell = {p: "all" for p in WORKSPACE_PERMISSIONS if access.workspace_rule(user, ws, p)}
            if cell:
                table[(uid, wid)] = cell
    return table


# ── comparison ────────────────────────────────────────────────────────────────

def _value(table: dict, key: tuple, perm: str) -> str:
    return (table.get(key) or {}).get(perm, "none")


def compare(before: dict, after: dict, permissions: Iterable[str]) -> list[dict]:
    """Every (person, target, permission) whose value differs."""
    keys = sorted(set(before) | set(after))
    lines = []
    for key in keys:
        for perm in permissions:
            old, new = _value(before, key, perm), _value(after, key, perm)
            if old != new:
                lines.append({"user_id": key[0], "target": key[1], "permission": perm,
                              "before": old, "after": new})
    return lines


def digest(table: dict) -> str:
    items = sorted(f"{k[0]}|{k[1]}|{p}|{v}" for k, cell in table.items() for p, v in cell.items())
    return hashlib.sha256(json.dumps(items).encode("utf-8")).hexdigest()


def _context(snap: dict) -> dict:
    """Lookups the categorisation needs, built once per snapshot."""
    ctx = snap.get("_ctx")
    if ctx is None:
        roles = {(r["user_id"], r["project_id"]): r["role_id"] for r in snap["rows"]}
        ctx = {"roles": roles, "intended": intended_roles(snap), "boundary": boundary_members(snap)}
        snap["_ctx"] = ctx
    return ctx


def categorize(line: dict, snap: dict, legacy: Optional[dict] = None, scope: str = "project") -> Optional[str]:
    """The category of one ② -> ③ difference, or None.

    The result may be a proposed category (G6, L4) that the design has not listed:
    ``category_allowed`` decides whether the migration may complete with it."""
    uid, target, perm = line["user_id"], line["target"], line["permission"]
    ctx = _context(snap)
    if scope == "role_scope":
        if line["after"] != "none":
            return None  # no role may gain a system or workspace ID
        # what a __SYSTEM__ row gave is L1 (design 7.2, last row); a project role's is L2
        return "L1" if target == SYSTEM_PROJECT else "L2"
    if scope == "workspace":
        old_role = snap["members"].get((target, uid))
        if line["before"] == "none" and line["after"] == "all" and perm == "workspace.view" and \
                old_role is None and (target, uid) in ctx["boundary"]:
            return "G6"
        # Anything else a boundary membership brings (project creation under the policy 'all')
        # has no category at all. G6 and L4 are proposed categories: named, not allowed.
        ws = snap["workspaces"].get(target) or {}
        if line["before"] == "all" and line["after"] == "none" and perm in _DISABLED_WORKSPACE_LOSSES and \
                old_role in ("owner", "admin") and not _active(ws):
            return "L4"
        return None
    if line["after"] != "all":
        if perm in (legacy or {}).get("system_only", {}).get((uid, target), set()):
            return "L1"
        return None
    sources = set(LEGACY_SOURCES[perm])
    registered = sources & snap["old_registered"]
    row_role = ctx["roles"].get((uid, target))
    held = _old_perms(snap, row_role) | _old_perms(snap, ctx["roles"].get((uid, SYSTEM_PROJECT)))
    if registered <= held and sources - registered:
        return "G5"
    if line["before"] == "partial" and registered - held:
        return "G3"
    new_role = ctx["intended"].get((uid, target))
    if new_role == ps.ROLE_REVIEWER and row_role == ps.ROLE_VIEWER and perm in _REVIEW_PERMISSIONS:
        return "G2"
    if row_role == "role_manager":
        return "G1"
    if perm == "project.member.read" or (
            perm == "project.storage.write" and new_role in (ps.ROLE_DEVELOPER, ps.ROLE_ADMIN)):
        return "G4"
    return None
