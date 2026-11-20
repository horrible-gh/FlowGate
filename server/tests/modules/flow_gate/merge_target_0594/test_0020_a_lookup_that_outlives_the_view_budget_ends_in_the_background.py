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
def test_0020_a_lookup_that_outlives_the_view_budget_ends_in_the_background(proj, monkeypatch):
    """flowgate.v02test.0020: a slow lookup used to be dropped at the 3 s budget and computed again from nothing by
    every retry and refresh, so on a slow host the list never came. Now the view still answers ``timeout`` in its own
    budget, but the computation (real Git) ends in the background; the next plain read gets the list without
    computing again, and a save never waited for any of it."""
    import time as _time

    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(65)
    proj.local_branch("develop")
    mt.reset_check_targets()
    # the basis is read once up front (this harness shares one SQLite connection; the real stores never do)
    basis = mt.check_target_basis(proj.pid, gid, require_slot=False)
    monkeypatch.setattr(mt, "check_target_basis", lambda *_a, **_k: basis)
    real_inspect, real_compute = mt.inspect_workspace, mt._compute_snapshot
    monkeypatch.setattr(mt, "inspect_workspace", lambda pid, name: _time.sleep(0.5) or real_inspect(pid, name))
    computed = []
    monkeypatch.setattr(mt, "_compute_snapshot", lambda *a: computed.append(1) or real_compute(*a))
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.3)
    started = _time.monotonic()
    first = _check_view(proj, gid, refresh=False)
    assert _time.monotonic() - started < 0.3 + 0.5                 # the view answered inside its own budget
    assert (first["merge_target_state"]["status"], first["merge_target_options"]) == ("timeout", [])
    assert mt.settle_check_targets(30)
    assert len(computed) == 1
    snap = mt._check_cache[(proj.pid, gid)]
    assert snap["status"] == "ok" and "stale" not in snap         # replaced the negative snapshot
    monkeypatch.setattr(mt, "inspect_workspace", real_inspect)
    picked = _check_view(proj, gid, refresh=False)
    assert picked["merge_target_state"]["status"] == "ok"
    assert picked["merge_target_options"] == ["develop", "main"]
    assert picked["merge_target_state"]["current"]["checked"] is True
    assert len(computed) == 1                                      # read from the finished computation
    assert mt._check_cache[(proj.pid, gid)] is snap

