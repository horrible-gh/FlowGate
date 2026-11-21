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

def test_M_base_group_default_target_stays_main(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(20)
    proj.commit(gid, "b.txt", "b\n")
    proj.ready(gid)
    out = svc.finalize(gid, "merge_only")["result"]
    assert out["target_branch"] == "main" and out["unmerge_supported"] is True
def test_I_terminal_reopen_records_a_new_floor_after_the_merged_work(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(21, work_base="v0.2")
    c1 = proj.commit(gid, "first.txt", "1\n")
    _ledger_commit(gid, proj.pid, 2, c1)
    proj.ready(gid)
    out = svc.finalize(gid, "merge_only")["result"]
    assert out["status"] == "merged"
    v02_tip = proj.base_git("rev-parse", "v0.2")
    svc.reopen_group_git(proj.pid, gid, terminal_commit_sha=c1)
    state = _state(gid)
    assert state["work_base_state"] == "verified", state
    assert state["work_base_sha"] == v02_tip
    assert _scope(proj.pid, gid) == set()
    proj.commit(gid, "second.txt", "2\n")
    assert _scope(proj.pid, gid) == {"second.txt"}
def test_K_remote_tools_default_to_the_work_base(proj):
    from modules.flow_gate.services import remote_tool_service as rts
    gid = proj.group(22, work_base="v0.2")
    assert rts._default_target_ref({"project": proj.pid, "group_id": gid}, proj.wt(gid)) == "v0.2"
    proj.origin_commit("v0.2", "ahead.txt", "a\n")
    proj.base_git("fetch", "origin")
    assert rts._default_target_ref({"project": proj.pid, "group_id": gid}, proj.wt(gid)) == "origin/v0.2"
    assert rts._default_target_ref({"project": proj.pid}, proj.base) == "main"
def test_L_scope_readers_never_measure_from_the_project_base():
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services import review_package_service as rps
    from modules.flow_gate.services.git import finalize

    for fn in (svc.collect_scope_changes, svc._group_diff_context, svc.read_group_file_diff,
               rps.build_review_package):
        source = inspect.getsource(fn)
        assert "resolve_scope_floor" in source or "scope_base_sha" in source, fn.__name__
        assert 'f"refs/heads/{base_branch}"' not in source, fn.__name__
    recover = inspect.getsource(finalize.group_update_untracked_recover)
    assert '"--no-ff", base_branch]' not in recover and "_worktree_start_point" in recover
    archive = inspect.getsource(inbox_routes)
    assert '["merge-base", base_branch, branch]' not in archive
