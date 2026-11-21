"""Legacy work-base recovery through the common scope floor."""
from __future__ import annotations

import json

import pytest

from tests.regression.group_git.test_group_work_base_scope_0665 import (
    _forget_floor, _scope, _state, patch_store, proj, storage_dir,
)


def test_first_scope_recovers_and_preserves_dirty_worktree(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(61, work_base="v0.2")
    fork = _state(gid)["work_base_sha"]
    proj.commit(gid, "committed.txt", "committed\n")
    _forget_floor(gid)
    (proj.wt(gid) / "shared.txt").write_text("staged\n", encoding="utf-8")
    proj.wt_git(gid, "add", "shared.txt")
    (proj.wt(gid) / "shared.txt").write_text("unstaged\n", encoding="utf-8")
    (proj.wt(gid) / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    before = (proj.wt_git(gid, "rev-parse", "HEAD"),
              proj.wt_git(gid, "branch", "--show-current"),
              proj.wt_git(gid, "status", "--porcelain=v1"),
              proj.wt_git(gid, "diff", "--cached"),
              proj.wt_git(gid, "diff"))
    initial_logs = len(db_git.list_work_base_log(gid))

    first = svc.collect_scope_changes(proj.pid, gid)
    assert first["available"] is True
    assert first["scope_base_sha"] == fork
    assert set(first["paths"]) == {"committed.txt", "shared.txt", "untracked.txt"}
    state = _state(gid)
    assert state["work_base_state"] == "verified" and state["work_base_sha"] == fork
    assert len(db_git.list_work_base_log(gid)) == initial_logs + 1
    assert db_git.list_work_base_log(gid)[-1]["kind"] == "backfill"

    second = svc.collect_scope_changes(proj.pid, gid)
    assert second["scope_base_sha"] == fork and second["paths"] == first["paths"]
    assert len(db_git.list_work_base_log(gid)) == initial_logs + 1
    after = (proj.wt_git(gid, "rev-parse", "HEAD"),
             proj.wt_git(gid, "branch", "--show-current"),
             proj.wt_git(gid, "status", "--porcelain=v1"),
             proj.wt_git(gid, "diff", "--cached"),
             proj.wt_git(gid, "diff"))
    assert after == before


def test_existing_verified_and_confirmed_records_are_unchanged(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    for seq, status in ((62, "verified"), (63, "confirmed")):
        gid = proj.group(seq, work_base="v0.2")
        if status == "confirmed":
            db_git.set_work_base_record(
                gid, work_base_sha=_state(gid)["work_base_sha"],
                work_base_sync_sha=None, state="confirmed",
                evidence={"origin": "manual_confirm", "basis": "test"},
            )
        before = dict(_state(gid))
        logs = list(db_git.list_work_base_log(gid))
        assert _scope(proj.pid, gid) == set()
        assert _state(gid) == before
        assert db_git.list_work_base_log(gid) == logs
