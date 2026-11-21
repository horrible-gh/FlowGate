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

def test_already_clean_workspace_abort_closes_without_git_abort(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(5)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    _git(["merge", "--abort"], cwd=root)        # cleaned outside the server
    out = svc.abort_merge(gid, merge_id)
    assert out["result"]["already_cleaned"] is True
    assert _session(merge_id)["status"] == "aborted"
def test_failed_abort_carries_git_reason_everywhere(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import git_error_envelope
    from modules.flow_gate.services.git_service import GitServiceError

    gid = proj.group(6)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    lock = _hold_index_lock(root)
    try:
        with pytest.raises(GitServiceError) as caught:
            svc.abort_merge(gid, merge_id)
    finally:
        lock.unlink()
    exc = caught.value
    assert exc.status == 500 and exc.code == "git_error"
    reason = exc.details["reason"]
    assert "index.lock" in reason
    assert exc.params["reason"] == reason and exc.details["recovery"] == "force_cleanup"
    envelope = git_error_envelope(exc)
    assert envelope["error"]["details"]["reason"] == reason
    assert envelope["error"]["params"]["reason"] == reason

    context = _ctx(merge_id)
    op = context["ref_operations"][-1]
    assert op["reason"] == "user_abort" and op["returncode"] != 0
    assert "index.lock" in op["stderr_tail"]
    assert context["abort_failure"]["trigger"] == "user_abort"
    assert context["abort_failure"]["reason"] == reason
    assert _session(merge_id)["status"] == "open"            # nothing hidden as success
    assert svc.list_conflicts(gid, merge_id)["abort_failure"]["reason"] == reason

    listed = svc.list_abort_failures(proj.pid)["result"]["sessions"]
    assert [row["merge_id"] for row in listed] == [merge_id]
    assert listed[0]["target_branch"] == "develop"

    member = _routes_client(MEMBER).get(f"/api/v1/projects/{proj.pid}/git/merge-abort-failures")
    assert member.status_code == 403
    admin = _routes_client(ADMIN).get(f"/api/v1/projects/{proj.pid}/git/merge-abort-failures")
    assert admin.status_code == 200, admin.text
    assert admin.json()["result"]["sessions"][0]["abort_failure"]["reason"] == reason

    # once the lock is gone the ordinary abort works again
    assert svc.abort_merge(gid, merge_id)["result"]["status"] == "waiting"
def test_sweep_failure_marks_session_cleanup_failed(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(7)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    _age_session(merge_id)
    lock = _hold_index_lock(root)
    try:
        svc.merge_session_sweep()
    finally:
        lock.unlink()
    assert _session(merge_id)["status"] == "open"            # never force-reset by the sweep
    failure = _ctx(merge_id)["abort_failure"]
    assert failure["trigger"] == "sweep_abort"
    assert "index.lock" in failure["reason"]
    assert _merge_head(root) is not None
    svc.abort_merge(gid, merge_id)
