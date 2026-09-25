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


def list_group_work_base_options(project_id: str) -> dict:
    """Return only what a requirement author needs to pick a group work base.

    flowgate.default.0613 T0013: the project branch catalog
    (``GET /projects/{id}/git/branches``) is a Branch Manager surface gated by
    ``project.settings.read`` and carries delete/merge metadata.  Requirement
    creation needs neither, so this read-only view lists exactly the names
    :func:`validate_group_work_base_ref` would accept -- ordinary local branches
    that no FlowGate group worktree owns -- plus the project base used as the
    default selection.  A disabled or absent integration answers
    ``git_enabled: False`` with no branches (the legacy, UI-hidden path).
    """
    from modules.flow_gate.services import git_service as _gs

    cfg = _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"ok": True, "git_enabled": False, "base_branch": None, "branches": []}
    base_root = _gs._base_root_of(project_id)
    if base_root is None or not (base_root / ".git").exists():
        raise GitServiceError(
            409,
            "group_work_base_checkout_unavailable",
            "project Git checkout is not available for group work-base validation",
        )
    proc = _gs._run_git(
        ["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=base_root
    )
    if proc.returncode != 0:
        raise GitServiceError(
            500,
            "group_work_base_options_failed",
            "Git could not list branches",
            diagnostic=" ".join((proc.stderr or "").split()),
        )
    names = sorted({line.strip() for line in (proc.stdout or "").splitlines() if line.strip()})
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    branches = [
        {"name": name, "is_project_base": name == base_branch}
        for name in names
        if _gs.db_git.get_state_by_branch(project_id, name) is None
    ]
    return {
        "ok": True,
        "git_enabled": True,
        "base_branch": base_branch,
        "branches": branches,
    }


def groups_pinning_work_base(project_id: str, branch: str) -> list[str]:
    """Return the non-terminal groups whose durable work base is ``branch``.

    The branch delete guard consults this so an ordinary local branch cannot be
    removed while a group may still provision, retry, reprovision or reopen its
    worktree from it.  Legacy (NULL) groups follow the project base, which the
    guard already protects as ``branch_is_base``.
    """
    return [
        row["group_id"]
        for row in db_groups.list_open_groups_by_work_base(project_id, branch)
    ]


def _locked_by_state(state: Optional[dict]) -> bool:
    """True once ``state`` shows real Git work already started from a work base.

    flowgate.default.0613 TR0014 rev2: ``worktree_registered`` covers the
    ordinary case (the worktree exists right now, created via
    ``worktree.py``'s ``... _worktree_start_point(base_root, work_base_ref)``
    fork). But ``worktree.unregister_worktree`` clears that flag on slot
    cleanup while leaving ``status``/``initial_source_sync_at`` as permanent
    group history (0182 NR0003 §5) -- and a later terminal reopen resolves its
    C1 ancestry against ``resolve_group_work_base_ref`` again
    (``worktree._ensure_worktree_locked``'s ``terminal_base_diverged`` check).
    So a stale ``worktree_registered = 0`` must not reopen the field: once the
    group ever synced from its base (``initial_source_sync_at``) or reached any
    non-'none' ledger status, the base is locked for good. A row that only
    records provisioning *failures* (``upsert_provision_failure``, worktree
    never actually created) leaves all three false and stays unlocked.
    """
    if not state:
        return False
    if state.get("worktree_registered"):
        return True
    if state.get("initial_source_sync_at"):
        return True
    status = state.get("status")
    return bool(status) and status != "none"


def group_work_base_locked(project_id: str, group_id: str) -> bool:
    """Whether ``group_id``'s stored work base can still be changed."""
    from modules.flow_gate.services import git_service as _gs

    return _locked_by_state(_gs.db_git.get_state(group_id))


def locked_group_ids(project_id: str) -> set[str]:
    """Batch form of :func:`group_work_base_locked` for one project's groups list."""
    from modules.flow_gate.services import git_service as _gs

    return {
        row["group_id"]
        for row in _gs.db_git.list_states_of_project_any(project_id)
        if _locked_by_state(row)
    }


def apply_group_work_base_ref(project_id: str, group_id: str, value: str) -> str:
    """Update an already-existing group's stored Base Branch.

    T0013's "no arbitrary change to an existing group's stored Base Branch"
    reads, per the rejection that reworked this contract, as: no change once
    that value has actually started to matter to real Git history (see
    :func:`group_work_base_locked`). Before that point the group has not
    forked or synced from it yet, so picking a different ref here is ordinary
    group setup, not a rewrite of established history.
    """
    if group_work_base_locked(project_id, group_id):
        raise GitServiceError(
            409,
            "group_work_base_locked",
            "group work base cannot change once its Git worktree has started",
        )
    ref = validate_group_work_base_ref(project_id, value)
    db_groups.update_work_base_ref(group_id, ref)
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