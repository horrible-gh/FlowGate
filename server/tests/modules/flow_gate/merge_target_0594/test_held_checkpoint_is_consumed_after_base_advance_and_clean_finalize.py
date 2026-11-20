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
def test_held_checkpoint_is_consumed_after_base_advance_and_clean_finalize(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    (proj.wt(gid) / "shared.txt").write_text("group answer\n", encoding="utf-8")
    _git(["add", "shared.txt"], cwd=proj.wt(gid))
    _git(["commit", "-m", "group change"], cwd=proj.wt(gid))
    held_base = proj.base_sha()
    (proj.base / "unrelated.txt").write_text("other group advance\n", encoding="utf-8")
    _git(["add", "unrelated.txt"], cwd=proj.base)
    _git(["commit", "-m", "advance base"], cwd=proj.base)
    _git(["push", "origin", "main"], cwd=proj.base)
    proj.ready(gid)

    old_merge_id = db_git.create_session(gid, ["shared.txt"], finalize_action="merge_only")
    db_git.close_session(old_merge_id, "aborted")
    checkpoint = db_git.create_resolution_checkpoint({
        "project_id": proj.pid, "group_id": gid, "source_merge_id": old_merge_id,
        "target_branch": "main", "source_branch": db_git.get_state(gid)["branch"],
        "base_head": held_base,
        "merge_head": _git(["rev-parse", "HEAD"], cwd=proj.wt(gid)).strip(),
        "resolved_paths": ["shared.txt"], "conflict_origins": [],
        "provenance": {"shared.txt": {"rerere_id": "held"}},
    })
    assert db_git.active_resolution_checkpoint(proj.pid, gid)["checkpoint_id"] == checkpoint["checkpoint_id"]

    result = svc.finalize(gid, "merge_only")
    assert result["result"]["status"] == "merged"
    assert db_git.get_resolution_checkpoint(checkpoint["checkpoint_id"])["state"] == "consumed"
    assert db_git.active_resolution_checkpoint(proj.pid, gid) is None

