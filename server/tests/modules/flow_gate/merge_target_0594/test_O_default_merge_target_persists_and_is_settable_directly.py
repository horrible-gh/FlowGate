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
def test_O_default_merge_target_persists_and_is_settable_directly(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    assert (db_git.get_config(proj.pid) or {}).get("default_merge_target") is None

    gid = proj.group(1)
    assert svc.list_branches(proj.pid)["default_merge_target"] is None
    proj.local_branch("integration-v2")
    (proj.wt(gid) / "a.txt").write_text("a\n", encoding="utf-8")
    proj.ready(gid)
    out = svc.finalize(gid, "merge", target_branch="integration-v2")
    assert out["result"]["status"] == "merged"
    assert db_git.get_config(proj.pid)["default_merge_target"] == "integration-v2"
    assert svc.list_branches(proj.pid)["default_merge_target"] == "integration-v2"

    # Its own action: set/clear the suggestion directly, no merge involved.
    proj.local_branch("integration-v3")
    result = mt.set_project_default_target(proj.pid, "integration-v3")
    assert result == {"ok": True, "default_merge_target": "integration-v3"}
    assert db_git.get_config(proj.pid)["default_merge_target"] == "integration-v3"

    # server-side allow-list still applies — never trusts the caller.
    with pytest.raises(svc.GitServiceError) as caught:
        mt.set_project_default_target(proj.pid, "does-not-exist")
    assert caught.value.code == "merge_target_not_found"

    cleared = mt.set_project_default_target(proj.pid, None)
    assert cleared == {"ok": True, "default_merge_target": None}
    assert db_git.get_config(proj.pid)["default_merge_target"] is None

    # The branch currently pinned as the merge-target suggestion
    # is itself undeletable, both through the guard and the catalog's can_delete
    # flag, until it is retargeted or cleared.
    mt.set_project_default_target(proj.pid, "integration-v3")
    assert svc.check_branch_delete(proj.pid, "integration-v3") == "branch_is_default_merge_target"
    row = next(b for b in svc.list_branches(proj.pid)["branches"] if b["name"] == "integration-v3")
    assert row["can_delete"] is False
    assert row["delete_blocked_reason"] == "branch_is_default_merge_target"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.delete_branch(proj.pid, "integration-v3")
    assert caught.value.code == "branch_is_default_merge_target"
    assert svc.list_branches(proj.pid)["default_merge_target"] == "integration-v3"

    # clearing the suggestion first unblocks the delete.
    mt.set_project_default_target(proj.pid, None)
    svc.delete_branch(proj.pid, "integration-v3")
    assert svc.list_branches(proj.pid)["default_merge_target"] is None

    # defense in depth: even if a dangling suggestion occurred by some other
    # path (e.g. the branch ref removed outside this API), the catalog must
    # never surface it as the current target.
    proj.local_branch("integration-v4")
    mt.set_project_default_target(proj.pid, "integration-v4")
    _git(["branch", "-D", "integration-v4"], cwd=proj.base)
    assert svc.list_branches(proj.pid)["default_merge_target"] is None

