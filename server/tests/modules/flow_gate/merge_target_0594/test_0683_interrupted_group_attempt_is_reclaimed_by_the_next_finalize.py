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
def test_0683_interrupted_group_attempt_is_reclaimed_by_the_next_finalize(proj):
    """(11) B's finalize settles A's dead attempt at plan time — no sweep in between."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid_a = proj.group(1)
    gid_b = proj.group(2)
    proj.local_branch("develop")
    proj.ready(gid_a)
    attempt = _open_attempt(proj, gid_a, "develop", "op:crashed")
    db_git.set_status(gid_a, "merging")
    assert (proj.workspace("develop") / "owner.json").exists()

    (proj.wt(gid_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(gid_b)
    out = svc.finalize(gid_b, "merge", target_branch="develop")
    assert out["result"]["status"] == "merged", out
    assert _session(attempt.merge_id)["status"] == "aborted"
    assert _ctx(attempt.merge_id)["attempt_state"] == "interrupted"
    assert db_git.get_state(gid_a)["status"] == "waiting"
    assert "b.txt" in proj.origin_files("develop")
    assert held_locks(proj.pid) == []

