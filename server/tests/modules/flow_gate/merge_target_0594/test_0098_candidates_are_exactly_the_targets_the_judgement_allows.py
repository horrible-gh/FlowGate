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
def test_0098_candidates_are_exactly_the_targets_the_judgement_allows(proj, check_budget, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import merge_target as mt

    gid, other = proj.group(1), proj.group(2)
    proj.local_branch("develop")
    proj.local_branch("release")
    own_branch, other_branch = db_git.get_state(gid)["branch"], db_git.get_state(other)["branch"]
    view = _check_view(proj, gid)
    assert view["merge_target_options"] == ["develop", "main", "release"]          # never a slot, never a remote
    assert view["default_target_branch"] == "main"
    assert view["merge_target_state"]["status"] == "ok"
    for name in view["merge_target_options"]:                                      # a candidate is a saveable target
        judged = _judge(proj, gid, name)
        assert judged["target_branch"] == name and judged["target_sha"] == _git(
            ["rev-parse", f"refs/heads/{name}"], cwd=proj.base).strip()
        assert (judged["is_project_base"], judged["unmerge_supported"]) == (name == "main", name == "main")
    assert _refused(proj, gid, own_branch).code == "merge_target_same_branch"
    slot = _refused(proj, gid, other_branch)
    assert (slot.code, slot.details["connected_group_id"]) == ("merge_target_internal_slot", other)
    for name, code in (("nope", "merge_target_not_found"), ("a..b", "merge_target_invalid"),
                       ("-x", "merge_target_invalid"), (" main", "merge_target_invalid")):
        assert _refused(proj, gid, name).code == code
    _git(["push", "origin", "main:remote-only"], cwd=proj.seed)
    _git(["fetch", "origin"], cwd=proj.base)
    assert _refused(proj, gid, "remote-only").code == "merge_target_remote_only"
    # the ledger decides a slot: a row left by an unregistered worktree does not exclude its branch by itself
    db_git.unregister_worktree(other)
    assert db_git.get_state_by_branch(proj.pid, other_branch) is not None
    assert _judge(proj, gid, other_branch)["target_branch"] == other_branch
    assert other_branch in _check_view(proj, gid)["merge_target_options"]
    # a ledger that cannot be read is no "allowed": the judgement and the whole list fail
    monkeypatch.setattr(db_git, "list_states_of_project",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db down")))
    failed = _refused(proj, gid, "develop")
    assert (failed.code, failed.details["reason"]) == ("gate_git_unavailable", "ledger_unavailable")
    broken = _check_view(proj, gid)
    assert broken["merge_target_options"] == []
    assert (broken["merge_target_state"]["status"], broken["merge_target_state"]["code"]) == (
        "failed", mt.CHECK_OPTIONS_UNAVAILABLE)

