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
def test_0098_main_is_a_target_only_when_it_holds_the_work_base_history(proj, check_budget, monkeypatch):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services.git import merge_target as mt

    gid = _feature_group(proj, 3)
    view = _check_view(proj, gid)
    # flowgate.v02test.0020 rev2: the list no longer reads the work base history a target lacks (the scope floor);
    # main lacks the v0.2 commit, is listed, and the single judgement of a save / confirm / run refuses it
    assert view["merge_target_options"] == ["feature/v0.2.0", "main"]
    assert view["default_target_branch"] == "feature/v0.2.0"
    inherited = view["merge_target_state"]["current"]
    assert (inherited["branch"], inherited["inherited"], inherited["unmerge_supported"], inherited["warnings"]) == (
        "feature/v0.2.0", True, False, ["unmerge_unsupported_for_non_base_target"])
    refused = _refused(proj, gid, "main")
    assert (refused.code, refused.details["incoming_work_base_commits"]) == ("merge_target_incoming_work_base", 1)
    main_state = _check_view(proj, gid, "main")["merge_target_state"]["current"]
    assert (main_state["branch"], main_state["inherited"], main_state["error"]) == ("main", False, None)
    work_base = _judge(proj, gid, "feature/v0.2.0")
    assert (work_base["is_work_base"], work_base["is_project_base"], work_base["unmerge_supported"],
            work_base["incoming_work_base_commits"]) == (True, False, False, None)
    # an incoming count that cannot be told refuses, never allows
    real_preview = mt.preview_target
    monkeypatch.setattr(mt, "preview_target", lambda *a, **k: {**real_preview(*a, **k),
                                                                "incoming_work_base_commits": None})
    assert _refused(proj, gid, "main").code == "merge_target_preview_unavailable"
    monkeypatch.setattr(mt, "preview_target", real_preview)
    # the list never asks Git which tips hold the floor (nor for the floor): a broken answer leaves it as it is
    from modules.flow_gate.services import git_service as svc
    real_run = svc._run_git
    monkeypatch.setattr(svc, "_run_git", lambda args, **kw: (
        subprocess.CompletedProcess(["git", *args], 128, "", "fatal: broken")
        if args[:2] in (["for-each-ref", "--contains"], ["merge-base", "--is-ancestor"]) else real_run(args, **kw)))
    mt.reset_check_targets()
    listed = _check_view(proj, gid, refresh=False)
    assert listed["merge_target_state"]["status"] == "ok"
    assert listed["merge_target_options"] == ["feature/v0.2.0", "main"]
    monkeypatch.setattr(svc, "_run_git", real_run)
    # main holds the whole work base history (incoming 0): a candidate and an allowed target
    _git(["merge", "--ff-only", "feature/v0.2.0"], cwd=proj.base)
    judged = _judge(proj, gid, "main")
    assert (judged["incoming_work_base_commits"], judged["is_project_base"], judged["unmerge_supported"]) == (
        0, True, True)
    assert judged["scope_floor_sha"] == _git(["rev-parse", "feature/v0.2.0"], cwd=proj.base).strip()
    assert judged["warnings"] == ["target_differs_from_work_base"]
    assert _check_view(proj, gid)["merge_target_options"] == ["feature/v0.2.0", "main"]
    # a confirm's recorded work base / floor are judged instead of the group's current ones
    first = _git(["rev-list", "--max-parents=0", "main"], cwd=proj.base).strip()
    _git(["branch", "old", first], cwd=proj.base)
    assert _refused(proj, gid, "old").code == "merge_target_incoming_work_base"
    assert _judge(proj, gid, "old", scope_floor_sha=first)["incoming_work_base_commits"] == 0
    assert db_groups.get_by_id(gid)["work_base_ref"] == "feature/v0.2.0"           # never changed

