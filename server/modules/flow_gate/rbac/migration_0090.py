"""One-time migration of the old grants into the fixed-role model (design 0004-D §7).

Classification of every ``user_project_roles`` row (design 7.2):

=========================================================  ===================================
old row                                                     new structure
=========================================================  ===================================
member of the project's active workspace, the row repeats   row removed: the workspace value
the workspace value (it was materialised from it)           gives the same role
member, row differs, role expressible as a workspace value  exception with the row's value, row
(admin / manager -> 'admin', worker -> 'dev')               removed
member, viewer row that differs from the workspace value    kept as a direct viewer grant, and a
                                                            'none' exception where the workspace
                                                            value would be higher
not a member of the project's workspace                     kept as a direct grant (fixed role);
                                                            the person becomes a 'member' of
                                                            the workspace with 'none' on every
                                                            project the member default would
                                                            open (no other access grows)
project in a disabled workspace                             kept as a direct grant (fixed role)
member with a workspace value but no row (missed sync)      'none' exception (no access grows)
project without workspace_id                                placed in the default workspace
role with no fixed counterpart                              nothing moves: the migration fails
``__SYSTEM__`` row                                          untouched (never read again)
=========================================================  ===================================

Run rules (design 7.1):

* Runs once. The record ``rbac_migrations('rbac_0090')`` says running / completed / failed. A
  completed migration never runs again, so a grant an administrator removes later is never
  recreated. A fresh install is recorded as completed by deploy SQL 214.
* The right to run is taken in the database: the first run INSERTs the 'running' record (the
  primary key lets exactly one of two servers through), a person's retry moves 'failed' /
  'running' to 'running' with a compare-and-swap on (status, attempt). A run that did not get
  it does nothing. Then, in ONE transaction: copy the old rows, compute tables ② and ①, apply
  the plan, compute table ③, check ① = ③ in every scope (condition 1) and that every ② -> ③
  difference carries a category design 7.3 lists (condition 2; L3 adds one fixed row per route),
  raise everyone's permission version, and record 'completed' with a compare-and-swap on
  ('running', the attempt taken) -- a run that lost its claim meanwhile rolls back.
* Any failure rolls the whole transaction back and records 'failed' with the cause. Nothing
  retries automatically; a person starts it again (``run(retry=True)``, tools/rbac_migrate.py).
  An interrupted run stays 'running' and is treated like a failure.
* Until the record says 'completed', only system administrators are allowed anything, in the
  project scope (``permission_service.migration_ready``) and in the workspace scope
  (``access.can_workspace``): the stored rows are still in the old shape.
* Table ② reads the permission registry and role mapping the install had before deploy SQL
  214 (``permissions_legacy_0090`` / ``role_permissions_legacy_0090``).
* ``run(preview=True)`` computes the same report inside a transaction that is always rolled
  back, for a copy of the production database before it is upgraded.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from modules.flow_gate.db import connection as _connection
from modules.flow_gate.db import request_cache as _request_cache
from modules.flow_gate.rbac import matrix
from modules.flow_gate.rbac import permission_service as ps

logger = logging.getLogger(__name__)

MIGRATION_ID = ps.MIGRATION_ID
SYSTEM_PROJECT = ps.SYSTEM_PROJECT


class MigrationFailed(Exception):
    def __init__(self, message: str, report: Optional[dict] = None):
        super().__init__(message)
        self.report = report or {}


class _PreviewRollback(Exception):
    def __init__(self, report: dict):
        super().__init__("preview")
        self.report = report


def _store():
    return _connection.get_store()


def _now() -> str:
    return _connection.now_iso()


def get_record(store=None) -> Optional[dict]:
    """The stored record (never a request-cached copy: another server may have written it)."""
    store = store or _store()
    _request_cache.invalidate()
    return store._fetch_one("SELECT * FROM rbac_migrations WHERE migration_id = ?", [MIGRATION_ID])


# ── plan ──────────────────────────────────────────────────────────────────────

def plan(snap: dict) -> dict:
    """What the migration does with the snapshot (no writes)."""
    actions: list[dict] = []
    exceptions: dict[tuple, str] = {}
    new_members: dict[tuple, str] = {}
    counts = {"remove_row": 0, "set_exception": 0, "keep_direct": 0, "missing_row": 0,
              "new_member": 0, "system_rows": 0, "unplaced_projects": 0}
    rows_by_key = {(r["user_id"], r["project_id"]): r for r in snap["rows"]}
    for row in snap["rows"]:
        uid, pid, role_id = row["user_id"], row["project_id"], row["role_id"]
        if pid == SYSTEM_PROJECT:
            counts["system_rows"] += 1
            continue
        role = ps.fixed_role(role_id)
        if role is None:
            continue  # reported by matrix.unmapped_rows; the migration fails before writing
        wid = snap["project_ws"].get(pid)
        ws = snap["workspaces"].get(wid) or {}
        member = snap["members"].get((wid, uid))
        active = (ws.get("status") or "active") == "active"
        if member is not None and active:
            value = matrix.workspace_value(snap, uid, pid)
            if matrix.OLD_ROLE_TO_ACCESS.get(role_id) == value:
                actions.append({"action": "remove_row", "user_id": uid, "project_id": pid, "role_id": role_id})
                counts["remove_row"] += 1
                continue
            access = ps.ROLE_TO_ACCESS.get(role)
            if access is not None:
                actions.append({"action": "set_exception", "user_id": uid, "project_id": pid,
                                "role_id": role_id, "access": access})
                exceptions[(pid, uid)] = access
                counts["set_exception"] += 1
                continue
            # a viewer row: kept as a direct grant; the workspace value must not exceed it
            actions.append({"action": "keep_direct", "user_id": uid, "project_id": pid,
                            "role_id": role_id, "role": role, "reason": "viewer_differs"})
            counts["keep_direct"] += 1
            if value not in (None, "none"):
                exceptions[(pid, uid)] = "none"
            continue
        actions.append({"action": "keep_direct", "user_id": uid, "project_id": pid, "role_id": role_id,
                        "role": role, "reason": "not_member" if member is None else "workspace_disabled"})
        counts["keep_direct"] += 1
        if member is None and wid:
            new_members[(wid, uid)] = "member"
    # the grant boundary: a new member gets 'none' wherever the workspace would open a project
    for (wid, uid) in sorted(new_members):
        ws = snap["workspaces"].get(wid) or {}
        for pid, pwid in snap["project_ws"].items():
            if pwid != wid:
                continue
            value = snap["exceptions"].get((pid, uid)) or ws.get("default_access_member") or "none"
            if value != "none":
                exceptions[(pid, uid)] = "none"
        actions.append({"action": "new_member", "user_id": uid, "workspace_id": wid})
        counts["new_member"] += 1
    # missed sync: a member with a workspace value on a project and no row there
    for (wid, uid), _role in snap["members"].items():
        if (snap["users"].get(uid) or {}).get("is_admin"):
            continue  # a system administrator holds everything anyway
        for pid, pwid in snap["project_ws"].items():
            if pwid != wid or (uid, pid) in rows_by_key or (pid, uid) in exceptions:
                continue
            value = matrix.workspace_value(snap, uid, pid)
            if value not in (None, "none"):
                exceptions[(pid, uid)] = "none"
                actions.append({"action": "missing_row", "user_id": uid, "project_id": pid, "value": value})
                counts["missing_row"] += 1
    counts["unplaced_projects"] = len(snap.get("unplaced", ()))
    return {"actions": actions, "exceptions": exceptions, "new_members": new_members, "counts": counts}


def _apply(store, snap: dict, the_plan: dict) -> None:
    now = _now()
    if snap.get("default_ws"):
        store._execute(
            "UPDATE projects SET workspace_id = ?, updated_at = ? WHERE workspace_id IS NULL AND project_id <> ?",
            [snap["default_ws"], now, SYSTEM_PROJECT],
        )
    for (wid, uid), role in sorted(the_plan["new_members"].items()):
        store._execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at)"
            " VALUES (?, ?, ?, NULL, ?, ?)",
            [wid, uid, role, now, now],
        )
    for (pid, uid), access in sorted(the_plan["exceptions"].items()):
        wid = snap["project_ws"].get(pid)
        if store._fetch_one(
            "SELECT 1 AS ok FROM workspace_project_access WHERE project_id = ? AND user_id = ?", [pid, uid]
        ):
            store._execute(
                "UPDATE workspace_project_access SET access = ?, workspace_id = ?, updated_by = NULL, updated_at = ?"
                " WHERE project_id = ? AND user_id = ?",
                [access, wid, now, pid, uid],
            )
        else:
            store._execute(
                "INSERT INTO workspace_project_access (project_id, user_id, workspace_id, access, updated_by, updated_at)"
                " VALUES (?, ?, ?, ?, NULL, ?)",
                [pid, uid, wid, access, now],
            )
    for action in the_plan["actions"]:
        kind = action["action"]
        if kind in ("remove_row", "set_exception"):
            store._execute(
                "DELETE FROM user_project_roles WHERE user_id = ? AND project_id = ?",
                [action["user_id"], action["project_id"]],
            )
        elif kind == "keep_direct" and action["role"] != action["role_id"]:
            store._execute(
                "UPDATE user_project_roles SET role_id = ? WHERE user_id = ? AND project_id = ?",
                [action["role"], action["user_id"], action["project_id"]],
            )


# ── the run ───────────────────────────────────────────────────────────────────

def _execute(store) -> dict:
    """Compute, apply and check. Runs inside the caller's transaction; raises MigrationFailed."""
    snap = matrix.snapshot(store)
    snap["unplaced"] = {r["project_id"] for r in store._fetch_all(
        "SELECT project_id FROM projects WHERE workspace_id IS NULL AND project_id <> ?", [SYSTEM_PROJECT])}
    unmapped = matrix.unmapped_rows(snap)
    if unmapped:
        raise MigrationFailed("role without a fixed counterpart", {"unmapped": unmapped})
    mapping = matrix.new_mapping(store)
    legacy = matrix.legacy_project_table(snap)
    before_project = legacy["table"]
    before_workspace = matrix.legacy_workspace_table(snap)
    intended_project = matrix.intended_project_table(snap, mapping)
    intended_workspace = matrix.intended_workspace_table(snap)
    before_role_scope = matrix.legacy_role_scope_table(snap)
    intended_role_scope = matrix.intended_role_scope_table(snap)
    the_plan = plan(snap)

    store._execute("DELETE FROM user_project_roles_legacy_0090")
    store._execute(
        "INSERT INTO user_project_roles_legacy_0090 (user_id, project_id, role_id, granted_at, granted_by, copied_at)"
        " SELECT user_id, project_id, role_id, granted_at, granted_by, ? FROM user_project_roles",
        [_now()],
    )
    _apply(store, snap, the_plan)

    ps.clear_all_cache()
    after = matrix.snapshot(store)
    after_project = matrix.current_project_table(store, after, mapping)
    after_workspace = matrix.current_workspace_table(after)
    after_role_scope = matrix.current_role_scope_table(store, after, before_role_scope)

    scopes = (
        ("project", before_project, intended_project, after_project, matrix.PROJECT_PERMISSIONS),
        ("workspace", before_workspace, intended_workspace, after_workspace, matrix.WORKSPACE_PERMISSIONS),
        ("role_scope", before_role_scope, intended_role_scope, after_role_scope, matrix.SCOPE_OUT_PERMISSIONS),
    )
    condition_1 = []
    diff = []
    for scope, before, intended, after_table, permissions in scopes:
        condition_1 += [{**line, "scope": scope} for line in matrix.compare(intended, after_table, permissions)]
        for line in matrix.compare(before, after_table, permissions):
            diff.append({**line, "scope": scope, "category": matrix.categorize(line, snap, legacy, scope)})
    diff += matrix.path_losses()
    for line in diff:
        line["allowed"] = matrix.category_allowed(line["category"])
    unexplained = [line for line in diff if not line["allowed"]]
    by_category: dict[str, int] = {}
    outside: dict[str, int] = {}
    for line in diff:
        key = line["category"] or "none"
        by_category[key] = by_category.get(key, 0) + 1
        if not line["allowed"]:
            outside[key] = outside.get(key, 0) + 1
    report = {
        "plan": the_plan["counts"],
        "categories": by_category,
        "outside_allowed": outside,
        "diff_rows": len(diff),
        "unexplained_rows": len(unexplained),
        "condition_1_mismatches": len(condition_1),
        "registry_source": snap.get("registry_source"),
        "before_digest": matrix.digest(before_project),
        "after_digest": matrix.digest(after_project),
        "before_workspace_digest": matrix.digest(before_workspace),
        "after_workspace_digest": matrix.digest(after_workspace),
        "before_role_scope_digest": matrix.digest(before_role_scope),
        "after_role_scope_digest": matrix.digest(after_role_scope),
        "system_rows": [
            {"user_id": r["user_id"], "role_id": r["role_id"],
             "is_admin": bool((snap["users"].get(r["user_id"]) or {}).get("is_admin"))}
            for r in snap["rows"] if r["project_id"] == SYSTEM_PROJECT
        ],
        "actions": the_plan["actions"],
        "diff": diff,
        "condition_1": condition_1,
    }
    if condition_1:
        raise MigrationFailed("condition 1: moved data does not reproduce the old grants", report)
    if unexplained:
        raise MigrationFailed("condition 2: a difference outside the allowed categories of design 7.3", report)

    store._execute("UPDATE users SET authz_version = authz_version + 1")
    ps.bump_global_version(store)
    return report


