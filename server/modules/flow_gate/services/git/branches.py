"""Project branch catalog and local-only branch lifecycle operations."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Literal, Optional

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


@dataclass(frozen=True)
class MergeEndpointIdentity:
    kind: str  # "branch" | "worktree"
    branch: str
    group_id: Optional[str] = None


@dataclass(frozen=True)
class ResolvedSource:
    kind: str
    branch: str
    group_id: Optional[str]
    head_sha: str
    root: Optional[Path] = None


@dataclass(frozen=True)
class ResolvedTarget:
    kind: str
    branch: str
    group_id: Optional[str]
    root: Path
    managed_workspace: bool


def check_git_operation_in_progress(root: Path) -> Optional[str]:
    """Check if a merge, rebase, cherry-pick, revert, or sequencer operation is in progress.

    Uses `git rev-parse --git-path` to correctly resolve worktree-specific Git paths.
    """
    from modules.flow_gate.services import git_service as _gs

    ops = [
        ("MERGE_HEAD", "merge"),
        ("REBASE_HEAD", "rebase"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("sequencer", "sequencer"),
    ]
    for git_rel, op_name in ops:
        proc = _gs._run_git(["rev-parse", "--git-path", git_rel], cwd=root)
        if proc.returncode == 0:
            out = (proc.stdout or "").strip()
            if out:
                p = Path(out)
                if not p.is_absolute():
                    p = (root / p).resolve()
                if p.exists():
                    return op_name
    return None


def _validate_worktree_git_metadata(
    base_root: Path, wt_path: Path, expected_branch: str
) -> None:
    from modules.flow_gate.services import git_service as _gs

    if not (wt_path / ".git").exists():
        raise GitServiceError(
            409, "branch_merge_worktree_identity_mismatch", "directory is not a Git worktree"
        )
    proc = _gs._run_git(["rev-parse", "--is-inside-work-tree"], cwd=wt_path)
    if proc.returncode != 0 or (proc.stdout or "").strip() != "true":
        raise GitServiceError(
            409, "branch_merge_worktree_identity_mismatch", "directory is not inside a Git worktree"
        )
    proc_base_common = _gs._run_git(["rev-parse", "--git-common-dir"], cwd=base_root)
    proc_wt_common = _gs._run_git(["rev-parse", "--git-common-dir"], cwd=wt_path)
    if proc_base_common.returncode != 0 or proc_wt_common.returncode != 0:
        raise GitServiceError(
            409, "branch_merge_worktree_identity_mismatch", "failed to resolve common Git directory"
        )
    base_common_raw = (proc_base_common.stdout or "").strip()
    wt_common_raw = (proc_wt_common.stdout or "").strip()
    base_common = Path(base_common_raw if Path(base_common_raw).is_absolute() else (base_root / base_common_raw)).resolve()
    wt_common = Path(wt_common_raw if Path(wt_common_raw).is_absolute() else (wt_path / wt_common_raw)).resolve()
    if os.path.normcase(str(base_common)) != os.path.normcase(str(wt_common)):
        raise GitServiceError(
            409,
            "branch_merge_worktree_identity_mismatch",
            "worktree does not share common Git directory with project base repository",
        )
    proc_list = _gs._run_git(["worktree", "list", "--porcelain"], cwd=base_root)
    if proc_list.returncode != 0:
        raise GitServiceError(
            409, "branch_merge_worktree_identity_mismatch", "failed to list repository worktrees"
        )
    reg_wts = {
        os.path.normcase(str(Path(line[len("worktree "):].strip()).resolve()))
        for line in proc_list.stdout.splitlines()
        if line.startswith("worktree ")
    }
    if os.path.normcase(str(wt_path.resolve())) not in reg_wts:
        raise GitServiceError(
            409,
            "branch_merge_worktree_identity_mismatch",
            "directory is not a registered worktree of the project base repository",
        )
    proc_branch = _gs._run_git(["symbolic-ref", "--short", "HEAD"], cwd=wt_path)
    if proc_branch.returncode != 0 or (proc_branch.stdout or "").strip() != expected_branch:
        raise GitServiceError(
            409,
            "branch_merge_worktree_identity_mismatch",
            f"worktree HEAD ref does not match registered branch '{expected_branch}'",
        )


def _resolve_worktree_endpoint(
    project_id: str,
    base_root: Path,
    endpoint: MergeEndpointIdentity,
    role: Literal["source", "target"],
) -> tuple[str, Path, str]:
    from modules.flow_gate.services import git_service as _gs
    from modules.flow_gate.db import groups as db_groups

    group_id = endpoint.group_id
    if not group_id:
        raise GitServiceError(
            422,
            "branch_merge_worktree_group_required",
            f"group_id is required for worktree merge {role}",
        )
    group_rec = db_groups.get_by_id(group_id)
    if group_rec is None or group_rec.get("deleted_at") is not None:
        raise GitServiceError(
            404,
            "branch_merge_worktree_group_not_found",
            f"group '{group_id}' not found or deleted",
        )
    if group_rec.get("project_id") != project_id:
        raise GitServiceError(
            409,
            "branch_merge_worktree_project_mismatch",
            f"group '{group_id}' does not belong to project '{project_id}'",
        )
    state = _gs.db_git.get_state(group_id)
    if state is None:
        raise GitServiceError(
            404,
            "branch_merge_worktree_group_not_found",
            f"group '{group_id}' git state not found",
        )
    if state.get("project_id") != project_id:
        raise GitServiceError(
            409,
            "branch_merge_worktree_project_mismatch",
            f"group '{group_id}' does not belong to project '{project_id}'",
        )
    if not state.get("worktree_registered"):
        raise GitServiceError(
            409,
            "branch_merge_worktree_not_registered",
            f"worktree is not registered for group '{group_id}'",
        )
    ledger_branch = (state.get("branch") or "").strip()
    if not ledger_branch or ledger_branch != endpoint.branch:
        raise GitServiceError(
            409,
            "branch_merge_worktree_branch_mismatch",
            f"requested branch '{endpoint.branch}' does not match registered group branch '{ledger_branch}'",
        )
    project_name = _gs._project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    wt_path = _gs.src_root(project_name, ledger_branch)
    if wt_path is None or not wt_path.exists() or not wt_path.is_dir():
        raise GitServiceError(
            404,
            "branch_merge_worktree_not_found",
            f"worktree directory not found for group '{group_id}'",
        )
    _validate_worktree_git_metadata(base_root, wt_path, ledger_branch)
    op = check_git_operation_in_progress(wt_path)
    if op:
        raise GitServiceError(
            409,
            "branch_merge_worktree_git_operation_in_progress",
            f"Git operation '{op}' in progress on {role} worktree",
            {"operation": op, "role": role, "group_id": group_id},
        )
    return group_id, wt_path, ledger_branch


def resolve_source(
    project_id: str,
    base_root: Path,
    base_branch: str,
    endpoint: MergeEndpointIdentity,
) -> ResolvedSource:
    """Resolve and validate merge source endpoint."""
    from modules.flow_gate.services import git_service as _gs

    kind = endpoint.kind or "branch"
    if kind == "branch":
        _validate_merge_branch(project_id, base_root, endpoint.branch, "source")
        head_sha = _gs._rev_parse(base_root, f"refs/heads/{endpoint.branch}")
        if not head_sha:
            raise GitServiceError(404, "branch_merge_source_not_found", "source branch was not found")
        return ResolvedSource(
            kind="branch",
            branch=endpoint.branch,
            group_id=None,
            head_sha=head_sha,
            root=base_root,
        )

    if kind == "worktree":
        group_id, wt_path, ledger_branch = _resolve_worktree_endpoint(
            project_id, base_root, endpoint, "source"
        )
        head_sha = _gs._rev_parse(wt_path, "HEAD")
        if not head_sha:
            raise GitServiceError(500, "branch_merge_failed", "could not resolve HEAD of source worktree")
        return ResolvedSource(
            kind="worktree",
            branch=endpoint.branch,
            group_id=group_id,
            head_sha=head_sha,
            root=wt_path.resolve(),
        )

    raise GitServiceError(422, "branch_merge_invalid_kind", f"unsupported source kind '{kind}'")


def resolve_target(
    project_id: str,
    base_root: Path,
    base_branch: str,
    endpoint: MergeEndpointIdentity,
    source: ResolvedSource,
) -> ResolvedTarget:
    """Resolve and validate merge target endpoint with preflight checks."""
    from . import merge_target
    from .refs import _dirty, _dirty_files

    kind = endpoint.kind or "branch"
    if source.kind == "worktree" and kind == "worktree":
        if source.group_id == endpoint.group_id:
            raise GitServiceError(
                409, "branch_merge_same_worktree", "source and target worktrees must differ"
            )
    if source.branch == endpoint.branch:
        if source.kind == "worktree" and kind == "worktree":
            raise GitServiceError(
                409, "branch_merge_same_worktree", "source and target worktrees must differ"
            )
        raise GitServiceError(409, "branch_merge_same_branch", "source and target must differ")

    if kind == "branch":
        _validate_merge_branch(project_id, base_root, endpoint.branch, "target")
        wdir = merge_target.workspace_dir(project_id, endpoint.branch)
        root = wdir / merge_target.WORKSPACE_TREE
        return ResolvedTarget(
            kind="branch",
            branch=endpoint.branch,
            group_id=None,
            root=root,
            managed_workspace=True,
        )

    if kind == "worktree":
        group_id, wt_path, ledger_branch = _resolve_worktree_endpoint(
            project_id, base_root, endpoint, "target"
        )
        from . import branch_merge
        claim = branch_merge.get_branch_merge_group_claim(group_id)
        if claim is not None:
            raise GitServiceError(
                409,
                "branch_merge_target_group_busy",
                f"target group '{group_id}' has an active branch merge claim",
                {"group_id": group_id, "claim": claim},
            )
        from modules.flow_gate.services import git_service as _gs
        if _gs.db_git.get_open_session_by_group(group_id) is not None:
            raise GitServiceError(
                409,
                "branch_merge_target_group_busy",
                f"target group '{group_id}' has an open merge session",
                {"group_id": group_id},
            )
        if _dirty(wt_path, include_untracked=True):
            dirty_files = _dirty_files(wt_path, include_untracked=True)
            raise GitServiceError(
                409,
                "branch_merge_target_worktree_dirty",
                "target worktree has uncommitted changes",
                {"files": dirty_files, "group_id": group_id},
            )
        from modules.flow_gate.db import group_ai_leases
        from modules.flow_gate.services import ai_invoke_service

        if ai_invoke_service.has_active_run(group_id) or group_ai_leases.get_active(group_id) is not None:
            raise GitServiceError(
                409,
                "branch_merge_target_group_ai_active",
                f"an AI run or lease is active for target group '{group_id}'",
                {"group_id": group_id},
            )

        return ResolvedTarget(
            kind="worktree",
            branch=endpoint.branch,
            group_id=group_id,
            root=wt_path.resolve(),
            managed_workspace=False,
        )

    raise GitServiceError(422, "branch_merge_invalid_kind", f"unsupported target kind '{kind}'")


def merge_branches(
    project_id: str, source_branch: str, target_branch: str, push: bool = True,
    *,
    source_kind: str = "branch",
    source_group_id: Optional[str] = None,
    target_kind: str = "branch",
    target_group_id: Optional[str] = None,
    requested_by: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> dict:
    """Merge one ordinary local branch into another, optionally publishing it.

    flowgate.default.0630 T0005 (D0004): every merge that passes the preconditions first
    writes a persistent ``branch_merge`` attempt (``git_merge_session``, owner
    ``branch_merge``, no group) and runs in a managed target workspace — a detached one for
    the project base, which Git refuses to check out twice.

    * clean  → the existing merge/push result, the attempt closes ``completed`` and its
               workspace is cleaned up.
    * conflict → NOT an error any more: the workspace keeps MERGE_HEAD/index/markers, the
               conflict files are attached to the attempt, and the answer is the live
               attempt (``status: conflict`` + ``merge_id``) that the existing resolver and
               merge review take over. Nothing is committed, moved or pushed until a person
               approves the reviewed candidate.

    Failures that mean the merge could not even start (invalid branch, dirty base
    checkout, remote divergence, workspace ownership, git failure) keep their errors; an
    attempt row already written for them is closed ``failed``.

    T0006 (kept): ``push=False`` never contacts origin. For the project base target the
    merge commit is applied to the shared base checkout by fast-forward, so the local ref
    and the checked-out tree land together. T0007 rev1 (kept): that base-checkout write is
    blocked while an unresolved merge session holds the base checkout, and git's untracked
    collision refusal is the named, non-destructive 409 — never an abort failure.
    """
    from modules.flow_gate.services import git_service as _gs
    from . import branch_merge, merge_target

    cfg, base_root, base_branch = _branch_context(project_id)
    source_ep = MergeEndpointIdentity(
        kind=source_kind or "branch", branch=source_branch, group_id=source_group_id
    )
    target_ep = MergeEndpointIdentity(
        kind=target_kind or "branch", branch=target_branch, group_id=target_group_id
    )
    source_resolved = resolve_source(project_id, base_root, base_branch, source_ep)
    target_resolved = resolve_target(project_id, base_root, base_branch, target_ep, source_resolved)

    is_base_target = (target_resolved.kind == "branch" and target_branch == base_branch)
    apply_to_base_checkout = is_base_target and not push
    if apply_to_base_checkout:
        _gs.guard_base_free(project_id)   # 0205 §2.2 — 1st gate (before lock)

    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(409, "git_busy", "another Git operation is in progress")
    owner = f"branch-merge:{uuid.uuid4()}"
    attempt_open = False
    prepared = False
    keep_workspace = False
    try:
        source_resolved = resolve_source(project_id, base_root, base_branch, source_ep)
        target_resolved = resolve_target(project_id, base_root, base_branch, target_ep, source_resolved)
        if target_resolved.kind == "worktree":
            ctx = merge_target.MergeTargetContext(
                project_id=project_id, base_branch=base_branch, target_branch=target_branch,
                is_project_base=False, root=target_resolved.root,
                workspace_dir=None, workspace_key=None,
                owner=owner, lock_holder=holder,
                target_kind="worktree", target_group_id=target_resolved.group_id,
                managed_workspace=False,
            )
        else:
            if apply_to_base_checkout:
                _gs.guard_base_free(project_id)   # 0205 §2.2 — 2nd gate (race close, after lock)
                if _gs._dirty(base_root, include_untracked=False):
                    raise GitServiceError(
                        409, "branch_merge_target_dirty",
                        "target branch checkout has uncommitted changes",
                    )
            # One live attempt per target workspace: another open branch merge or finalize
            # attempt on the same target is refused before anything is written.
            merge_target.raise_if_workspace_unavailable(project_id, target_branch)
            wdir = merge_target.workspace_dir(project_id, target_branch)
            ctx = merge_target.MergeTargetContext(
                project_id=project_id, base_branch=base_branch, target_branch=target_branch,
                is_project_base=False, root=wdir / merge_target.WORKSPACE_TREE,
                workspace_dir=wdir, workspace_key=merge_target.workspace_key(target_branch),
                owner=owner, lock_holder=holder,
                target_kind="branch", target_group_id=None,
                managed_workspace=True,
            )
        local_target_head = _gs._rev_parse(base_root, f"refs/heads/{target_branch}")
        ctx = branch_merge.open_attempt(
            ctx, source_branch=source_branch, push=push, base_branch=base_branch,
            target_is_base=is_base_target, requested_by=requested_by, provider_id=provider_id,
            source_kind=source_resolved.kind, source_group_id=source_resolved.group_id,
        )
        attempt_open = True
        # Git refuses a second checkout of the project base. A detached managed worktree
        # preserves the shared checkout while HEAD is pushed / applied explicitly.
        if ctx.managed_workspace:
            merge_target.prepare_workspace(ctx, detached=is_base_target)
            prepared = True
        merge_root = ctx.root
        username = cfg.get("username")
        secret = _gs._load_secret_for(cfg) or ""
        # flowgate.default.0361 NR0003 §5.4/§8.1: a cross-branch merge fetches and
        # later pushes through this same repository's `origin` — sync it first so
        # a repo_url change is not silently ignored by this entry point either.
        _gs.ensure_origin_matches_config(base_root, (cfg.get("repo_url") or "").strip())
        fetch = _gs._run_git(
            ["fetch", "origin"], cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
            username=username, secret=secret,
        )
        if fetch.returncode != 0:
            raise GitServiceError(500, "git_error", "Git fetch failed", diagnostic=_one_line(fetch.stderr))
        if _gs._ref_exists(merge_root, f"refs/remotes/origin/{target_branch}"):
            update = _gs._run_git(["merge", "--ff-only", f"origin/{target_branch}"], cwd=merge_root)
            if update.returncode != 0:
                # An untracked collision makes git refuse before touching HEAD at
                # all — that is not divergence (the branch really could fast-
                # forward) and there is nothing to abort or clean up.
                blockers = _gs._untracked_merge_blockers(update.stderr)
                if blockers is not None:
                    raise GitServiceError(
                        409, "branch_merge_untracked_conflict",
                        "branch merge is blocked by untracked files that collide "
                        "with the target branch's remote counterpart",
                        {"source_branch": source_branch, "target_branch": target_branch,
                         "files": blockers},
                    )
                raise GitServiceError(
                    409, "branch_merge_target_diverged",
                    "target branch cannot fast-forward to its remote counterpart",
                    diagnostic=_one_line(update.stderr),
                )
        source_before = source_resolved.head_sha
        target_before = _gs._rev_parse(merge_root, "HEAD")
        expected_remote_head = _gs._rev_parse(merge_root, f"refs/remotes/origin/{target_branch}")
        # T0012 §6.3: pinned BEFORE `git merge`, so recovery can tell "never merged" from
        # "the merge commit landed" after a crash in between.
        merge_target.record_merge_inputs(
            ctx, pre_head=target_before, source_head=source_before,
            expected_remote_head=expected_remote_head,
        )
        merged = _gs._run_git(
            [*_gs._GIT_IDENT, "-c", "merge.conflictStyle=zdiff3", "merge", "--no-ff",
             "-m", f"Merge branch '{source_branch}' into {target_branch}", source_before],
            cwd=merge_root,
        )
        if merged.returncode != 0:
            files_proc = _gs._run_git(["diff", "--name-only", "--diff-filter=U"], cwd=merge_root)
            files = [line for line in (files_proc.stdout or "").splitlines() if line]
            if not files:
                # No conflict markers means git never started the merge at all — an
                # untracked collision refuses BEFORE MERGE_HEAD exists. Nothing to keep.
                blockers = _gs._untracked_merge_blockers(merged.stderr)
                _gs._run_git(["merge", "--abort"], cwd=merge_root)
                if blockers is not None:
                    raise GitServiceError(
                        409, "branch_merge_untracked_conflict",
                        "branch merge is blocked by untracked files that collide "
                        "with the source branch",
                        {"source_branch": source_branch, "target_branch": target_branch,
                         "files": blockers},
                    )
                raise GitServiceError(
                    500, "branch_merge_failed", "Git could not merge the branches",
                    diagnostic=_one_line(merged.stderr),
                )
            # 0630 T0005: the conflict is KEPT — no `merge --abort`, no workspace cleanup.
            branch_merge.mark_conflict(
                ctx, files, source_head=source_before, target_head=target_before,
                merge_head=_gs._rev_parse(merge_root, "MERGE_HEAD"),
                expected_remote_head=expected_remote_head,
                local_target_head=local_target_head,
            )
            keep_workspace = True
            return branch_merge.conflict_response(ctx, files)
        if push:
            push_proc = _gs._run_git(
                ["push", "origin", f"HEAD:{target_branch}"], cwd=merge_root,
                timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
            )
            if push_proc.returncode != 0:
                _gs._run_git(["reset", "--hard", target_before], cwd=merge_root)
                raise GitServiceError(
                    500, "branch_merge_push_failed", "Git push was rejected",
                    diagnostic=_one_line(push_proc.stderr),
                )
        target_after = _gs._rev_parse(merge_root, "HEAD")
        if apply_to_base_checkout:
            # The local base ref and the shared checkout's tree advance together, exactly
            # as when this combination merged in the base checkout itself (T0006 T5).
            applied = _gs._run_git(["merge", "--ff-only", target_after], cwd=base_root)
            if applied.returncode != 0:
                blockers = _gs._untracked_merge_blockers(applied.stderr)
                if blockers is not None:
                    raise GitServiceError(
                        409, "branch_merge_untracked_conflict",
                        "branch merge is blocked by untracked files in the base checkout "
                        "that collide with the merge result",
                        {"source_branch": source_branch, "target_branch": target_branch,
                         "files": blockers},
                    )
                raise GitServiceError(
                    409, "branch_merge_target_diverged",
                    "the base checkout could not fast-forward to the merge result",
                    diagnostic=_one_line(applied.stderr),
                )
        branch_merge.complete_clean(ctx, merge_commit=target_after, pushed=push)
        prepared = False
        if ctx.managed_workspace and ctx.workspace_dir and merge_target.read_owner_marker(ctx.workspace_dir) is not None:
            raise GitServiceError(
                500, "branch_merge_cleanup_failed",
                "branch merge succeeded but its managed workspace could not be cleaned",
                {"target_branch": target_branch, "target_head": target_after,
                 "merge_id": ctx.merge_id},
            )
        return {
            "ok": True, "status": "merged", "merge_id": ctx.merge_id,
            "source_branch": source_branch, "target_branch": target_branch,
            "source_head": source_before, "target_before": target_before,
            "target_head": target_after, "pushed": push,
            "workspace_cleaned": bool(ctx.managed_workspace),
        }
    except GitServiceError as exc:
        if attempt_open and not keep_workspace:
            branch_merge.fail_in_progress(ctx, {"code": exc.code, "message": exc.message})
            prepared = False
        raise
    except Exception as exc:
        if attempt_open and not keep_workspace:
            branch_merge.fail_in_progress(ctx, {"code": "unexpected_error",
                                                "message": exc.__class__.__name__})
            prepared = False
        raise
    finally:
        if prepared and not keep_workspace:
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
        context = _gs.db_git.session_context(session)
        target = context.get("target_branch")
        # 0630 T0005 (D0004 §22): an open ordinary branch merge pins BOTH its branches —
        # the target it will land on and the source it merges from.
        source = (context.get("branch_merge") or {}).get("source_branch")
        if (state or {}).get("branch") == name or target == name or source == name:
            details = {
                "blocking_group_id": group_id,
                "merge_id": session.get("merge_id"),
            }
            if _gs.db_git.is_branch_merge_session(session):
                details["blocking_owner_type"] = _gs.db_git.OWNER_BRANCH_MERGE
                details["role"] = "target" if target == name else "source"
            return "branch_in_use", details
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
        # 0630 T0005 §12: open ordinary branch-merge attempts, so the Branch Manager can
        # re-enter a conflict after a reload without a second request.
        "open_branch_merges": _open_branch_merge_summaries(project_id),
    }


def _open_branch_merge_summaries(project_id: str) -> list[dict]:
    """Best-effort: a session-table problem must never break the branch catalog."""
    from . import branch_merge
    try:
        attempts = branch_merge.list_attempts(project_id)["result"]["attempts"]
    except Exception:
        return []
    keys = ("merge_id", "source_branch", "target_branch", "source_kind", "source_group_id",
            "target_kind", "target_group_id", "state", "push", "file_count", "resolved_count")
    return [{key: attempt.get(key) for key in keys} for attempt in attempts]
