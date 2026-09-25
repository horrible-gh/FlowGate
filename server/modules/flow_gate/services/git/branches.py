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


def _validate_merge_branch(
    project_id: str, base_root: Path, branch: str, role: str,
) -> None:
    """Validate a local ordinary branch at the branch-merge execution boundary."""
    from modules.flow_gate.services import git_service as _gs
    if not _gs._ref_exists(base_root, f"refs/heads/{branch}"):
        remote_only = _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}")
        code = f"branch_merge_{role}_remote_only" if remote_only else f"branch_merge_{role}_not_found"
        raise GitServiceError(
            409 if remote_only else 404, code,
            f"{role} branch is not an allowed local branch",
        )
    owner = internal_slot_owner(project_id, branch)
    if owner is not None:
        raise GitServiceError(
            409, f"branch_merge_{role}_internal_slot",
            f"registered group worktree branch cannot be the merge {role}",
            {"connected_group_id": owner.get("group_id")},
        )


def merge_branches(project_id: str, source_branch: str, target_branch: str) -> dict:
    """Merge and publish one ordinary local branch into another.

    Conflicts are terminal: collect paths, abort, clean the managed target
    worktree, and return branch_merge_conflict without a finalize session.
    """
    from modules.flow_gate.services import git_service as _gs
    from . import merge_target

    cfg, base_root, base_branch = _branch_context(project_id)
    if source_branch == target_branch:
        raise GitServiceError(409, "branch_merge_same_branch", "source and target must differ")
    _validate_merge_branch(project_id, base_root, source_branch, "source")
    _validate_merge_branch(project_id, base_root, target_branch, "target")

    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(409, "git_busy", "another Git operation is in progress")
    owner = f"branch-merge:{uuid.uuid4()}"
    wdir = merge_target.workspace_dir(project_id, target_branch)
    ctx = merge_target.MergeTargetContext(
        project_id=project_id, base_branch=base_branch, target_branch=target_branch,
        is_project_base=False, root=wdir / merge_target.WORKSPACE_TREE,
        workspace_dir=wdir, workspace_key=merge_target.workspace_key(target_branch),
        merge_id=owner, owner=owner, lock_holder=holder,
    )
    prepared = False
    try:
        if source_branch == target_branch:
            raise GitServiceError(409, "branch_merge_same_branch", "source and target must differ")
        _validate_merge_branch(project_id, base_root, source_branch, "source")
        _validate_merge_branch(project_id, base_root, target_branch, "target")
        merge_target.raise_if_workspace_unavailable(project_id, target_branch)
        # Git refuses a second checkout of the project base. A detached managed
        # worktree preserves the shared checkout while HEAD is pushed explicitly.
        merge_target.prepare_workspace(ctx, detached=target_branch == base_branch)
        prepared = True
        username = cfg.get("username")
        secret = _gs._load_secret_for(cfg) or ""
        fetch = _gs._run_git(
            ["fetch", "origin"], cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
            username=username, secret=secret,
        )
        if fetch.returncode != 0:
            raise GitServiceError(500, "git_error", "Git fetch failed", diagnostic=_one_line(fetch.stderr))
        if _gs._ref_exists(ctx.root, f"refs/remotes/origin/{target_branch}"):
            update = _gs._run_git(["merge", "--ff-only", f"origin/{target_branch}"], cwd=ctx.root)
            if update.returncode != 0:
                raise GitServiceError(
                    409, "branch_merge_target_diverged",
                    "target branch cannot fast-forward to its remote counterpart",
                    diagnostic=_one_line(update.stderr),
                )
        source_before = _gs._rev_parse(base_root, f"refs/heads/{source_branch}")
        target_before = _gs._rev_parse(ctx.root, "HEAD")
        merged = _gs._run_git(
            [*_gs._GIT_IDENT, "-c", "merge.conflictStyle=zdiff3", "merge", "--no-ff",
             "-m", f"Merge branch '{source_branch}' into {target_branch}", source_branch],
            cwd=ctx.root,
        )
        if merged.returncode != 0:
            files_proc = _gs._run_git(["diff", "--name-only", "--diff-filter=U"], cwd=ctx.root)
            files = [line for line in (files_proc.stdout or "").splitlines() if line]
            aborted = _gs._run_git(["merge", "--abort"], cwd=ctx.root)
            if aborted.returncode != 0:
                raise GitServiceError(
                    500, "branch_merge_abort_failed",
                    "branch merge conflicted and Git could not abort it",
                    {"conflict_files": files}, diagnostic=_one_line(aborted.stderr),
                )
            if not merge_target.release_workspace(ctx):
                prepared = False
                raise GitServiceError(
                    500, "branch_merge_cleanup_failed",
                    "branch merge conflicted but its managed workspace could not be cleaned",
                    {"conflict_files": files},
                )
            prepared = False
            raise GitServiceError(
                409, "branch_merge_conflict", "branch merge has conflicts",
                {"source_branch": source_branch, "target_branch": target_branch,
                 "conflict_files": files},
            )
        pushed = _gs._run_git(
            ["push", "origin", f"HEAD:{target_branch}"], cwd=ctx.root,
            timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
        )
        if pushed.returncode != 0:
            _gs._run_git(["reset", "--hard", target_before], cwd=ctx.root)
            raise GitServiceError(
                500, "branch_merge_push_failed", "Git push was rejected",
                diagnostic=_one_line(pushed.stderr),
            )
        target_after = _gs._rev_parse(ctx.root, "HEAD")
        if not merge_target.release_workspace(ctx):
            prepared = False
            raise GitServiceError(
                500, "branch_merge_cleanup_failed",
                "branch merge was pushed but its managed workspace could not be cleaned",
                {"target_branch": target_branch, "target_head": target_after},
            )
        prepared = False
        return {
            "ok": True, "source_branch": source_branch, "target_branch": target_branch,
            "source_head": source_before, "target_before": target_before,
            "target_head": target_after, "pushed": True, "workspace_cleaned": True,
        }
    finally:
        if prepared:
            merge_target.release_workspace(ctx)
        _gs.db_git.release_lock(project_id, holder)


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
    cfg, base_root, base_branch = _branch_context(project_id)
    if not _gs._ref_exists(base_root, f"refs/heads/{name}"):
        return "branch_not_found", {}
    if name == base_branch:
        return "branch_is_base", {}
    owner = internal_slot_owner(project_id, name)
    if owner is not None:
        return "branch_is_internal_slot", {"connected_group_id": owner.get("group_id")}
    # T0016 §6.1: the branch currently pinned as this project's merge-target
    # suggestion cannot be deleted out from under it — it must be retargeted
    # or cleared (mt.set_project_default_target) first, same as base/internal
    # slot above.
    if (cfg or {}).get("default_merge_target") == name:
        return "branch_is_default_merge_target", {}
    # flowgate.default.0613 T0013: a group's durable work base is re-read for the
    # whole group lifecycle (provisioning, retry, reprovision, reopen); deleting it
    # would strand that group, so it is protected until the group is terminal.
    from .group_work_base import groups_pinning_work_base
    pinning = groups_pinning_work_base(project_id, name)
    if pinning:
        return "branch_is_group_work_base", {"connected_group_ids": pinning}

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
    "branch_is_default_merge_target": (
        409, "branch set as the current integration target cannot be deleted",
    ),
    "branch_is_group_work_base": (
        409, "branch is the work base of an active group and cannot be deleted",
    ),
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


