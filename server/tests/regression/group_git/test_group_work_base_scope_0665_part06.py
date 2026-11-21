"""Independently selectable scenarios; shared fixtures remain in test_group_work_base_scope_0665.py."""
from __future__ import annotations

from tests.regression.group_git.test_group_work_base_scope_0665 import (
    Path,
    Proj,
    V02_ONLY,
    _COUNTER,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _fail_audit_log,
    _forget_floor,
    _git,
    _ledger_commit,
    _scope,
    _seed_wf_done_root,
    _set_marker,
    _state,
    base64,
    contextmanager,
    inspect,
    itertools,
    json,
    os,
    patch_store,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    tempfile,
    threading,
)

def test_E_conflict_resolution_audit_failure_keeps_the_previous_floor(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(31, work_base="v0.2")
    proj.commit(gid, "shared.txt", "group version\n")
    fork = _state(gid)["work_base_sha"]
    tip = proj.origin_commit("v0.2", "shared.txt", "v0.2 version\n")
    head = proj.wt_git(gid, "rev-parse", "HEAD")
    resolution = [{"path": "shared.txt", "content": "group version\nv0.2 version\n"}]

    first = svc.update_from_base(gid)["result"]
    assert first["status"] == "conflict"
    with monkeypatch.context() as m:
        _fail_audit_log(m)
        with pytest.raises(GitServiceError) as exc:
            svc.resolve_conflicts(gid, first["merge_id"], resolution, True)
    assert exc.value.code == "group_work_base_record_failed"
    state = _state(gid)
    assert state["work_base_sync_sha"] is None and state["work_base_sha"] == fork
    assert proj.wt_git(gid, "rev-parse", "HEAD") == head              # resolution undone
    assert db_git.get_session(first["merge_id"])["status"] == "aborted"
    assert _scope(proj.pid, gid) == {"shared.txt"}
    assert [row["kind"] for row in db_git.list_work_base_log(gid)] == ["fork"]

    second = svc.update_from_base(gid)["result"]
    assert second["status"] == "conflict"
    resolved = svc.resolve_conflicts(gid, second["merge_id"], resolution, True)["result"]
    assert resolved["status"] == "updated" and _state(gid)["work_base_sync_sha"] == tip
def test_backfill_leaves_a_group_unapplied_when_its_work_base_ref_pin_fails(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(32)                                     # NULL work_base_ref
    proj.commit(gid, "p.txt", "p\n")
    _forget_floor(gid)
    log_before = len(db_git.list_work_base_log(gid))

    def raising_pin(*_args, **_kwargs):
        raise RuntimeError("pin write failed")
    for failing_pin in (raising_pin, lambda *_a, **_k: None):   # raises / silently no-op
        with monkeypatch.context() as m:
            m.setattr(db_groups, "update_work_base_ref", failing_pin)
            report = svc.backfill_group_work_base(proj.pid, apply=True, actor="admin")
        row = next(r for r in report["groups"] if r["group_id"] == gid)
        assert row["verdict"] == "verified"                     # classification itself is fine
        assert row["applied"] is False and row["apply_error"] == "work_base_ref_pin_failed"
        assert gid in report["apply_failed"]
        assert db_groups.get_by_id(gid)["work_base_ref"] is None
        state = _state(gid)
        assert state["work_base_state"] is None and state["work_base_sha"] is None
        assert len(db_git.list_work_base_log(gid)) == log_before
        assert svc.collect_scope_changes(proj.pid, gid)["reason"] == "group_work_base_unverified"

    report = svc.backfill_group_work_base(proj.pid, apply=True, actor="admin")
    row = next(r for r in report["groups"] if r["group_id"] == gid)
    assert row["applied"] is True and gid not in report["apply_failed"]
    assert db_groups.get_by_id(gid)["work_base_ref"] == "main"
    assert _state(gid)["work_base_state"] == "verified"
    assert _scope(proj.pid, gid) == {"p.txt"}
def test_G_default_target_is_the_work_base_and_non_base_merge_is_recorded(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    main_before = proj.base_git("rev-parse", "main")
    gid = proj.group(18, work_base="v0.2")
    proj.commit(gid, "feature.txt", "f\n")
    view = svc.get_finalize_state(gid)["state"]
    assert view["default_target"] == "v0.2" and view["default_target_unmerge_supported"] is False

    proj.ready(gid)
    out = svc.finalize(gid, "merge_only")["result"]        # no target: unattended default
    assert out["status"] == "merged" and out["target_branch"] == "v0.2"
    assert out["unmerge_supported"] is False
    record = db_git.session_context(db_git.get_session(_state(gid)["merge_id"]))["merge_target"]
    assert record["unmerge_supported"] is False and record["defaulted_to_work_base"] is True
    assert proj.base_git("rev-parse", "main") == main_before   # main untouched
    assert "feature.txt" in proj.base_git("ls-tree", "--name-only", "v0.2").split()

    with pytest.raises(GitServiceError) as exc:
        svc.unmerge(gid, out["merge_commit"])
    assert exc.value.code == "unmerge_unsupported_target"
    assert "revert_merge_on_target" in exc.value.details["recovery_options"]
def test_G_prime_preview_counts_work_base_commits_flowing_into_main(proj):
    from modules.flow_gate.services.git import merge_target
    gid = proj.group(19, work_base="v0.2")
    proj.commit(gid, "p.txt", "p\n")
    default = merge_target.preview_target(gid)
    assert default["target_branch"] == "v0.2" and default["warnings"] == [
        "unmerge_unsupported_for_non_base_target"]
    other = merge_target.preview_target(gid, "main")
    assert other["is_work_base"] is False and other["unmerge_supported"] is True
    assert other["incoming_work_base_commits"] == len(V02_ONLY)
    assert "target_differs_from_work_base" in other["warnings"]
