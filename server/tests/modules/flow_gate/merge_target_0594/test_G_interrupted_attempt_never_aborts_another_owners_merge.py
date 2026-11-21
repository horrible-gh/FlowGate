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
def test_G_interrupted_attempt_never_aborts_another_owners_merge(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.ready(gid)
    attempt = _open_attempt(proj, gid, "develop", "op:crashed")
    tree = proj.workspace("develop") / "tree"
    # someone else's in-flight merge state in the tree, marker naming them
    wb = gid.replace(".", "_")
    (proj.wt(gid) / "x.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], cwd=proj.wt(gid))
    _git(["commit", "-m", "x"], cwd=proj.wt(gid))
    _git(["merge", "--no-commit", "--no-ff", wb], cwd=tree)
    marker_path = proj.workspace("develop") / "owner.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["owner"] = "someone-else"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    head_before = _merge_head(tree)
    svc.startup_recovery()
    assert _session(attempt.merge_id)["status"] == "open"
    assert _merge_head(tree) == head_before
    assert (tree / "x.txt").exists()

