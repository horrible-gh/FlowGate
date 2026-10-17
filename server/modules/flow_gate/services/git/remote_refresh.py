"""Fetch-only remote refresh (0066 D#1 §3-7, E12).

Under R alone: origin is synced with the configuration and ``fetch --prune origin``
refreshes the remote-tracking refs (a branch deleted on origin loses its stale tracking
ref, so E10/E12 show it as ``remote_branch_missing``). No local branch moves (no ff-only), neither the base checkout nor a
Group worktree changes, and B is not taken. The recovery ``POST …/git/fetch``
(``base_slot.manual_fetch``, fetch + base fast-forward) is unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import base_slot
from . import manual_merge
from . import merge_target
from .credentials import GitServiceError


def _ahead_behind(repo: Path, branch: str) -> dict:
    """Local ``branch`` against ``origin/<branch>`` from local refs only."""
    from modules.flow_gate.services import git_service as _gs
    out = {"branch": branch, "remote_branch_missing": None, "ahead_count": None, "behind_count": None}
    if not branch or not _gs._ref_exists(repo, f"refs/heads/{branch}"):
        return out
    if not _gs._ref_exists(repo, f"refs/remotes/origin/{branch}"):
        out["remote_branch_missing"] = True
        return out
    proc = _gs._run_git(["rev-list", "--left-right", "--count",
                         f"refs/remotes/origin/{branch}...refs/heads/{branch}"], cwd=repo)
    parts = (proc.stdout or "").split() if proc.returncode == 0 else []
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        out.update(remote_branch_missing=False, behind_count=int(parts[0]), ahead_count=int(parts[1]))
    return out


def _group_view(project_id: str, group_id: str, base_root: Path, cfg: dict) -> dict:
    from modules.flow_gate.services import git_service as _gs
    work_base_ref = _gs.resolve_group_work_base_ref(project_id, group_id, config=cfg) \
        or merge_target.project_base_branch(cfg)
    view = manual_merge.merge_results_view(group_id)
    return {
        "group_id": group_id,
        "work_base": {"work_base_ref": work_base_ref, **_ahead_behind(base_root, work_base_ref)},
        "results": [{k: row[k] for k in ("merge_id", "target", "remote", "push_available",
                                         "push_blocked_reason")} for row in view["results"]],
        "push_candidates": view["push_candidates"],
        "group_blocker": view["group_blocker"],
    }


def fetch_refs(project_id: str, group_id: Optional[str] = None) -> dict:
    """POST …/projects/{pid}/git/fetch-refs."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs._require_enabled_config(project_id)
    base_branch = merge_target.project_base_branch(cfg)
    project_name = _gs._project_name(project_id)
    base_root = _gs.src_root(project_name, base_branch) if project_name else None
    if base_root is None or _gs._judge_base_slot(base_root, base_branch) != "checkout":
        raise GitServiceError(409, "invalid_state", "base checkout is not available for fetch")
    if group_id is not None:
        if not isinstance(group_id, str) or _gs._project_of_group(group_id) != project_id \
                or _gs.db_git.get_state(group_id) is None:
            raise GitServiceError(422, "invalid_request", "group_id is not a Git group of this project")
    if not _gs.git_available():
        raise GitServiceError(500, "git_unavailable",
                              "git binary not found on server (install git in the runtime image)")
    with base_slot._domain_locks(project_id, "R"):
        _gs.ensure_origin_matches_config(base_root, (cfg.get("repo_url") or "").strip())
        proc = _gs._run_git(
            ["fetch", "--prune", "origin"],
            cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "",
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
    ahead, behind = _gs._base_ahead_behind(base_root, base_branch)
    result = {"fetched": True, "base_branch": base_branch, "ahead_count": ahead,
              "behind_count": behind, "group": None}
    if group_id is not None:
        result["group"] = _group_view(project_id, group_id, base_root, cfg)
    return result
