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

def test_LB2_legacy_marker_is_not_copied_and_prior_tr_files_stay_in_scope(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(12, work_base="v0.2")
    c1 = proj.commit(gid, "tr1.txt", "1\n")
    _ledger_commit(gid, proj.pid, 2, c1, "2026-01-01T00:00:00")
    c2 = proj.commit(gid, "tr2.txt", "2\n")
    _ledger_commit(gid, proj.pid, 3, c2, "2026-01-01T00:00:01")
    _forget_floor(gid)
    _set_marker(gid, c2, "2026-01-02T00:00:00")   # legacy path: marker = HEAD with TR work
    c3 = proj.commit(gid, "tr3.txt", "3\n")
    _ledger_commit(gid, proj.pid, 4, c3, "2026-01-03T00:00:00")

    # Contrast: the marker passes "ancestor of HEAD" yet would drop tr1/tr2.
    assert proj.wt_git(gid, "merge-base", "--is-ancestor", c2, "HEAD") == ""
    assert "tr1.txt" not in proj.wt_git(gid, "diff", "--name-only", c2)

    svc.backfill_group_work_base(proj.pid, apply=True)
    state = _state(gid)
    assert state["work_base_state"] == "verified"
    assert state["work_base_sha"] == proj.base_git("rev-parse", "v0.2")
    assert _scope(proj.pid, gid) == {"tr1.txt", "tr2.txt", "tr3.txt"}
def test_LB4_group_already_merged_into_its_work_base_never_reports_empty(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(13, work_base="v0.2")
    c1 = proj.commit(gid, "merged_work.txt", "w\n")
    _ledger_commit(gid, proj.pid, 2, c1, "2026-01-01T00:00:00")
    proj.base_git("branch", "-f", "v0.2", c1)               # someone merged it by hand
    _forget_floor(gid)
    _set_marker(gid, c1, "2026-01-02T00:00:00")
    svc.backfill_group_work_base(proj.pid, apply=True)
    state = _state(gid)
    if state["work_base_state"] == "verified":
        assert "merged_work.txt" in _scope(proj.pid, gid)
    else:
        assert svc.collect_scope_changes(proj.pid, gid)["reason"] == "group_work_base_unverified"
def test_SB1_no_update_history_is_verified_with_null_sync(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(14, work_base="v0.2")
    proj.commit(gid, "s.txt", "s\n")
    _forget_floor(gid)
    svc.backfill_group_work_base(proj.pid, apply=True)
    state = _state(gid)
    assert state["work_base_state"] == "verified" and state["work_base_sync_sha"] is None
