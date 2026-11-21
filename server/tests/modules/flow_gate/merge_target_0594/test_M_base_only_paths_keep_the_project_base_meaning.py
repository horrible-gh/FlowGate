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
def test_M_base_only_paths_keep_the_project_base_meaning(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc

    # update_from_base's own `git merge` carries no FlowGate identity (unchanged
    # base-only code); give hosts without a global git identity one for this test.
    # (_run_git strips an ambient author on purpose → configure the project author.)
    for key, value in (("GIT_COMMITTER_NAME", "T"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(key, value)
    svc.save_config(proj.pid, {
        "repo_url": proj.bare.as_uri(), "provider": "generic", "base_branch": "main",
        "default_finalize_action": "merge", "enabled": True,
        "author_name": "T", "author_email": "t@t",
    })

    gid1 = proj.group(1)
    gid2 = proj.group(2)
    proj.local_branch("develop")
    (proj.wt(gid1) / "only_develop.txt").write_text("d\n", encoding="utf-8")
    proj.ready(gid1)
    assert svc.finalize(gid1, "merge", target_branch="develop")["result"]["status"] == "merged"
    proj.origin_commit("main", "main_only.txt", "m\n")

    updated = svc.update_from_base(gid2)
    assert updated["result"]["status"] == "updated"
    assert (proj.wt(gid2) / "main_only.txt").exists()
    assert not (proj.wt(gid2) / "only_develop.txt").exists()
    state = svc.get_finalize_state(gid2)["state"]
    assert state["base_branch"] == "main"
    status = svc.project_git_status(proj.pid)["status"]
    assert status["base_branch"] == "main"
    assert proj.base_head_branch() == "main"
    assert svc.base_checkout_dirty_status(proj.pid)["dirty"] is False
    changes = svc.read_group_changes(proj.pid, gid2)
    paths = json.dumps(changes)
    assert "only_develop.txt" not in paths

