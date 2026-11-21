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
def test_B_non_base_clean_finalize_uses_managed_workspace(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    before = proj.base_sha()
    (proj.wt(gid) / "feature.txt").write_text("feature\n", encoding="utf-8")
    proj.ready(gid)

    seen = {}
    real_prepare = mt.prepare_workspace

    def spy(ctx, **kwargs):
        real_prepare(ctx, **kwargs)
        seen["root"] = ctx.root
        seen["exists"] = ctx.root.is_dir()
        seen["base_branch_during"] = proj.base_head_branch()

    monkeypatch.setattr(mt, "prepare_workspace", spy)
    out = svc.finalize(gid, "merge", target_branch="develop")
    assert out["result"]["status"] == "merged"
    assert out["result"]["target_branch"] == "develop"
    assert seen["root"] == proj.workspace("develop") / "tree" and seen["exists"]
    assert seen["base_branch_during"] == "main"
    # the shared base checkout was never switched nor moved
    assert proj.base_head_branch() == "main"
    assert proj.base_sha() == before
    assert not (proj.base / "feature.txt").exists()
    assert "feature.txt" in _git(["ls-tree", "-r", "--name-only", "develop"], cwd=proj.base).split()
    assert "feature.txt" not in _git(["ls-tree", "-r", "--name-only", "main"], cwd=proj.base).split()
    assert "feature.txt" in proj.origin_files("develop")
    assert "feature.txt" not in proj.origin_files("main")
    state = db_git.get_state(gid)
    ctx = _ctx(state["merge_id"])
    assert ctx["attempt_state"] == "completed"
    assert ctx["merge_target"]["branch"] == "develop"
    assert ctx["merge_target"]["is_project_base"] is False
    assert not proj.workspace("develop").exists()             # own workspace released
    # slot teardown: the work branch is contained in develop (not in base) and is
    # still removed, because the completed attempt proves where it landed
    assert _git(["show-ref", "--verify", f"refs/heads/{gid.replace('.', '_')}"],
                cwd=proj.base, check=False) == ""
    assert "_default_0001" not in _git(["branch", "--list"], cwd=proj.base)
    # restart: the real target is recovered from the ledger → attempt record
    svc.startup_recovery()
    done = mt.completed_target_of_state(db_git.get_state(gid))
    assert done.target_branch == "develop" and not done.is_project_base
    assert svc.get_finalize_state(gid)["state"]["finalize_target"]["target_branch"] == "develop"

    # Connected edge: the result just pushed by group finalize is then
    # promoted through the real ordinary-branch merge service into main.
    promoted = svc.merge_branches(proj.pid, "develop", "main")
    assert promoted["pushed"] is True and promoted["workspace_cleaned"] is True
    assert "feature.txt" in proj.origin_files("main")
    assert not proj.workspace("main").exists()
    assert proj.base_head_branch() == "main"

