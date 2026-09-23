"""Project branch catalog and local-only branch lifecycle operations."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from .credentials import GitServiceError


def _one_line(value: str) -> str:
    return " ".join((value or "").splitlines()).strip()


def _branch_context(project_id: str) -> tuple[dict, Path, str]:
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs._require_enabled_config(project_id)
    base_root = _gs._base_root_of(project_id)
    base_branch = _gs.base_branch_for(project_id)
    if base_root is None or not base_branch:
        raise GitServiceError(
            409, "invalid_state", f"git checkout is not available for project '{project_id}'"
        )
    return cfg, base_root, base_branch


def internal_slot_owner(project_id: str, branch_name: str) -> Optional[dict]:
    """Return the registered group-worktree ledger row owning ``branch_name``."""
    from modules.flow_gate.services import git_service as _gs
    for row in _gs.db_git.list_states_of_project(project_id):
        if row.get("branch") == branch_name:
            return row
    return None


def validate_new_branch_name(
    project_id: str, base_root: Path, base_branch: str, name: str
) -> None:
    """Validate a requested branch name without rewriting it."""
    from modules.flow_gate.services import git_service as _gs
    if name == base_branch or name == "HEAD":
        raise GitServiceError(409, "branch_reserved_name", "branch name is reserved")
    if _gs._ref_exists(base_root, f"refs/heads/{name}"):
        raise GitServiceError(409, "branch_name_exists", "local branch already exists")
    proc = _gs._run_git(["check-ref-format", "--branch", name], cwd=base_root)
    if proc.returncode != 0:
        diagnostic = _one_line(proc.stderr)
        raise GitServiceError(
            422,
            "branch_ref_format_invalid",
            "branch name is not a valid Git branch name",
            {"git_stderr": diagnostic},
            diagnostic=diagnostic,
        )


def _validate_create_source(project_id: str, base_root: Path, source_branch: str) -> None:
    from modules.flow_gate.services import git_service as _gs
    if not _gs._ref_exists(base_root, f"refs/heads/{source_branch}"):
        if _gs._ref_exists(base_root, f"refs/remotes/origin/{source_branch}"):
            raise GitServiceError(
                409, "branch_source_remote_only", "remote-only branch is read-only"
            )
        raise GitServiceError(404, "branch_source_not_found", "source branch was not found")
    owner = internal_slot_owner(project_id, source_branch)
    if owner is not None:
        raise GitServiceError(
            409,
            "branch_source_internal_slot",
            "registered group worktree branch cannot be a branch source",
            {"connected_group_id": owner.get("group_id")},
        )


def create_branch(project_id: str, name: str, source_branch: str) -> dict:
    """Create one local branch. This operation intentionally never publishes it."""
    from modules.flow_gate.services import git_service as _gs
    _, base_root, base_branch = _branch_context(project_id)
    validate_new_branch_name(project_id, base_root, base_branch, name)
    _validate_create_source(project_id, base_root, source_branch)

    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(409, "git_busy", "another Git operation is in progress")
    try:
        # Fail closed after lock acquisition: both the destination ref and the
        # source's registered-slot ownership can have changed while we waited.
        validate_new_branch_name(project_id, base_root, base_branch, name)
        _validate_create_source(project_id, base_root, source_branch)
        proc = _gs._run_git(["branch", name, source_branch], cwd=base_root)
        if proc.returncode != 0:
            diagnostic = _one_line(proc.stderr)
            raise GitServiceError(
                500, "branch_create_failed", "Git could not create the branch",
                diagnostic=diagnostic,
            )
        return {
            "ok": True,
            "branch": name,
            "source_branch": source_branch,
            "published": False,
        }
    finally:
        _gs.db_git.release_lock(project_id, holder)


def _delete_guard(project_id: str, name: str) -> tuple[Optional[str], dict]:
    from modules.flow_gate.services import git_service as _gs
    _, base_root, base_branch = _branch_context(project_id)
    if not _gs._ref_exists(base_root, f"refs/heads/{name}"):
        return "branch_not_found", {}
    if name == base_branch:
        return "branch_is_base", {}
    owner = internal_slot_owner(project_id, name)
    if owner is not None:
        return "branch_is_internal_slot", {"connected_group_id": owner.get("group_id")}

    # 0594 T0012: every open finalize attempt pins its target (base or not), and a
    # non-base attempt no longer shows up as the base checkout's blocking session —
    # scan all of them, plus the one session that holds the base checkout.
    from .merge_target import open_merge_attempts
    sessions = list(open_merge_attempts(project_id))
    base_holder = _gs.open_merge_session_of_project(project_id)
    if base_holder is not None and all(
        s.get("merge_id") != base_holder.get("merge_id") for s in sessions
    ):
        sessions.append(base_holder)
    for session in sessions:
        group_id = session.get("group_id")
        state = _gs.db_git.get_state(group_id) if group_id else None
        target = _gs.db_git.session_context(session).get("target_branch")
        if (state or {}).get("branch") == name or target == name:
            return "branch_in_use", {
                "blocking_group_id": group_id,
                "merge_id": session.get("merge_id"),
            }
    return None, {}


def check_branch_delete(project_id: str, name: str) -> Optional[str]:
    """Return the first non-destructive deletion guard code, or ``None``."""
    reason, _ = _delete_guard(project_id, name)
    return reason


_DELETE_ERRORS = {
    "branch_not_found": (404, "local branch was not found"),
    "branch_is_base": (409, "base branch cannot be deleted"),
    "branch_is_internal_slot": (409, "registered group worktree branch cannot be deleted"),
    "branch_in_use": (409, "branch is in use by an open merge"),
}


def _raise_delete_guard(reason: str, details: dict) -> None:
    status, message = _DELETE_ERRORS[reason]
    raise GitServiceError(status, reason, message, details or None)


def _unmerged_commits(base_root: Path, base_branch: str, name: str) -> tuple[list[dict], bool]:
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["log", "--format=%H%x1f%cI%x1f%s", f"{base_branch}..{name}"], cwd=base_root
    )
    if proc.returncode != 0:
        return [], False
    lines = [line for line in (proc.stdout or "").splitlines() if line]
    commits = []
    for line in lines[:20]:
        parts = line.split("\x1f", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "committed_at": parts[1], "subject": parts[2]})
    return commits, len(lines) > 20


def delete_branch(project_id: str, name: str) -> dict:
    """Delete one local, merged branch; the corresponding remote ref is untouched."""
    from modules.flow_gate.services import git_service as _gs
    _, base_root, base_branch = _branch_context(project_id)
    reason, details = _delete_guard(project_id, name)
    if reason:
        _raise_delete_guard(reason, details)

    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(409, "git_busy", "another Git operation is in progress")
    try:
        reason, details = _delete_guard(project_id, name)
        if reason:
            _raise_delete_guard(reason, details)
        had_remote = _gs._ref_exists(base_root, f"refs/remotes/origin/{name}")
        proc = _gs._run_git(["branch", "-d", name], cwd=base_root)
        if proc.returncode != 0:
            diagnostic = _one_line(proc.stderr)
            if "not fully merged" in (proc.stderr or "").lower():
                commits, truncated = _unmerged_commits(base_root, base_branch, name)
                raise GitServiceError(
                    409,
                    "branch_unmerged_commits",
                    "branch has commits that are not merged into the base branch",
                    {"commits": commits, "truncated": truncated},
                    diagnostic=diagnostic,
                )
            raise GitServiceError(
                500, "branch_delete_failed", "Git could not delete the branch",
                diagnostic=diagnostic,
            )
        return {
            "ok": True,
            "branch": name,
            "deleted": True,
            "remote_counterpart_remains": had_remote,
        }
    finally:
        _gs.db_git.release_lock(project_id, holder)


def _ahead_behind(base_root: Path, base_branch: str, name: str) -> tuple[Optional[int], Optional[int]]:
    from modules.flow_gate.services import git_service as _gs
    if name == base_branch:
        return 0, 0
    proc = _gs._run_git(
        ["rev-list", "--left-right", "--count", f"{base_branch}...{name}"],
        cwd=base_root,
    )
    if proc.returncode != 0:
        return None, None
    try:
        behind, ahead = (int(value) for value in proc.stdout.split())
        return ahead, behind
    except (TypeError, ValueError):
        return None, None


def list_branches(project_id: str) -> dict:
    """Return local branches plus read-only remote-only tracking refs."""
    from modules.flow_gate.services import git_service as _gs
    _, base_root, base_branch = _branch_context(project_id)
    local_proc = _gs._run_git(
        ["for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/heads"],
        cwd=base_root,
    )
    remote_proc = _gs._run_git(
        ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"],
        cwd=base_root,
    )
    if local_proc.returncode != 0 or remote_proc.returncode != 0:
        diagnostic = _one_line(local_proc.stderr or remote_proc.stderr)
        raise GitServiceError(
            500, "branch_catalog_failed", "Git could not list branches", diagnostic=diagnostic
        )

    locals_by_name: dict[str, str] = {}
    for line in (local_proc.stdout or "").splitlines():
        name, separator, oid = line.partition("\t")
        if name and separator:
            locals_by_name[name] = oid
    remote_names = {
        ref[len("origin/"):]
        for ref in (remote_proc.stdout or "").splitlines()
        if ref.startswith("origin/") and ref != "origin/HEAD"
    }

    branches = []
    for name in sorted(locals_by_name):
        owner = internal_slot_owner(project_id, name)
        kind = "base" if name == base_branch else ("internal_slot" if owner else "local")
        reason = check_branch_delete(project_id, name)
        if reason is None:
            ahead_hint = _gs._ahead_of_base(base_root, base_branch, name)
            if ahead_hint is not None and ahead_hint > 0:
                reason = "branch_unmerged_commits"
        ahead, behind = _ahead_behind(base_root, base_branch, name)
        row = {
            "name": name,
            "kind": kind,
            "oid": locals_by_name[name],
            "ahead_of_base": ahead,
            "behind_base": behind,
            "can_delete": reason is None,
            "delete_blocked_reason": reason,
            "can_be_create_source": kind != "internal_slot",
            "create_source_blocked_reason": (
                "internal_slot" if kind == "internal_slot" else None
            ),
            "has_remote_counterpart": name in remote_names,
        }
        if owner is not None:
            row["connected_group_id"] = owner.get("group_id")
        branches.append(row)

    for name in sorted(remote_names - set(locals_by_name)):
        branches.append({
            "name": name,
            "kind": "remote_only",
            "oid": None,
            "ahead_of_base": None,
            "behind_base": None,
            "can_delete": False,
            "delete_blocked_reason": "remote_only",
            "can_be_create_source": False,
            "create_source_blocked_reason": "remote_only",
            "has_remote_counterpart": True,
        })
    return {"ok": True, "base_branch": base_branch, "branches": branches}
