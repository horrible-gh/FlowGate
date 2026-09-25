"""Durable per-group work-base contract.

``project_git_config.base_branch`` remains the project default.  A non-NULL
``groups.work_base_ref`` pins the branch/ref selected for one group; legacy rows
fall back to the project default.  Finalize targets and recovery ``start_point``
are intentionally outside this module.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db import groups as db_groups

from .credentials import GitServiceError


def _normalized_ref(value: str) -> str:
    ref = value.strip() if isinstance(value, str) else ""
    if not ref or ref != value:
        raise GitServiceError(
            422,
            "group_work_base_invalid",
            "group work base must be a non-blank branch name without surrounding whitespace",
        )
    if ref == "HEAD" or ref.startswith("-"):
        raise GitServiceError(422, "group_work_base_invalid", "invalid group work base")
    return ref


def validate_group_work_base_ref(project_id: str, value: str) -> str:
    """Validate and return the exact branch name stored on a new group.

    Only ordinary local branches or their ``origin`` tracking counterparts are
    accepted.  Arbitrary revision expressions, commits, FlowGate-owned group
    branches, and a checkout-less/disabled integration fail explicitly.
    """
    from modules.flow_gate.services import git_service as _gs

    _gs._require_enabled_config(project_id)
    ref = _normalized_ref(value)
    base_root = _gs._base_root_of(project_id)
    if base_root is None or not (base_root / ".git").exists():
        raise GitServiceError(
            409,
            "group_work_base_checkout_unavailable",
            "project Git checkout is not available for group work-base validation",
        )

    checked = _gs._run_git(["check-ref-format", "--branch", ref], cwd=base_root)
    if checked.returncode != 0:
        raise GitServiceError(
            422,
            "group_work_base_invalid",
            "group work base is not a valid Git branch name",
        )

    owner = _gs.db_git.get_state_by_branch(project_id, ref)
    if owner is not None:
        raise GitServiceError(
            409,
            "group_work_base_internal",
            "a FlowGate group worktree branch cannot be a group work base",
            {"connected_group_id": owner.get("group_id")},
        )

    local_ref = f"refs/heads/{ref}"
    remote_ref = f"refs/remotes/origin/{ref}"
    if not _gs._ref_exists(base_root, local_ref):
        if _gs._ref_exists(base_root, remote_ref):
            raise GitServiceError(
                409,
                "group_work_base_remote_only",
                "a remote-only branch cannot be a group work base",
                {"work_base_ref": ref},
            )
        raise GitServiceError(
            404,
            "group_work_base_not_found",
            "group work base does not resolve to an available repository branch",
            {"work_base_ref": ref},
        )

    resolved = _gs._run_git(
        ["rev-parse", "--verify", "--quiet", f"{local_ref}^{{commit}}"],
        cwd=base_root,
    )
    if resolved.returncode != 0:
        raise GitServiceError(
            422,
            "group_work_base_not_commit",
            "group work base does not resolve to a commit",
            {"work_base_ref": ref},
        )
    return ref


def resolve_group_work_base_ref(
    project_id: str,
    group_id: str,
    *,
    group: Optional[dict] = None,
    config: Optional[dict] = None,
) -> Optional[str]:
    """Return the authoritative effective work base for a group.

    Resolution is deliberately small and stable: stored group value first,
    otherwise the project's ``base_branch``.  Non-Git projects return ``None``.
    No finalize target, read-only ref, or recovery ``start_point`` participates.
    """
    from modules.flow_gate.services import git_service as _gs

    cfg = config if config is not None else _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return None
    row = group if group is not None else db_groups.get_by_id(group_id)
    stored = ((row or {}).get("work_base_ref") or "").strip()
    if stored:
        return stored
    return (cfg.get("base_branch") or "main").strip() or "main"