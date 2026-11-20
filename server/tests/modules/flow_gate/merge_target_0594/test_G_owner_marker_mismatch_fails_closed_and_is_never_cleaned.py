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
def test_G_owner_marker_mismatch_fails_closed_and_is_never_cleaned(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid_a = proj.group(1)
    gid_b = proj.group(2)
    proj.local_branch("develop")
    merge_a = _conflict_on(proj, gid_a, "develop")["merge_id"]
    ws = proj.workspace("develop")
    marker = json.loads((ws / "owner.json").read_text(encoding="utf-8"))
    marker["owner"] = "someone-else"
    (ws / "owner.json").write_text(json.dumps(marker), encoding="utf-8")

    (proj.wt(gid_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(gid_b)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_owner_mismatch"

    # abort of A is refused BEFORE git runs: the workspace is not A's by marker, so
    # A's row, the merge state and the workspace all stay (see the next test)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.abort_merge(gid_a, merge_a)
    assert caught.value.code == "merge_target_owner_mismatch"
    assert _session(merge_a)["status"] == "open"
    assert (ws / "tree").is_dir() and (ws / "owner.json").exists()
    svc.merge_session_sweep()
    assert (ws / "tree").is_dir()
    # a marker with no matching open attempt still fails closed (no takeover)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    assert caught.value.code == "merge_target_owner_mismatch"
    assert db_git.get_open_session_by_group(gid_b) is None