def resolve_local_branch_ref(project_id: str, branch: str) -> tuple[Path, str]:
    """(base_root, commit) for an ordinary local branch (T0004 §3). Pure read: no
    lock, no worktree, no DB write. Rejects anything that is not a real
    ``refs/heads/<branch>`` -- a remote-only ref is a distinct 409, everything else
    (including a typo) is 404."""
    from modules.flow_gate.services import git_service as _gs
    _, base_root, _base_branch = _branch_context(project_id)
    if not _gs._ref_exists(base_root, f"refs/heads/{branch}"):
        if _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}"):
            raise GitServiceError(409, "branch_read_remote_only", "remote-only branch is read-only")
        raise GitServiceError(404, "branch_not_found", "local branch was not found")
    commit = _gs._rev_parse(base_root, f"refs/heads/{branch}")
    if not commit:
        raise GitServiceError(500, "git_error", "Git could not resolve the branch tip")
    return base_root, commit


def read_local_branch_tree(project_id: str, branch: str) -> dict:
    """Checkout-free recursive tree of an ordinary local branch's HEAD commit
    (T0004 §3.1). Same hidden-path rule as the group explorer's committed view
    (dotfiles / *.db). Unlike a group branch this has no live worktree behind it, so
    there is no untracked channel to merge in -- the tree is exactly what the branch
    has committed, and base checkout HEAD/index/worktree are never touched."""
    from modules.flow_gate.services import git_service as _gs
    base_root, commit = resolve_local_branch_ref(project_id, branch)
    proc = _gs._run_git(["ls-tree", "-r", "-z", commit], cwd=base_root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", "Git tree lookup failed", diagnostic=_one_line(proc.stderr))
    visible_files: list[str] = []
    for record in (proc.stdout or "").split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        if not path:
            continue
        parts = meta.split()
        # entry: "<mode> <type> <sha>"; only blobs are files.
        if len(parts) < 2 or parts[1] != "blob":
            continue
        segments = path.split("/")
        if any(seg.startswith(".") for seg in segments) or segments[-1].lower().endswith(".db"):
            continue
        visible_files.append(path)
    nodes = _gs._build_tree_nodes(visible_files)
    return {"ok": True, "data": {
        "branch": branch, "commit": commit, "nodes": nodes, "read_only": True,
    }}


def read_local_branch_blob(
    project_id: str, branch: str, path: str, ref: Optional[str] = None
) -> dict:
    """Checkout-free single-file read from an ordinary local branch (T0004 §3.2).
    No working-tree fallback: unlike a group branch a local branch has no live
    worktree, so an untracked (never-committed) file is never exposed here -- it is
    committed-tree read-only, exactly as T0004 requires."""
    from modules.flow_gate.services import git_service as _gs
    _gs._validate_blob_path(path)
    base_root, head_commit = resolve_local_branch_ref(project_id, branch)
    commit = head_commit
    if ref:
        if not _gs._REF_PIN_RE.match(ref):
            raise GitServiceError(400, "invalid_ref", "ref must be a full 40-hex commit sha")
        tproc = _gs._run_git(["cat-file", "-t", ref], cwd=base_root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
        if tproc.returncode != 0 or (tproc.stdout or "").strip() != "commit":
            raise GitServiceError(404, "not_found", f"commit '{ref}' not found")
        commit = ref
    entry = _gs._ls_tree_entry(base_root, commit, path)
    if entry is None or entry[0] != "blob":
        raise GitServiceError(404, "not_found", f"path '{path}' not found in commit {commit}")
    sha = entry[1]
    size = _gs._cat_file_size(base_root, sha)
    head = _gs._cat_file_blob_head(base_root, sha, _gs.BLOB_MAX_RETURN_BYTES)
    if b"\x00" in head[:_gs.BLOB_BINARY_SNIFF_BYTES]:
        return {"ok": True, "data": {
            "branch": branch, "commit": commit, "path": path,
            "size": size, "binary": True, "truncated": False,
            "encoding": None, "content": None,
        }}
    truncated = size > _gs.BLOB_MAX_RETURN_BYTES
    body = head[:_gs.BLOB_MAX_RETURN_BYTES] if truncated else head[:size]
    content = body.decode("utf-8", errors="replace")
    return {"ok": True, "data": {
        "branch": branch, "commit": commit, "path": path,
        "size": size, "binary": False, "truncated": truncated,
        "encoding": "utf-8", "content": content,
    }}


def list_branches(project_id: str) -> dict:
    """Return local branches plus read-only remote-only tracking refs."""
    from modules.flow_gate.services import git_service as _gs
    cfg, base_root, base_branch = _branch_context(project_id)
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
    # T0016 §2.2 — the last non-base target a finalize actually merged into,
    # surfaced only while it still names a real local branch (never a dangling
    # suggestion for a branch that was since deleted).
    suggested_target = (cfg or {}).get("default_merge_target")
    default_merge_target = suggested_target if suggested_target in locals_by_name else None
    return {
        "ok": True,
        "base_branch": base_branch,
        "default_merge_target": default_merge_target,
        "branches": branches,
    }
