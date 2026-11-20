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


def test_workspace_path_is_deterministic_safe_and_collision_free(storage_dir):
    from modules.flow_gate.services.git import merge_target as mt

    a = mt.workspace_dir("flowgate", "feature/a")
    assert a == mt.workspace_dir("flowgate", "feature/a")            # deterministic
    assert mt.workspace_key("feature/a") != mt.workspace_key("feature_a")   # no fold collision
    assert mt.workspace_key("a/b") != mt.workspace_key("a_b")
    root = (storage_dir / mt.WORKSPACES_DIR).resolve()
    for name in ("../../etc", "..", "한글/브랜치", "x" * 300, ".hidden", "a\\b"):
        key = mt.workspace_key(name)
        assert "/" not in key and "\\" not in key and not key.startswith(".")
        assert root in mt.workspace_dir("flowgate", name).resolve().parents
    # under {storage}/git_merge_targets — never inside {storage}/src (source explorer)
    assert mt.workspaces_root() == storage_dir / mt.WORKSPACES_DIR
    assert (storage_dir / "src").resolve() not in a.resolve().parents

