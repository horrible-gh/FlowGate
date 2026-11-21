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

def test_C_prime_origin_ahead_forks_from_origin_and_initial_sync_keeps_it(proj):
    from modules.flow_gate.services import git_service as svc
    origin_tip = proj.origin_commit("v0.2", "origin_ahead.txt", "o\n")
    gid = proj.group(4, work_base="v0.2")
    local_tip = proj.base_git("rev-parse", "refs/heads/v0.2")
    assert local_tip != origin_tip
    assert _state(gid)["work_base_sha"] == origin_tip
    assert proj.wt_git(gid, "rev-parse", "HEAD") == origin_tip

    out = svc.ensure_initial_group_source_sync(proj.pid, "default", gid)
    assert out["performed"] is True and out["sha"] == origin_tip
    assert (proj.wt(gid) / "origin_ahead.txt").exists()   # not rewound to the local tip
    assert _scope(proj.pid, gid) == set()
def test_E_origin_only_advance_absorbed_by_update_is_not_group_work(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(5, work_base="v0.2")
    proj.commit(gid, "mine.txt", "m\n")
    x1 = proj.origin_commit("v0.2", "x1.txt", "1\n")
    x2 = proj.origin_commit("v0.2", "x2.txt", "2\n")
    local_tip = proj.base_git("rev-parse", "refs/heads/v0.2")

    out = svc.update_from_base(gid)["result"]
    assert out["status"] == "updated"
    assert out["source_ref"] == "origin/v0.2" and out["source_sha"] == x2
    assert _state(gid)["work_base_sync_sha"] == x2
    assert proj.base_git("rev-parse", "refs/heads/v0.2") == local_tip   # local never moved

    assert _scope(proj.pid, gid) == {"mine.txt"}
    changes = svc.read_group_changes(proj.pid, gid)["data"]["changes"]
    assert {c["path"] for c in changes} == {"mine.txt"}
    # Contrast: a NAME-based floor (local v0.2 tip) counts X1..Xn as group work.
    named = proj.wt_git(gid, "merge-base", "HEAD", "refs/heads/v0.2")
    assert {"x1.txt", "x2.txt"} <= set(proj.wt_git(gid, "diff", "--name-only", named).split())
    from modules.flow_gate.db import git_integration as db_git
    log = db_git.list_work_base_log(gid)
    assert [row["kind"] for row in log] == ["fork", "update"]
    assert log[-1]["source_sha"] == x2 and x1 != x2
def test_E_repeat_floor_only_moves_forward(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(6, work_base="v0.2")
    first = proj.origin_commit("v0.2", "r1.txt", "1\n")
    assert svc.update_from_base(gid)["result"]["source_sha"] == first
    # Second round: the LOCAL work base is ahead of origin now.
    proj.base_git("checkout", "-q", "v0.2")
    proj.base_git("merge", "-q", "--ff-only", "origin/v0.2")
    (proj.base / "r2.txt").write_text("2\n", encoding="utf-8")
    proj.base_git("add", "-A")
    proj.base_git("commit", "-m", "local r2")
    second = proj.base_git("rev-parse", "HEAD")
    proj.base_git("checkout", "-q", "main")
    out = svc.update_from_base(gid)["result"]
    assert out["source_ref"] == "v0.2" and out["source_sha"] == second
    assert _state(gid)["work_base_sync_sha"] == second
    assert proj.base_git("merge-base", "--is-ancestor", first, second) == ""
    assert _scope(proj.pid, gid) == set()
def test_E_conflict_abort_keeps_floor_and_resolution_records_source(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(7, work_base="v0.2")
    proj.commit(gid, "shared.txt", "group version\n")
    floor = _state(gid)["work_base_sha"]
    tip = proj.origin_commit("v0.2", "shared.txt", "v0.2 version\n")

    first = svc.update_from_base(gid)["result"]
    assert first["status"] == "conflict" and first["source_sha"] == tip
    svc.abort_merge(gid, first["merge_id"])
    assert _state(gid)["work_base_sync_sha"] is None
    assert _state(gid)["work_base_sha"] == floor
    assert _scope(proj.pid, gid) == {"shared.txt"}

    second = svc.update_from_base(gid)["result"]
    assert second["status"] == "conflict"
    resolved = svc.resolve_conflicts(
        gid, second["merge_id"],
        [{"path": "shared.txt", "content": "group version\nv0.2 version\n"}], True,
    )["result"]
    assert resolved["status"] == "updated" and resolved["source_sha"] == tip
    assert _state(gid)["work_base_sync_sha"] == tip
    assert _scope(proj.pid, gid) == {"shared.txt"}
