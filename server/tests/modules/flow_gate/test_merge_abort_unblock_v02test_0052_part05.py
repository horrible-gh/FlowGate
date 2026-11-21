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

def test_the_merge_review_read_never_calls_touch_session(proj, monkeypatch):
    """The merge review GET never reaches ``db_git.touch_session`` (a spy, so no timestamp
    resolution can hide a call). The TR conflict review GET is covered on a real TR
    session by test_conflict_flow_simplification_0668
    ``test_opening_the_tr_review_is_not_activity``."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(12)
    merge_id, _root = _resolve_everything(proj, gid)
    touched: list[int] = []
    monkeypatch.setattr(db_git, "touch_session", lambda mid: touched.append(mid))
    svc.get_merge_review(gid, merge_id)
    assert touched == []
def test_hold_abort_failure_is_audited_on_the_session(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import rerere_checkpoint
    from modules.flow_gate.services.git_service import GitServiceError

    gid = proj.group(13)
    merge_id = _two_file_conflict(proj, gid)["merge_id"]
    root = svc.resolve_conflict_src_root(gid, merge_id)
    svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
    }], False)
    real_run = rerere_checkpoint._run
    locks: list[Path] = []

    def rerere_then_lock(r):
        real_run(r)
        locks.append(_hold_index_lock(root))     # only the abort that follows meets it

    monkeypatch.setattr(rerere_checkpoint, "_run", rerere_then_lock)
    try:
        with pytest.raises(GitServiceError) as caught:
            rerere_checkpoint.hold(gid, merge_id)
    finally:
        for lock in locks:
            lock.unlink()
    assert caught.value.status == 500
    assert "index.lock" in caught.value.details["reason"]
    _assert_abort_audited(merge_id, "hold_abort", "hold_abort")
    assert _merge_head(root) is not None
    monkeypatch.setattr(rerere_checkpoint, "_run", real_run)
    svc.abort_merge(gid, merge_id)
def test_group_update_sweep_abort_failure_is_audited_on_the_session(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import cleanup

    gid = proj.group(14)
    root, _side = _conflict_in_group_worktree(proj, gid)
    branch = db_git.get_state(gid)["branch"]
    assert svc.src_root(svc._project_name(proj.pid), branch) == root
    merge_id = db_git.create_session(
        gid, ["shared.txt"], kind=db_git.SESSION_KIND_GROUP_UPDATE, context={"branch": branch},
    )
    _age_session(merge_id)
    lock = _hold_index_lock(root)
    try:
        cleanup._sweep_group_update_session(_session(merge_id), proj.pid)
    finally:
        lock.unlink()
    _assert_abort_audited(merge_id, "group_update_sweep_abort", "sweep_abort")
    assert _merge_head(root) is not None                     # never force-reset

    cleanup._sweep_group_update_session(_session(merge_id), proj.pid)
    assert _session(merge_id)["status"] == "aborted" and _merge_head(root) is None
