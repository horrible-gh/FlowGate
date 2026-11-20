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
def test_0098_a_base_status_that_cannot_be_read_is_no_clean_base(proj, check_budget, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import command
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(61)
    proj.local_branch("develop")
    real_run, real_subprocess_run = svc._run_git, command.subprocess.run

    def status_fails(args, **kw):                      # e.g. an unreadable index: exit 128
        if args[:1] == ["status"]:
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: index file corrupt")
        return real_run(args, **kw)

    def status_outlived(argv, **kw):                   # the call's own timeout
        if argv[1:2] == ["status"]:
            raise command.subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return real_subprocess_run(argv, **kw)

    for owner, name, fake in ((svc, "_run_git", status_fails), (command.subprocess, "run", status_outlived)):
        with monkeypatch.context() as m:
            m.setattr(owner, name, fake)
            failed = _refused(proj, gid, "main")
            assert (failed.code, failed.details["reason"]) == ("gate_git_unavailable", "probe_failed")
            assert _judge(proj, gid, "develop")["is_project_base"] is False      # a non-base target never reads it
            # flowgate.v02test.0020 rev2: the list does not read the base checkout's status
            view = _check_view(proj, gid)
            assert view["merge_target_options"] == ["develop", "main"]
            assert view["merge_target_state"]["status"] == "ok"
    assert _check_view(proj, gid)["merge_target_options"] == ["develop", "main"]

