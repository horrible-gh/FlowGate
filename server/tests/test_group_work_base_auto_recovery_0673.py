"""Legacy work-base recovery through the common scope floor."""
from __future__ import annotations

import json

import pytest

from test_group_work_base_scope_0665 import (
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


def test_corrupt_ledger_remains_explicit_error_without_source_change(proj, monkeypatch):
    from modules.flow_gate.db import tr_commit_ledger
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(64, work_base="v0.2")
    _forget_floor(gid)
    before = (proj.wt_git(gid, "rev-parse", "HEAD"),
              proj.wt_git(gid, "status", "--porcelain=v1"))
    monkeypatch.setattr(tr_commit_ledger, "group_commit_evidence",
                        lambda _gid: (_ for _ in ()).throw(RuntimeError("ledger unavailable")))
    out = svc.collect_scope_changes(proj.pid, gid)
    assert out["available"] is False
    assert out["work_base_error"]["code"] == "group_work_base_unverified"
    assert out["work_base_error"]["details"]["reason"] == "ledger_unavailable"
    assert _state(gid)["work_base_state"] is None
    assert before == (proj.wt_git(gid, "rev-parse", "HEAD"),
                      proj.wt_git(gid, "status", "--porcelain=v1"))


def test_tr_review_and_final_recheck_continue_after_legacy_recovery(proj, monkeypatch):
    import io
    import zipfile
    from modules.flow_gate.services import review_package_service as rps
    from modules.flow_gate.services import tr_scope_service as trs

    tr_gid = proj.group(65, work_base="v0.2")
    proj.commit(tr_gid, "tr.txt", "tr\n")
    _forget_floor(tr_gid)
    monkeypatch.setattr(trs, "resolve_stage", lambda _pid: trs.STAGE_ENFORCE)
    verdict = trs.evaluate(proj.pid, tr_gid, "## 변경 파일\n\n- tr.txt\n")
    assert verdict["verdict"] == trs.VERDICT_PASS
    assert verdict["detected"] == ["tr.txt"]

    review_gid = proj.group(66, work_base="v0.2")
    proj.commit(review_gid, "review.txt", "review\n")
    _forget_floor(review_gid)
    monkeypatch.setattr(rps.tr_scope_service, "group_declared_paths",
                        lambda group_id, exclude_doc_id=None: [])
    package = rps.build_review_package(proj.pid, review_gid)
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
    assert metadata["changed_files"] == ["review.txt"]

    final_gid = proj.group(67, work_base="v0.2")
    proj.commit(final_gid, "final.txt", "final\n")
    _forget_floor(final_gid)
    recheck = trs.evaluate_group_unreported(proj.pid, final_gid)
    assert recheck["checked"] is True
    assert {row["path"] for row in recheck["unreported"]} == {"final.txt"}


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
