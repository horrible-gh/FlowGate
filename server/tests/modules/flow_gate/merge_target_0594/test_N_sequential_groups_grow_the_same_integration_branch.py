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
def test_N_sequential_groups_grow_the_same_integration_branch(proj):
    from modules.flow_gate.services import git_service as svc

    g_a = proj.group(1)
    proj.local_branch("integration-v2")
    (proj.wt(g_a) / "a.txt").write_text("a\n", encoding="utf-8")
    proj.ready(g_a)
    out_a = svc.finalize(g_a, "merge", target_branch="integration-v2")
    assert out_a["result"]["status"] == "merged"
    assert not proj.workspace("integration-v2").exists()

    g_b = proj.group(2)
    (proj.wt(g_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(g_b)
    out_b = svc.finalize(g_b, "merge", target_branch="integration-v2")
    assert out_b["result"]["status"] == "merged"
    assert not proj.workspace("integration-v2").exists()

    g_c = proj.group(3)
    (proj.wt(g_c) / "c.txt").write_text("c\n", encoding="utf-8")
    proj.ready(g_c)
    out_c = svc.finalize(g_c, "merge", target_branch="integration-v2")
    assert out_c["result"]["status"] == "merged"
    assert not proj.workspace("integration-v2").exists()

    files = proj.origin_files("integration-v2")
    assert "a.txt" in files and "b.txt" in files and "c.txt" in files
    main_files = proj.origin_files("main")
    assert "a.txt" not in main_files and "b.txt" not in main_files and "c.txt" not in main_files
    assert proj.base_head_branch() == "main"

