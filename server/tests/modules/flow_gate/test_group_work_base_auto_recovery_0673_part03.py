"""Independently selectable scenarios; shared fixtures remain in test_group_work_base_auto_recovery_0673.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_group_work_base_auto_recovery_0673 import (
    _forget_floor,
    _scope,
    _state,
    json,
    patch_store,
    proj,
    pytest,
    storage_dir,
)

def test_stale_state_and_repeated_recovery_do_not_duplicate_record(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(68, work_base="v0.2")
    _forget_floor(gid)
    stale = dict(_state(gid))
    floor = svc.resolve_scope_floor(proj.pid, gid, proj.wt(gid), state=stale)
    logs = len(db_git.list_work_base_log(gid))
    again = svc.resolve_scope_floor(proj.pid, gid, proj.wt(gid), state=stale)
    assert again == floor
    assert len(db_git.list_work_base_log(gid)) == logs
def test_parallel_scope_requests_write_one_backfill_record(proj, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import scope_base

    gid = proj.group(69, work_base="v0.2")
    _forget_floor(gid)
    stale = dict(_state(gid))
    before_logs = len(db_git.list_work_base_log(gid))
    barrier = Barrier(2)
    original = scope_base.classify_group

    def classify_together(*args, **kwargs):
        result = original(*args, **kwargs)
        barrier.wait(timeout=15)
        return result

    monkeypatch.setattr(scope_base, "classify_group", classify_together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(svc.resolve_scope_floor, proj.pid, gid, proj.wt(gid),
                               state=stale) for _ in range(2)]
        floors = [future.result(timeout=30) for future in futures]
    assert floors[0] == floors[1]
    assert _state(gid)["work_base_state"] == "verified"
    assert len(db_git.list_work_base_log(gid)) == before_logs + 1
def test_legacy_null_ref_is_pinned_with_recovered_floor(proj):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(70)
    fork = _state(gid)["work_base_sha"]
    _forget_floor(gid)
    assert db_groups.get_by_id(gid)["work_base_ref"] is None
    floor = svc.resolve_scope_floor(proj.pid, gid, proj.wt(gid))
    assert floor["floor_sha"] == fork
    assert db_groups.get_by_id(gid)["work_base_ref"] == "main"
    assert _state(gid)["work_base_state"] == "verified"
