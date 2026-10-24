"""Gate archive checkpoints in the existing archive list and restore (flowgate.v02.0085 T#3 rework, D0007 §7.1).

The existing group archive (v0.1, ``inbox_routes``: one record per group in the settings, ``refs/flowgate/archive/...``,
a consuming restore that drops its refs, merged / pushed groups refused) is kept as it is. A gate's checkpoint is one
row per decision (``gate_archive_checkpoints``: archive id, decision, run, gate, attempt, frozen / applied SHA and their
refs ``refs/flowgate/gate-archive/<decision>[-applied]``); several rounds of a group stand side by side.

- ``list_for_project``: the checkpoints as items of the existing archive list (``GET /projects/{p}/git/archives``),
  told apart by ``kind`` = ``gate_checkpoint`` and ``consumable`` = false.
- ``restore``: ``POST /groups/{g}/git/archive/restore`` with ``archive_id`` of a gate checkpoint (without it the v0.1
  restore runs as before). Non-consuming: the group branch comes back at the checkpoint's frozen SHA (create-only, under
  the group's G and M) and the existing provisioning attaches the work tree (``ensure_worktree``'s ``start_point``);
  the checkpoint row and its refs stay, only ``last_restored_at`` moves. A slot already at that SHA is a no-op
  (``already_at_checkpoint``); a group branch at another commit is never moved (409 ``restore_conflict``). Uncommitted
  files were never part of a gate checkpoint, so none come back.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from flowgate.gate_decisions import git_adapter as git
from flowgate.gate_decisions import repository as repo

KIND_GATE = "gate_checkpoint"


def _error(status: int, code: str, message: str, **details):
    from modules.flow_gate.services.git.credentials import GitServiceError
    raise GitServiceError(status, code, message, details=details or None)


def item(cp: dict, title: Optional[str] = None) -> dict:
    """One checkpoint as an item of the existing archive list."""
    info = cp.get("restore_info") or {}
    return {"kind": KIND_GATE, "consumable": False, "status": "archived", "archive_id": cp["archive_id"],
            "group_id": cp["group_id"], "title": title or cp["group_id"], "archived_at": cp.get("preserved_at"),
            "decision_id": cp["decision_id"], "run_id": cp.get("run_id"), "node_key": cp.get("node_key"),
            "attempt_id": cp.get("attempt_id"), "source_sha": cp.get("source_sha"),
            "applied_sha": cp.get("applied_sha"), "head_ref": cp.get("ref_name"),
            "applied_ref": cp.get("applied_ref"), "branch": info.get("branch"), "mode": info.get("mode"),
            "last_restored_at": cp.get("last_restored_at")}


def list_for_project(project_id: str) -> list[dict]:
    from modules.flow_gate.db import groups as db_groups
    out = []
    for cp in repo.checkpoints_of_project(project_id):
        group = db_groups.get_by_id(cp["group_id"]) or {}
        out.append(item(cp, group.get("title")))
    return out


def restore(group_id: str, archive_id: str, actor_user_id: Optional[str] = None) -> dict:
    """The non-consuming restore of one gate checkpoint (module doc). Raises GitServiceError (404 / 409 / 500)."""
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import lock_manager as locks
    cp = repo.checkpoint(archive_id)
    if cp is None or cp["group_id"] != group_id:
        _error(404, "archive_not_found", f"gate checkpoint '{archive_id}' of '{group_id}' not found")
    group = db_groups.get_by_id(group_id)
    if group is None:
        _error(404, "not_found", f"group '{group_id}' not found")
    project_id = group.get("project_id")
    cfg = gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        _error(409, "invalid_state", "git integration is not enabled")
    base_root = gs._base_root_of(project_id)
    if base_root is None:
        _error(409, "invalid_state", "base checkout is not provisioned")
    base_root = Path(base_root)
    sha, ref = cp["source_sha"], cp["ref_name"]
    if git.rev(base_root, ref) != sha or not git.commit_exists(base_root, sha):
        _error(409, "archive_corrupt", "the checkpoint ref is missing or moved")
    state = gs.db_git.get_state(group_id) or {}
    branch = ((cp.get("restore_info") or {}).get("branch") or state.get("branch") or "").strip()
    if not branch:
        _error(409, "invalid_state", "the group branch of the checkpoint is unknown")
    ctx = locks.new_context("req")
    held = []
    try:
        for domain, extra in (("G", {"group_id": group_id}), ("M", {})):
            o = locks.acquire(domain, project_id, holder_kind="archive", mode="interactive", ctx=ctx, **extra)
            if not o.ok:
                _error(409, "git_busy", "the group's Git work is busy", **locks.outcome_details(o))
            held.append(o.lock_key)
        current = git.rev(base_root, f"refs/heads/{branch}")
        if current is git.READ_ERROR:
            _error(500, "restore_failed", "the group branch cannot be read")
        if current is not None and current != sha:
            _error(409, "restore_conflict", f"local branch '{branch}' is at another commit",
                   branch_sha=current, checkpoint_sha=sha)
        made = "same" if current == sha else git.create_ref(base_root, f"refs/heads/{branch}", sha)
        if made not in ("created", "same"):
            _error(409, "restore_conflict", f"local branch '{branch}' could not be created")
    finally:
        for key in reversed(held):
            locks.release(ctx, key)
    attached = bool(int(state.get("worktree_registered") or 0)) and gs.effective_src_root(project_id, group_id)
    if not attached:
        module = group.get("module") or group_id.split(".")[1]
        if gs.ensure_worktree(project_id, module, group_id, trigger="gate_checkpoint_restore",
                              start_point=sha) != "ok":
            _error(500, "restore_failed", "the work tree could not be attached")
    repo.mark_restored(archive_id)                                  # noted, never consumed
    stored = repo.checkpoint(archive_id) or cp
    return {"kind": KIND_GATE, "archive_id": archive_id, "group_id": group_id, "branch": branch, "sha": sha,
            "consumed": False, "outcome": "restored" if made == "created" or not attached else "already_at_checkpoint",
            "restored_by": actor_user_id, "last_restored_at": stored.get("last_restored_at")}
