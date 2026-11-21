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
def test_0098_base_and_non_base_targets_take_their_own_slot_checks(proj, check_budget):
    from modules.flow_gate.db import groups as db_groups

    gid = _feature_group(proj, 4)
    _git(["merge", "--ff-only", "feature/v0.2.0"], cwd=proj.base)
    assert {_judge(proj, gid, n)["target_branch"] for n in ("main", "feature/v0.2.0")} == {"main", "feature/v0.2.0"}
    # the project base checkout off its branch (or detached) refuses every target, the inherited one too.
    # The list does not read the checkout's branch; the names stay listed and the
    # single judgement of a save / confirm / run refuses them
    for off in (["checkout", "feature/v0.2.0"], ["checkout", "--detach", "main"]):
        _git(off, cwd=proj.base)
        for name in ("main", "feature/v0.2.0"):
            assert _refused(proj, gid, name).code == "base_checkout_branch_mismatch"
        view = _check_view(proj, gid)
        assert view["merge_target_options"] == ["feature/v0.2.0", "main"]
        assert view["merge_target_state"]["status"] == "ok"
        assert view["merge_target_state"]["current"]["error"] is None
        _git(["checkout", "main"], cwd=proj.base)
    # tracked local changes in the base checkout refuse the project base only (on save; the list does not read them)
    (proj.base / "README.md").write_text("dirty\n", encoding="utf-8")
    assert _refused(proj, gid, "main").code == "base_dirty"
    assert _judge(proj, gid, "feature/v0.2.0")["is_project_base"] is False
    assert _check_view(proj, gid)["merge_target_options"] == ["feature/v0.2.0", "main"]
    _git(["checkout", "--", "README.md"], cwd=proj.base)
    assert db_groups.get_by_id(gid)["work_base_ref"] == "feature/v0.2.0"

