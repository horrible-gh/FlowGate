"""Selected finalize target: isolated scenario from the shared 0594 harness."""
from __future__ import annotations

from test_git_merge_target_0594 import (
    Path,
    Proj,
    _COUNTER,
    _Crash,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _arm_crash,
    _assert_off_branch,
    _check_view,
    _conflict_on,
    _crash_finalize,
    _ctx,
    _end_job,
    _feature_group,
    _git,
    _judge,
    _legacy_group,
    _live_job,
    _merge_head,
    _merge_rows,
    _open_attempt,
    _recover_job_after_crash,
    _refused,
    _seed_wf_done_root,
    _session,
    base64,
    check_budget,
    contextmanager,
    held_locks,
    itertools,
    json,
    needs_git,
    os,
    patch_store,
    proj,
    pytest,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    uuid,
)


@needs_git
def test_G_mismatch_leaves_merge_state_row_and_workspace_on_every_path(proj, monkeypatch):
    """§9.1/§9.3: abort, TTL, orphan/startup recovery check the owner BEFORE git runs."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import cleanup

    gid = proj.group(1)
    proj.local_branch("develop")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    ws = proj.workspace("develop")
    tree = ws / "tree"
    head_before = _merge_head(tree)
    content_before = (tree / "shared.txt").read_text(encoding="utf-8")
    status_before = _git(["status", "--porcelain"], cwd=tree)
    assert head_before and "<<<<<<<" in content_before
    marker = json.loads((ws / "owner.json").read_text(encoding="utf-8"))
    marker["owner"] = "someone-else"
    (ws / "owner.json").write_text(json.dumps(marker), encoding="utf-8")

    def untouched():
        assert _merge_head(tree) == head_before
        assert (tree / "shared.txt").read_text(encoding="utf-8") == content_before
        assert _git(["status", "--porcelain"], cwd=tree) == status_before
        assert _session(merge_id)["status"] == "open"
        assert _ctx(merge_id)["attempt_state"] == "conflict"
        assert db_git.get_state(gid)["status"] == "conflict"
        assert (ws / "owner.json").exists()

    with pytest.raises(svc.GitServiceError) as caught:
        svc.abort_merge(gid, merge_id)
    assert caught.value.code == "merge_target_owner_mismatch"
    untouched()
    monkeypatch.setattr(cleanup, "_ttl_expired", lambda _last: True)
    svc.merge_session_sweep()
    untouched()
    svc.startup_recovery()
    untouched()
    # the TTL reclaimer itself re-checks under its own lock
    cleanup._auto_abort_session(_session(merge_id), proj.pid, tree, "ttl_expired")
    untouched()
    # orphan close refuses too (e.g. MERGE_HEAD read as gone)
    svc._close_orphan(_session(merge_id), proj.pid)
    untouched()

