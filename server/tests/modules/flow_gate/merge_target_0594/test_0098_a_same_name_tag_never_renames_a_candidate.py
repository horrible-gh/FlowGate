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
def test_0098_a_same_name_tag_never_renames_a_candidate(proj, check_budget):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import group_work_base as gwb

    gid, other = proj.group(62), proj.group(63)
    proj.local_branch("develop")
    other_branch = db_git.get_state(other)["branch"]
    for tag in ("main", "develop", other_branch):
        _git(["tag", tag, "main"], cwd=proj.base)
    assert _git(["for-each-ref", "--format=%(refname:short)", "refs/heads/main"], cwd=proj.base).strip() == (
        "heads/main")                                                     # what a short name answers now
    view = _check_view(proj, gid)
    assert view["merge_target_options"] == ["develop", "main"]           # the branch names; the slot left out
    assert view["merge_target_state"]["status"] == "ok"
    for name in view["merge_target_options"]:
        assert _judge(proj, gid, name)["target_branch"] == name
    assert _judge(proj, gid, "main")["is_project_base"] is True          # the base checkout still reads as on main
    slot = _refused(proj, gid, other_branch)
    assert (slot.code, slot.details["connected_group_id"]) == ("merge_target_internal_slot", other)
    refs = gwb.local_branch_refs(proj.base)
    assert {"develop", "main", other_branch} <= set(refs) and not any(n.startswith("heads/") for n in refs)
    options = gwb.list_group_work_base_options(proj.pid)["branches"]
    assert {"name": "main", "is_project_base": True, "remote_only": False} in options
    assert not any(o["name"].startswith("heads/") or o["name"] == other_branch for o in options)

