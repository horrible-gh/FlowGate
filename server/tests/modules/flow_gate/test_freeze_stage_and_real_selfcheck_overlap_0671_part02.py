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
    def test_different_group_is_not_blocked_by_a_running_selfcheck(
        self, origin_repo, selfcheck_stubs,
    ):
        from modules.flow_gate.db import git_concurrency as gc
        from modules.flow_gate.services import git_service as svc

        g_sc = f"{PROJECT}.default.0902"
        g_other = f"{PROJECT}.default.0903"
        _root_sc, _ac_sc, tr_sc = _seed_final_approval_group(g_sc)
        _root_other, ac_other, _tr_other = _seed_final_approval_group(g_other)
        for g in (g_sc, g_other):
            assert svc.ensure_worktree(PROJECT, "default", g) == "ok"
        (_worktree(g_other) / "f.py").write_text("f = 1\n", encoding="utf-8")
        _git(["add", "-A"], cwd=_worktree(g_other))
        _git(["commit", "-m", "work"], cwd=_worktree(g_other))

        # Long enough to safely outlast this sandbox's own real (several-second) git
        # subprocess latency for one approval -- observed empirically (~7s for a single
        # real merge+push here) -- so "still running afterward" is a real, not a lucky,
        # observation. The self-check body is a bare sleep; a longer one costs wall time,
        # not CPU, and does not change what is being proven.
        run = _start_check(selfcheck_stubs, tr_sc, _worktree(g_sc), 25.0)
        _wait_running(selfcheck_stubs, tr_sc, run["self_check_run_id"])
        assert not any(r["domain"] == "G" and r["holder_kind"] == "selfcheck"
                       for r in gc.list_locks_in_scope(PROJECT))    # 0053 1-B: no G

        base = _base()
        origin_before = _origin_head(origin_repo)
        client = api()
        t0 = time.monotonic()
        status, payload = approve_http(client, ac_other)
        took = time.monotonic() - t0

        assert status == 200, payload
        assert payload["git"]["result"]["pushed"] is True
        assert took < 18.0         # well short of the self-check's 25s sleep: no contention
        assert _origin_head(origin_repo) == _rev(base, "HEAD") != origin_before

        row = selfcheck_stubs.read(tr_sc, run["self_check_run_id"])
        assert row["status"] in ("pending", "running")       # still running, untouched
        _wait_check(selfcheck_stubs, tr_sc, run["self_check_run_id"])
        assert gc.list_locks_in_scope(PROJECT) == []
        _drive_jobs(g_other)
