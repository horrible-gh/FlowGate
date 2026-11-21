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

def test_E_untracked_refusal_does_not_move_the_floor(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(8, work_base="v0.2")
    # A policy-excluded path is never absorbed, so it stays untracked and blocks.
    proj.origin_commit("v0.2", "debug.log", "theirs\n")
    (proj.wt(gid) / "debug.log").write_text("mine, untracked\n", encoding="utf-8")
    # The recover probe classifies against the SAME source the update merges
    # (main has no debug.log, so a project-base probe would find no blocker).
    with pytest.raises(GitServiceError) as exc:
        svc.update_from_base(gid)
    assert exc.value.code == "group_untracked_conflict"
    assert _state(gid)["work_base_sync_sha"] is None
    with pytest.raises(GitServiceError) as bad:
        svc.group_update_untracked_recover(gid, ["README.md"], "remove")
    assert "debug.log" in bad.value.details["allowed"]
def test_E_prime_rewritten_work_base_fails_explicitly(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(9, work_base="v0.2")
    proj.commit(gid, "g.txt", "g\n")
    # v0.2 is rewritten on both sides: a new root-less history replaces it.
    _git(["checkout", "-B", "v0.2", "main"], cwd=proj.seed)
    (proj.seed / "rewritten.txt").write_text("r\n", encoding="utf-8")
    _git(["add", "-A"], cwd=proj.seed)
    _git(["commit", "-m", "rewrite"], cwd=proj.seed)
    _git(["push", "-f", "origin", "v0.2"], cwd=proj.seed)
    _git(["checkout", "main"], cwd=proj.seed)
    proj.base_git("fetch", "origin", "+v0.2:refs/remotes/origin/v0.2")
    proj.base_git("branch", "-f", "v0.2", "origin/v0.2")

    result = svc.collect_scope_changes(proj.pid, gid)
    assert result["available"] is False
    assert result["reason"] == "group_work_base_diverged"
    with pytest.raises(GitServiceError) as exc:
        svc.read_group_changes(proj.pid, gid)
    assert exc.value.code == "group_work_base_diverged"
    with pytest.raises(GitServiceError) as upd:
        svc.update_from_base(gid)
    assert upd.value.code in ("group_work_base_diverged", "group_work_base_source_rewritten")
def test_D_F_new_groups_pin_the_base_and_base_change_pins_locked_null_rows(proj):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.workflow import pipeline_service

    created = pipeline_service.create_group(
        project_id=proj.pid, module="default", title="t", actor_user_id="u",
        user_permissions={"project.group.manage"}, group_id=f"{proj.pid}.default.0100",
    )
    assert created["work_base_ref"] == "main"            # F: never NULL for a Git project

    legacy = proj.group(10)                                # NULL work_base_ref, locked
    assert db_groups.get_by_id(legacy)["work_base_ref"] is None
    floor = _state(legacy)["work_base_sha"]
    svc.create_branch(proj.pid, "develop", "main")
    out = svc.save_config(proj.pid, {
        "repo_url": proj.bare.as_uri(), "provider": "generic", "base_branch": "develop",
        "default_finalize_action": "merge", "enabled": True,
    })
    assert legacy in out["pinned_work_base_groups"]
    assert db_groups.get_by_id(legacy)["work_base_ref"] == "main"
    assert svc.resolve_group_work_base_ref(proj.pid, legacy) == "main"
    assert _state(legacy)["work_base_sha"] == floor
    proj.commit(legacy, "d.txt", "d\n")
    assert _scope(proj.pid, legacy) == {"d.txt"}
def test_LB1_reset_marker_is_verified(proj):
    from modules.flow_gate.services import git_service as svc
    gid = proj.group(11, work_base="v0.2")
    fork = _state(gid)["work_base_sha"]
    _forget_floor(gid)
    _set_marker(gid, fork, "2026-01-02T00:00:00")
    proj.commit(gid, "after.txt", "a\n")
    _ledger_commit(gid, proj.pid, 2, proj.wt_git(gid, "rev-parse", "HEAD"), "2026-01-03T00:00:00")

    report = svc.backfill_group_work_base(proj.pid)          # dry-run first
    row = next(r for r in report["groups"] if r["group_id"] == gid)
    assert row["verdict"] == "verified" and row["origin"] == "initial_sync_marker"
    assert _state(gid)["work_base_state"] is None            # dry-run wrote nothing
    svc.backfill_group_work_base(proj.pid, apply=True, actor="admin")
    assert _state(gid)["work_base_sha"] == fork and _state(gid)["work_base_state"] == "verified"
    assert _scope(proj.pid, gid) == {"after.txt"}