def _summary(report: dict) -> dict:
    return {k: v for k, v in report.items() if k not in ("diff", "actions", "condition_1")}


# ── the record: who holds the run ─────────────────────────────────────────────
#
# Two servers may start on the same database. The right to run is taken in the database, never
# from an earlier read: the first run INSERTs the record (the primary key lets one INSERT
# through), a retry moves a failed / interrupted record to 'running' with a compare-and-swap on
# (status, attempt). The attempt number a caller took is its claim; the final write is again a
# compare-and-swap on ('running', that attempt), inside the migration transaction for
# 'completed', so a run that lost its claim rolls its own work back instead of writing over a
# record another run owns.

class _ClaimLost(Exception):
    pass


def _claim(store, record: Optional[dict], retry: bool) -> Optional[int]:
    """Take the right to run. Returns the attempt number this caller now holds, or None when
    another run took it (or the record no longer allows a run)."""
    now = _now()
    if record is None:
        try:
            # its own transaction: a duplicate key rolls back only this INSERT (also on PostgreSQL)
            with store.transaction():
                store._execute(
                    "INSERT INTO rbac_migrations (migration_id, status, attempt, started_at, updated_at)"
                    " VALUES (?, 'running', 1, ?, ?)",
                    [MIGRATION_ID, now, now],
                )
        except Exception as exc:
            if _connection.is_unique_violation(exc):
                return None
            raise
        return 1
    status = record.get("status")
    if not retry or status not in ("failed", "running"):
        return None
    attempt = int(record.get("attempt") or 0)
    taken = store._execute_affected(
        "UPDATE rbac_migrations SET status = 'running', attempt = ?, started_at = ?, finished_at = NULL,"
        " error = NULL, updated_at = ? WHERE migration_id = ? AND status = ? AND attempt = ?",
        [attempt + 1, now, now, MIGRATION_ID, status, attempt],
    )
    return attempt + 1 if taken == 1 else None


