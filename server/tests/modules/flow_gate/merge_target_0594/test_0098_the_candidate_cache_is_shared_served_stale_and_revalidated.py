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
def test_0098_the_candidate_cache_is_shared_served_stale_and_revalidated(proj, check_budget, monkeypatch):
    import threading
    import time as _time

    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(8)
    computed = []
    real = mt._compute_snapshot

    def counted(basis, *args):
        computed.append(basis.group_id)
        _time.sleep(0.3)
        return real(basis, *args)

    monkeypatch.setattr(mt, "_compute_snapshot", counted)
    first = _check_view(proj, gid, refresh=False)
    assert _check_view(proj, gid, refresh=False) == first and len(computed) == 1     # fresh: the cache
    # A server change marks it stale; it is still served and recomputed behind it
    mt.invalidate_check_targets(proj.pid, gid)
    assert _check_view(proj, gid, refresh=False) == first
    assert mt.settle_check_targets(60) and len(computed) == 2
    assert _check_view(proj, gid, refresh=False) == first and len(computed) == 2     # the recomputed list
    _check_view(proj, gid, refresh=True)                                              # the refresh option skips it
    assert len(computed) == 3
    monkeypatch.setattr(mt, "CHECK_TARGET_CACHE_SEC", 0.0)                            # older than the cache time
    _check_view(proj, gid, refresh=False)
    assert len(computed) == 4
    monkeypatch.setattr(mt, "CHECK_TARGET_CACHE_SEC", 1800.0)
    # concurrent misses of one key share one computation (the basis is read once up front: this harness shares a
    # single SQLite connection, which the real stores never do)
    basis = mt.check_target_basis(proj.pid, gid)
    monkeypatch.setattr(mt, "check_target_basis", lambda *_a, **_k: basis)
    mt.reset_check_targets()
    results = []
    threads = [threading.Thread(target=lambda: results.append(_check_view(proj, gid, refresh=False)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(computed) == 5 and len(results) == 4
    assert all(r["merge_target_options"] == first["merge_target_options"] for r in results), json.dumps(
        [r["merge_target_state"] for r in results])
    # a failed computation is never cached: the next read tries again
    monkeypatch.setattr(mt, "_compute_snapshot",
                        lambda basis, *_a: computed.append("x") or mt._failed_snapshot(
                            mt.CHECK_STATUS_FAILED, mt.CHECK_OPTIONS_UNAVAILABLE, "probe_failed",
                            default_branch=basis.work_base_ref, basis=None))
    mt.reset_check_targets()
    _check_view(proj, gid, refresh=False)
    _check_view(proj, gid, refresh=False)
    assert computed.count("x") == 2

