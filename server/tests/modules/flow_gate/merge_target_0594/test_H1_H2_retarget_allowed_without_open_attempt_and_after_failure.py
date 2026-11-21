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
def test_H1_H2_retarget_allowed_without_open_attempt_and_after_failure(proj, tmp_path):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.local_branch("release")
    (proj.wt(gid) / "h.txt").write_text("h\n", encoding="utf-8")
    proj.ready(gid)
    cfg = db_git.get_config(proj.pid)
    # H1: nothing open → any allowed target
    assert mt.plan_finalize_target(gid, proj.pid, cfg, "develop").target_branch == "develop"
    assert mt.plan_finalize_target(gid, proj.pid, cfg, "release").target_branch == "release"

    # H2: an attempt on develop fails (develop is checked out elsewhere) …
    blocker = tmp_path / "blocker"
    _git(["worktree", "add", str(blocker), "develop"], cwd=proj.base)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_checkout_failed"
    failed = [
        s for s in db_git.list_open_sessions() if s["group_id"] == gid
    ]
    assert failed == []
    state = db_git.get_state(gid)
    assert state["status"] == "awaiting_choice"
    from modules.flow_gate.db.connection import get_store
    row = get_store()._fetch_one(
        "SELECT * FROM git_merge_session WHERE group_id = ? ORDER BY merge_id DESC", [gid],
    )
    assert row["status"] == "aborted"
    assert db_git.session_context(row)["attempt_state"] == "failed"
    assert db_git.session_context(row)["merge_target"]["branch"] == "develop"
    assert not proj.workspace("develop").exists()
    _git(["worktree", "remove", "--force", str(blocker)], cwd=proj.base)
    # … and the retry may pick another target
    out = svc.finalize(gid, "merge", target_branch="release")
    assert out["result"]["status"] == "merged"
    assert "h.txt" in proj.origin_files("release")

