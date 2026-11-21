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
def test_0098_one_budget_covers_the_basis_the_candidates_and_the_stored_target(proj, monkeypatch):
    import time as _time

    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(64)
    proj.local_branch("develop")
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 60.0)
    mt.reset_check_targets()
    assert _check_view(proj, gid, refresh=False)["merge_target_options"] == ["develop", "main"]      # cached
    real_basis, real_verdict, real_compute = mt.check_target_basis, mt._verdict, mt._compute_snapshot
    with monkeypatch.context() as m:
        # each step alone stays inside the 1 s budget; the basis and the stored target's judgement together do not
        m.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 1.0)
        # The background computation does not finish either (its own budget runs out)
        m.setattr(mt, "CHECK_TARGET_COMPUTE_SEC", 0.3)
        m.setattr(mt, "check_target_basis", lambda *a, **k: _time.sleep(0.6) or real_basis(*a, **k))
        m.setattr(mt, "_verdict", lambda *a, **k: _time.sleep(0.6) or real_verdict(*a, **k))
        view = _check_view(proj, gid, "gone", refresh=False)
        assert mt.settle_check_targets(10)
    state = view["merge_target_state"]
    assert view["merge_target_options"] == []
    assert (state["status"], state["code"]) == ("timeout", mt.CHECK_OPTIONS_TIMEOUT)
    assert (state["current"]["branch"], state["current"]["checked"], state["default"]["checked"]) == (
        "gone", False, False)
    # 0148: an unfinished added judgement also replaces the candidate view with a short timeout cache.
    again = _check_view(proj, gid, "gone", refresh=False)
    assert again["merge_target_options"] == []
    assert (again["merge_target_state"]["status"], again["merge_target_state"]["current"]["checked"]) == (
        "timeout", False)
    again = _check_view(proj, gid, "gone", refresh=True)
    assert again["merge_target_options"] == ["develop", "main"]
    assert (again["merge_target_state"]["current"]["checked"], again["merge_target_state"]["current"]["error"]) == (
        True, "merge_target_not_found")
    # A basis miss spends the same budget and is cached too; hits do no new computation, refresh does.
    computed = []
    monkeypatch.setattr(mt, "_compute_snapshot", lambda *a: computed.append(1) or real_compute(*a))
    mt.reset_check_targets()
    with monkeypatch.context() as m:
        m.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.5)
        m.setattr(mt, "CHECK_TARGET_COMPUTE_SEC", 0.0)     # nothing finishes behind it
        m.setattr(mt, "check_target_basis", lambda *a, **k: _time.sleep(0.6) or real_basis(*a, **k))
        missed = _check_view(proj, gid, "gone", refresh=False)
        assert mt.settle_check_targets(10)
    assert missed["merge_target_options"] == []
    assert (missed["merge_target_state"]["status"], missed["merge_target_state"]["current"]["checked"]) == (
        "timeout", False)
    timeout_calls = len(computed)
    cached = _check_view(proj, gid, refresh=False)
    assert cached["merge_target_options"] == []
    assert cached["merge_target_state"]["status"] == "timeout"
    assert len(computed) == timeout_calls
    assert _check_view(proj, gid, refresh=True)["merge_target_options"] == ["develop", "main"]
    assert len(computed) == timeout_calls + 1
    mt.reset_check_targets()