def _finish(store, status: str, *, attempt: int, report: Optional[dict] = None,
            error: Optional[str] = None) -> bool:
    """Write the end of the run this caller holds. False: the claim was lost (nothing written)."""
    now = _now()
    report = report or {}
    values = {
        "status": status,
        "finished_at": now,
        "before_digest": report.get("before_digest"),
        "after_digest": report.get("after_digest"),
        "summary": json.dumps(_summary(report), ensure_ascii=False, default=str) if report else None,
        "diff": json.dumps({"diff": report.get("diff", []), "actions": report.get("actions", []),
                            "condition_1": report.get("condition_1", [])},
                           ensure_ascii=False, default=str) if report else None,
        "error": error,
        "updated_at": now,
    }
    sets = ", ".join(f"{k} = ?" for k in values)
    return store._execute_affected(
        f"UPDATE rbac_migrations SET {sets} WHERE migration_id = ? AND status = 'running' AND attempt = ?",
        [*values.values(), MIGRATION_ID, attempt],
    ) == 1


def _lost(store) -> dict:
    ps.clear_all_cache()
    current = get_record(store) or {}
    logger.warning("[rbac] grant migration: another run holds the record (%s, attempt %s)",
                   current.get("status"), current.get("attempt"))
    return {"status": current.get("status"), "skipped": True, "claim_lost": True}


