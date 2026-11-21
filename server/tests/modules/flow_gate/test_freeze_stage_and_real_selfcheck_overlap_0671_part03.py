"""Independently selectable scenarios; shared fixtures remain in test_freeze_stage_and_real_selfcheck_overlap_0671.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_freeze_stage_and_real_selfcheck_overlap_0671 import (
    PROJECT,
    PROJECT_NAME,
    Path,
    SLEEP_SEC,
    USER,
    _base,
    _branch,
    _drive_jobs,
    _git,
    _job,
    _origin_head,
    _rev,
    _review_status,
    _seed_final_approval_group,
    _start_check,
    _wait_check,
    _wait_running,
    _worktree,
    api,
    approve_http,
    db_path,
    needs_git,
    origin_repo,
    patch_store,
    project,
    pytest,
    selfcheck_stubs,
    subprocess,
    threading,
    time,
)

@needs_git
class TestRealSelfCheckOverlap:

    def test_same_group_approval_does_not_wait_for_a_running_selfcheck(
        self, origin_repo, selfcheck_stubs,
    ):
        """0053 1-B: the run executes in its snapshot and holds no G, so the same Group's real
        HTTP approve proceeds at once (it used to wait the run out on G), removes the Group's
        worktree, and the self-check still ends with its own result."""
        from modules.flow_gate.db import git_concurrency as gc
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0904"
        _root_id, ac_id, tr_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        wt = _worktree(group)
        (wt / "g904.py").write_text("g904 = 1\n", encoding="utf-8")
        _git(["add", "-A"], cwd=wt)
        _git(["commit", "-m", "work 0904"], cwd=wt)

        run = _start_check(selfcheck_stubs, tr_id, _worktree(group), 25.0)
        _wait_running(selfcheck_stubs, tr_id, run["self_check_run_id"])
        base = _base()
        origin_before = _origin_head(origin_repo)
        client = api()
        t0 = time.monotonic()
        status, payload = approve_http(client, ac_id)
        took = time.monotonic() - t0

        assert status == 200, payload
        assert payload["git"]["result"]["pushed"] is True
        assert payload.get("job", {}).get("blocked_operation") != "selfcheck"
        assert took < 18.0         # well short of the self-check's 25s sleep: no contention
        assert _origin_head(origin_repo) == _rev(base, "HEAD") != origin_before
        assert selfcheck_stubs.read(tr_id, run["self_check_run_id"])["status"] in ("pending", "running")

        row = _wait_check(selfcheck_stubs, tr_id, run["self_check_run_id"], limit=40)
        assert (row["status"], row["exit_code"]) == ("completed", 0)      # its snapshot was not touched
        assert "selfcheck done" in row["stdout_tail"]
        assert gc.list_locks_in_scope(PROJECT) == []
        _drive_jobs(group)
