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


    def test_same_group_approval_is_not_deferred_by_a_selfcheck_under_the_real_budget(
        self, origin_repo, selfcheck_stubs,
    ):
        """The real (unmodified) freeze_interactive budget (2s) is shorter than the
        self-check's sleep: the approval used to give up into freeze_wait behind the run's G
        (``blocked_operation="selfcheck"``). Now it is approved in the same request."""
        from modules.flow_gate.db import git_concurrency as gc
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0905"
        _root_id, ac_id, tr_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        wt = _worktree(group)
        (wt / "g905.py").write_text("g905 = 1\n", encoding="utf-8")
        _git(["add", "-A"], cwd=wt)
        _git(["commit", "-m", "work 0905"], cwd=wt)

        run = _start_check(selfcheck_stubs, tr_id, _worktree(group), SLEEP_SEC)
        _wait_running(selfcheck_stubs, tr_id, run["self_check_run_id"])
        base = _base()
        origin_before = _origin_head(origin_repo)
        client = api()
        status, payload = approve_http(client, ac_id)

        assert status == 200, payload
        assert payload["git"]["result"]["pushed"] is True
        assert payload.get("approval", {}).get("stage") != "queued"
        assert _review_status(ac_id) == "approved"
        assert _origin_head(origin_repo) == _rev(base, "HEAD") != origin_before

        row = _wait_check(selfcheck_stubs, tr_id, run["self_check_run_id"])
        assert (row["status"], row["exit_code"]) == ("completed", 0)
        assert gc.list_locks_in_scope(PROJECT) == []
        _drive_jobs(group)
@needs_git
class TestRealBlockedTransition:
    def test_two_real_approvals_contend_for_b_and_r_and_the_loser_goes_blocked(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.db import git_concurrency as gc
        from modules.flow_gate.services import git_service as svc

        g1 = f"{PROJECT}.default.0906"
        g2 = f"{PROJECT}.default.0907"
        _root1, ac1, _tr1 = _seed_final_approval_group(g1)
        _root2, ac2, _tr2 = _seed_final_approval_group(g2)
        for g, ac in ((g1, ac1), (g2, ac2)):
            assert svc.ensure_worktree(PROJECT, "default", g) == "ok"
            wt = _worktree(g)
            # distinct files: the point of this test is lock contention, not a real
            # merge conflict, so the two groups must not touch the same path.
            (wt / f"work_{g.replace('.', '_')}.py").write_text(f"g = {g!r}\n", encoding="utf-8")
            _git(["add", "-A"], cwd=wt)
            _git(["commit", "-m", f"work {g}"], cwd=wt)

        # Two environment timing knobs only (module docstring) -- no Git or lock logic
        # is replaced: 1) the real `git push` subprocess still runs with its real exit
        # code, only delayed a little so whichever thread the OS scheduler runs first
        # really holds B+R across the other thread's admission attempt; 2) the real
        # `interactive` wait budgets read a shorter real env value, so the loser's real
        # wait really exceeds budget instead of (also realistically) just waiting it
        # out -- it is the "blocked" outcome specifically being demonstrated here, not
        # "wait succeeds" (already shown for G in TestRealSelfCheckOverlap).
        monkeypatch.setenv("FLOWGATE_INTERACTIVE_WAIT_SEC", "0.3")
        monkeypatch.setenv("FLOWGATE_R_INTERACTIVE_WAIT_SEC", "0.3")
        original_run_git = svc._run_git

        def slow_push(args, **kwargs):
            if list(args[:2]) == ["push", "origin"]:
                time.sleep(1.5)
            return original_run_git(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", slow_push)

        base = _base()
        origin_before = _origin_head(origin_repo)
        results = {}

        def approve(key, doc_id):
            results[key] = approve_http(api(), doc_id)

        t1 = threading.Thread(target=approve, args=("g1", ac1))
        t2 = threading.Thread(target=approve, args=("g2", ac2))
        t1.start()
        t2.start()
        t1.join(30)
        t2.join(30)
        monkeypatch.undo()

        assert not t1.is_alive() and not t2.is_alive()
        status1, payload1 = results["g1"]
        status2, payload2 = results["g2"]
        assert status1 == 200, payload1
        assert status2 == 200, payload2

        # Exactly one of the two really won B+R outright (real merge, real push) and
        # the other really contended for it and really lost: a real "blocked" job
        # status (not retry_wait, not a generic queued placeholder), observed straight
        # out of the real HTTP response -- whichever group that turns out to be.
        outcomes = {"g1": (ac1, g1, payload1), "g2": (ac2, g2, payload2)}
        winners = [k for k, (_, _, p) in outcomes.items() if p["approval"]["approved"] is True]
        losers = [k for k, (_, _, p) in outcomes.items() if p["approval"]["approved"] is False]
        assert len(winners) == 1 and len(losers) == 1, (payload1, payload2)
        win_ac, _win_g, win_payload = outcomes[winners[0]]
        lose_ac, lose_g, lose_payload = outcomes[losers[0]]

        assert win_payload["git"]["result"]["pushed"] is True
        assert lose_payload["approval"]["stage"] == "queued" and lose_payload["approval"]["deferred"] is True
        assert lose_payload["job"]["status"] == "blocked"
        assert lose_payload["job"]["blocked_domain"] in ("B", "R")
        assert _review_status(lose_ac) == "pending_review"

        # the real Job Runner's later claim finishes the blocked approval; both real
        # merges really land on the real origin.
        assert _drive_jobs(lose_g) >= 1
        assert _job(lose_g)["status"] == "succeeded"
        assert _review_status(lose_ac) == "approved"
        head = _rev(base, "HEAD")
        assert _origin_head(origin_repo) == head != origin_before
        # both groups' work really landed on the real base/origin history
        assert int(_git(["rev-list", "--count", f"{origin_before}..{head}"], cwd=base)) >= 2
        assert gc.list_locks_in_scope(PROJECT) == []

        _drive_jobs(g1)
        _drive_jobs(g2)