def run(*, retry: bool = False, preview: bool = False) -> dict:
    """Run the migration once. Returns ``{"status": ..., ...}``.

    * completed already: nothing runs (``retry`` is refused as well).
    * failed / interrupted: nothing runs unless a person passes ``retry=True``.
    * another run took the record first (two servers starting together): nothing runs here,
      ``{"skipped": True, "claim_lost": True}``.
    * ``preview``: computes the report and rolls everything back, whatever the record says.
    """
    store = _store()
    if preview:
        try:
            with store.transaction():
                try:
                    report = _execute(store)
                except MigrationFailed as exc:
                    raise _PreviewRollback({"status": "would_fail", "error": str(exc), **exc.report}) from None
                raise _PreviewRollback({"status": "would_complete", **report})
        except _PreviewRollback as done:
            ps.clear_all_cache()
            return done.report
    record = get_record(store)
    status = (record or {}).get("status")
    if status == "completed":
        return {"status": "completed", "skipped": True, "retry_refused": bool(retry)}
    if status in ("failed", "running") and not retry:
        return {"status": status, "skipped": True}
    try:
        attempt = _claim(store, record, retry)
    except Exception as exc:  # noqa: BLE001 -- nothing was taken or written; the start hook logs it
        logger.exception("[rbac] grant migration could not take the record")
        return {"status": status, "skipped": True, "error": f"claim: {type(exc).__name__}: {exc}"}
    if attempt is None:
        return _lost(store)
    try:
        with store.transaction():
            report = _execute(store)
            if not _finish(store, "completed", attempt=attempt, report=report):
                raise _ClaimLost()
    except _ClaimLost:
        return _lost(store)
    except MigrationFailed as exc:
        ps.clear_all_cache()
        _finish_failed(store, attempt, report=exc.report, error=str(exc))
        logger.error("[rbac] grant migration failed: %s", exc)
        return {"status": "failed", "error": str(exc), **_summary(exc.report)}
    except Exception as exc:  # noqa: BLE001 -- any error is recorded; the transaction is already rolled back
        ps.clear_all_cache()
        _finish_failed(store, attempt, error=f"{type(exc).__name__}: {exc}")
        logger.exception("[rbac] grant migration failed")
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    ps.clear_all_cache()
    logger.info("[rbac] grant migration completed: %s", _summary(report))
    return {"status": "completed", **_summary(report)}


def _finish_failed(store, attempt: int, *, report: Optional[dict] = None, error: str) -> None:
    """Record the failure of the run this caller holds; a lost claim or a write error is logged
    (the record then stays 'running', which refuses non-administrators like 'failed')."""
    try:
        if not _finish(store, "failed", attempt=attempt, report=report, error=error):
            logger.warning("[rbac] grant migration failure not recorded: another run holds the record")
    except Exception:  # noqa: BLE001
        logger.exception("[rbac] grant migration failure could not be recorded")
