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
def test_0098_without_a_verified_floor_no_target_beyond_the_work_base_is_allowed(proj, check_budget, monkeypatch):
    from modules.flow_gate.db import tr_commit_ledger as db_tr_ledger
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = _feature_group(proj, 59)
    a = _git(["rev-parse", "main"], cwd=proj.base).strip()
    b = _git(["rev-parse", "feature/v0.2.0"], cwd=proj.base).strip()
    # the work base rewound to main's commit A (local and origin): the group's floor B left its history
    _git(["branch", "-f", "feature/v0.2.0", a], cwd=proj.base)
    _git(["update-ref", "refs/remotes/origin/feature/v0.2.0", a], cwd=proj.base)
    assert mt.preview_target(gid, "main")["incoming_work_base_commits"] == 0      # the preview's tip fallback
    assert _refused(proj, gid, "main").code == "merge_target_preview_unavailable"
    view = _check_view(proj, gid, "main")
    # flowgate.v02test.0020 rev2: the list reads no floor; main is listed and refused when saved / confirmed / run
    assert view["merge_target_options"] == ["feature/v0.2.0", "main"]
    assert view["merge_target_state"]["current"]["error"] is None
    assert _refused(proj, gid, "main", scope_floor_sha=b).code == "merge_target_incoming_work_base"
    _git(["branch", "-f", "feature/v0.2.0", b], cwd=proj.base)
    _git(["update-ref", "refs/remotes/origin/feature/v0.2.0", b], cwd=proj.base)
    _git(["merge", "--ff-only", "feature/v0.2.0"], cwd=proj.base)
    assert _judge(proj, gid, "main")["incoming_work_base_commits"] == 0
    # the ledger the floor check reads, or Git answering it, failing: the judgement's basis fails; the list, which
    # reads neither, stays as it is
    real_run = svc._run_git
    for patch_failure, reason in (
        (lambda m: m.setattr(db_tr_ledger, "group_commit_evidence",
                             lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db down"))), "ledger_unavailable"),
        (lambda m: m.setattr(svc, "_run_git", lambda args, **kw: (
            subprocess.CompletedProcess(["git", *args], 128, "", "fatal: broken")
            if args[:2] == ["merge-base", "--is-ancestor"] else real_run(args, **kw))), "probe_failed"),
    ):
        with monkeypatch.context() as m:
            patch_failure(m)
            failed = _refused(proj, gid, "main")
            assert (failed.code, failed.details["reason"]) == ("gate_git_unavailable", reason)
            listed = _check_view(proj, gid)
            assert listed["merge_target_state"]["status"] == "ok"
            assert listed["merge_target_options"] == ["feature/v0.2.0", "main"]
            assert _judge(proj, gid, "feature/v0.2.0")["is_work_base"] is True     # the work base reads no floor
    assert _check_view(proj, gid)["merge_target_options"] == ["feature/v0.2.0", "main"]

