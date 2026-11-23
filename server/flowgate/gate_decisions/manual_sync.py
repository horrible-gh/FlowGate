"""Explicit remote reconciliation for Git-tab pushes, using the gate's merge engine.

The recorded merge remains intact. No force push, branch reset or implicit commit.
Conflicts use the existing manual review session and retain its original inputs.
"""
from flowgate.gate_decisions import git_adapter as git


def sync(group_id: str, target: str, *, on_started=None) -> dict:
    from modules.flow_gate.services.git import finalize, lock_manager as locks, manual_merge, merge_target
    slot = git.slot_of(group_id)
    rows = manual_merge.merge_results_view(group_id)["results"]
    if not any((row.get("target") or {}).get("target_branch") == target for row in rows):
        raise git.GitServiceError(409, "merge_result_missing", "a completed group merge is required")
    ctx, group_lock = finalize._group_lock(slot.project_id, group_id, "publish")
    held = [group_lock]
    try:
        manual_merge._require_group_valid(group_id)
        is_base = target == merge_target.project_base_branch(slot.cfg)
        for domain in ("B" if is_base else "W", "R"):
            result = locks.acquire(domain, slot.project_id, target_key=target if domain == "W" else None,
                                   holder_kind="publish", ctx=ctx)
            if not result.ok:
                raise git.GitServiceError(409, "git_busy", "another Git operation is active",
                                         details=locks.outcome_details(result))
            held.append(result.lock_key)
        manual_merge._require_group_valid(group_id)
        manual_merge._require_target_owner(slot.project_id, target, is_base)
        if not git.fetch_branch(slot, target):
            raise git.GitServiceError(503, "remote_head_unknown", "remote state could not be read")
        remote = git.remote_tracking_head(slot, target)
        head = git.rev(slot.base_root, "refs/heads/" + target)
        if remote is git.READ_ERROR or head is git.READ_ERROR or not head:
            raise git.GitServiceError(409, "target_ref_unreadable", "target state could not be read")
        if not remote:
            return {"status": "no_change"}
        result = git.sync_merge(slot, "", "", target, head, remote, on_started or (lambda evidence: None),
                                lock_holder=ctx.ctx_id)
        return {"status": "conflict" if result["outcome"] == git.CONFLICT else "updated",
                "merge_id": result.get("merge_id")}
    finally:
        for key in reversed(held):
            if ctx.find_held(key) is not None:
                locks.release(ctx, key)
