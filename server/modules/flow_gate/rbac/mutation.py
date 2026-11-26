"""Transactional access changes and rollback-only impact previews."""
from __future__ import annotations

from uuid import uuid4

from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.rbac import permission_service as permissions
from modules.flow_gate.settings.workspace_service import WorkspaceError


class _Preview(Exception):
    def __init__(self, result):
        self.result = result


def audit(store, actor, operation, target):
    store._execute(
        "INSERT INTO rbac_change_audit (change_id, actor_id, operation, target_id, changed_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [uuid4().hex, actor.get("user_id"), operation, target, now_iso()],
    )


def _snapshot(store, projects):
    if not projects:
        return {}, {}
    result = {}
    admins = {project: set() for project in projects}
    for row in store._fetch_all("SELECT user_id FROM users WHERE is_active = 1"):
        uid = row["user_id"]
        grants = permissions.compute_grants(uid, store)
        for project in projects:
            role = permissions._best(grants.get(project))
            if role:
                result[(uid, project)] = role
            if role == "role_admin":
                admins[project].add(uid)
    return result, admins


def change(actor, operation, target, prepare, *, revision=None, preview=False):
    """Run authorization and writes under the same lock used by competing editors.

    prepare returns the affected project IDs and a callback applying the validated change.
    Preview uses the same callback and rolls the database transaction back.
    """
    store = get_store()
    try:
        with store.transaction():
            # The update serializes editors, including on SQLite where SELECT FOR UPDATE is absent.
            permissions.bump_global_version(store)
            current = store._fetch_one(
                "SELECT global_version FROM rbac_state WHERE state_key = 'global'"
            )["global_version"] - 1
            state = store._fetch_one(
                "SELECT is_active, is_admin FROM users WHERE user_id = ?", [actor.get("user_id")],
            )
            if not state or not state["is_active"] or bool(state["is_admin"]) != bool(actor.get("is_admin")):
                raise WorkspaceError(403, "actor_access_changed")
            projects, apply = prepare()
            if not preview and revision != current:
                raise WorkspaceError(409, "revision_conflict")
            before, old_admins = _snapshot(store, projects)
            result = apply()
            after, new_admins = _snapshot(store, projects)
            if any(old_admins[p] and not new_admins[p] for p in projects):
                raise WorkspaceError(409, "last_project_admin")
            if preview:
                impact = []
                for uid, project in sorted(before.keys() | after.keys()):
                    old, new = before.get((uid, project)), after.get((uid, project))
                    if old != new:
                        old_ids = permissions.role_permissions(old, store) if old else set()
                        new_ids = permissions.role_permissions(new, store) if new else set()
                        impact.append({"user_id": uid, "project_id": project, "before": old, "after": new,
                                       "gained": sorted(new_ids - old_ids), "lost": sorted(old_ids - new_ids)})
                raise _Preview({"revision": current, "impact": impact})
            audit(store, actor, operation, target)
            return {"revision": current + 1, "result": result}
    except _Preview as exc:
        return exc.result
