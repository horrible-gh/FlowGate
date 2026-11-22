"""Reconstruct a clean group on its freshly fetched base, preserving both histories.

The recovery ref and session are durable before HEAD moves. Conflicts use the normal
group-update resolver; explicit abort and the abandoned-session sweep restore the
original branch tip. This never chooses a conflicting side automatically.
"""
from .credentials import GitServiceError, _author_env_from_cfg


def _git(root, args, cfg=None):
    from modules.flow_gate.services import git_service as gs
    proc = gs._run_git(args, cwd=root, **({"author_env": _author_env_from_cfg(cfg)} if cfg else {}))
    if proc.returncode:
        raise GitServiceError(409, "reconstruction_failed", "Git reconstruction failed",
                              diagnostic=gs._last_line(proc.stderr))
    return (proc.stdout or "").strip()


def restore(root, context):
    """Refuse an unexpected HEAD; --merge also preserves unrelated unstaged edits."""
    original = context.get("reconstruct_head")
    if not original:
        return
    head = _git(root, ["rev-parse", "HEAD"])
    if head == original:
        return
    source = context["source_sha"]
    if head != source:
        parents = _git(root, ["rev-list", "--parents", "-n", "1", "HEAD"]).split()[1:]
        if parents != [source, original]:
            raise GitServiceError(409, "reconstruction_head_changed", "branch changed during reconstruction")
    _git(root, ["reset", "--merge", original])


def apply(group_id, project_id, root, cfg, state, pinned):
    from modules.flow_gate.services import git_service as gs
    from . import scope_base
    if gs._dirty(root, include_untracked=True):
        raise GitServiceError(409, "uncommitted_changes", "commit changes before reconstruction")
    original = _git(root, ["rev-parse", "HEAD"])
    source = pinned["source_sha"]
    context = {"prev_status": state.get("status") or "none", "branch": state.get("branch"),
               "source_ref": pinned["source_ref"], "source_sha": source,
               "floor_before": pinned["floor_sha"], "reconstruct_head": original}
    merge_id = gs.db_git.create_session(group_id, [], kind=gs.db_git.SESSION_KIND_GROUP_UPDATE,
                                        context=context)
    try:
        _git(root, ["update-ref", f"refs/flowgate/reconstruction/{merge_id}", original])
        _git(root, ["reset", "--keep", source])
        proc = gs._run_git([*gs._GIT_IDENT, "-c", "rerere.enabled=false", "merge", "--no-ff",
                           original, "-m", "Reconstruct group on latest base"], cwd=root,
                          author_env=_author_env_from_cfg(cfg))
        conflicts = gs._unmerged_paths(root)
        if proc.returncode and conflicts:
            gs.db_git.add_session_files(merge_id, conflicts)
            return {"ok": True, "result": {"status": "conflict", "merge_id": merge_id,
                    "source_ref": pinned["source_ref"], "source_sha": source, "conflict_files": conflicts}}
        if proc.returncode:
            raise GitServiceError(409, "reconstruction_failed", "Git reconstruction failed",
                                  diagnostic=gs._last_line(proc.stderr))
        from modules.flow_gate.db.connection import get_store
        with get_store().transaction():
            recorded = scope_base.record_update(project_id, group_id, root,
                source_ref=pinned["source_ref"], source_sha=source, floor_before=pinned["floor_sha"],
                path="reconstruct", merge_id=merge_id, config=cfg)
            gs.db_git.close_session(merge_id, "done")
        return {"ok": True, "result": {"status": "updated", "branch": state.get("branch"), **recorded}}
    except Exception:
        merging = gs._run_git(["rev-parse", "--verify", "MERGE_HEAD"], cwd=root)
        if merging.returncode == 0:
            _git(root, ["merge", "--abort"])
        restore(root, context)
        gs.db_git.close_session(merge_id, "aborted")
        raise
