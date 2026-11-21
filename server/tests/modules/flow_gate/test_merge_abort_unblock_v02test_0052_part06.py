"""Independently selectable scenarios; shared fixtures remain in test_merge_abort_unblock_v02test_0052.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_merge_abort_unblock_v02test_0052 import (
    ADMIN,
    MEMBER,
    Path,
    Proj,
    _COUNTER,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _age_session,
    _assert_abort_audited,
    _conflict_in_group_worktree,
    _ctx,
    _git,
    _git_path,
    _group_branch_sha,
    _hold_index_lock,
    _merge_head,
    _resolve_everything,
    _rewrite_same_bytes_later,
    _routes_client,
    _seed_wf_done_root,
    _session,
    _stale_state,
    _stat_dirty,
    _two_file_conflict,
    base64,
    contextmanager,
    itertools,
    os,
    patch_store,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    time,
)

def test_graph_attempt_abort_failure_is_audited_and_marked(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import conflict
    from modules.flow_gate.services.git_service import GitServiceError

    gid = proj.group(15)
    root, side = _conflict_in_group_worktree(proj, gid)
    merge_id = db_git.create_session(
        gid, ["shared.txt"], kind=db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE,
        context={"target_sha": side, "prev_status": "waiting"},
    )
    lock = _hold_index_lock(root)
    try:
        with pytest.raises(GitServiceError) as caught:
            conflict._abort_graph_attempt_merge(gid, merge_id, proj.pid, root)
    finally:
        lock.unlink()
    exc = caught.value
    assert exc.status == 500 and exc.code == "git_error"
    assert "index.lock" in exc.details["reason"] and exc.params["reason"] == exc.details["reason"]
    assert exc.details["recovery"] is None                    # force cleanup is merge-only
    _assert_abort_audited(merge_id, "graph_attempt_abort", conflict.GRAPH_ATTEMPT_ABORT_TRIGGER)
    assert _merge_head(root) == side

    out = conflict._abort_graph_attempt_merge(gid, merge_id, proj.pid, root)
    assert out["result"]["status"] == "aborted"
    assert _session(merge_id)["status"] == "aborted" and _merge_head(root) is None
def test_interrupted_recovery_abort_failure_is_audited_on_the_session(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target

    gid = proj.group(16)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    lock = _hold_index_lock(root)
    try:
        assert merge_target.recover_interrupted_attempt(_session(merge_id), "interrupted") is None
    finally:
        lock.unlink()
    _assert_abort_audited(merge_id, "interrupted_recovery_abort", "interrupted_recovery")
    svc.abort_merge(gid, merge_id)
def test_disposed_group_abort_failure_keeps_the_session_open_and_audited(proj):
    """Disposing a group mid-conflict: a failed abort is audited and the DB session is NOT
    closed over a workspace that is still merging (T0003 하지 말 것); once the abort can
    run, the same call closes it."""
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import worktree

    gid = proj.group(17)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    lock = _hold_index_lock(root)
    try:
        worktree._abort_disposed_merge_session(proj.pid, gid, proj.base)
    finally:
        lock.unlink()
    _assert_abort_audited(merge_id, "disposed_group_abort", worktree.DISPOSED_GROUP_ABORT_TRIGGER)
    assert _merge_head(root) is not None

    worktree._abort_disposed_merge_session(proj.pid, gid, proj.base)
    assert _session(merge_id)["status"] == "aborted"
    assert not root.exists() or _merge_head(root) is None
def test_session_audit_keeps_reason_for_local_commands_only(proj, monkeypatch):
    """A failed local command keeps git's reason in ``ref_operations[].stderr_tail``; a
    failed network command (push, fetch, …, or any call with a configured ``secret``)
    keeps none — its stderr may carry a credential only the caller's secret can mask
    (test_git_push_evidence_0153 pins that contract)."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(18)
    merge_id = db_git.create_session(gid, ["shared.txt"])
    leaked = "fatal: could not read from remote: token configured-secret-value\n"
    monkeypatch.setattr(svc, "_run_git", lambda args, **kw: subprocess.CompletedProcess(
        args, 128, "", leaked if args[0] != "merge" else "error: merge refused\n"))

    svc._run_session_git(merge_id, None, "push", "t_push", ["push", "origin", "main"])
    svc._run_session_git(merge_id, None, "merge", "t_fetch", ["fetch", "origin"])
    svc._run_session_git(merge_id, None, "merge", "t_secret", ["rev-parse", "x"], secret="s")
    svc._run_session_git(merge_id, None, "merge", "t_merge", ["merge", "--abort"])
    ops = {op["reason"]: op for op in _ctx(merge_id)["ref_operations"]}
    for name in ("t_push", "t_fetch", "t_secret"):
        assert ops[name]["returncode"] == 128 and "stderr_tail" not in ops[name]
    assert ops["t_merge"]["stderr_tail"] == "error: merge refused"
    assert "configured-secret-value" not in str(_ctx(merge_id))
    db_git.close_session(merge_id, "aborted")
