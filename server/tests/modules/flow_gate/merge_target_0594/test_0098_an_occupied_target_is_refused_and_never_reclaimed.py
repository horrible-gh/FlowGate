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
def test_0098_an_occupied_target_is_refused_and_never_reclaimed(proj, check_budget):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import merge_target as mt

    gid_a, gid_b, gid_c = proj.group(5), proj.group(6), proj.group(7)
    proj.local_branch("develop")
    attempt = _open_attempt(proj, gid_a, "develop", "op:crashed")            # interrupted: recovery's to reclaim
    busy = _refused(proj, gid_b, "develop")
    assert (busy.code, busy.details["merge_id"]) == ("merge_target_busy", attempt.merge_id)
    assert "develop" not in _check_view(proj, gid_b)["merge_target_options"]
    assert _session(attempt.merge_id)["status"] == "open"                       # judged, not reclaimed
    assert proj.workspace("develop").exists()
    # the caller's own attempt is not "another"
    assert _judge(proj, gid_a, "develop", own_merge_id=attempt.merge_id)["target_branch"] == "develop"
    marker_path = proj.workspace("develop") / "owner.json"
    original = marker_path.read_text(encoding="utf-8")
    marker = json.loads(original)
    marker["owner"] = "someone-else"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    assert _refused(proj, gid_b, "develop").code == "merge_target_owner_mismatch"
    state = _check_view(proj, gid_b, "develop")["merge_target_state"]["current"]
    assert (state["branch"], state["error"]) == ("develop", "merge_target_owner_mismatch")
    # another owner's marker on the caller's own merge id is still a mismatch, never the caller's resume
    assert mt.inspect_workspace(proj.pid, "develop")[0] == "mismatch"
    own_but_foreign = _refused(proj, gid_a, "develop", own_merge_id=attempt.merge_id)
    assert (own_but_foreign.code, own_but_foreign.details["merge_id"]) == (
        "merge_target_owner_mismatch", attempt.merge_id)
    marker_path.write_text(original, encoding="utf-8")
    assert _judge(proj, gid_a, "develop", own_merge_id=attempt.merge_id)["target_branch"] == "develop"
    # a second open claim on the same workspace: the first claim being the caller's own does not hide it
    gid_d = proj.group(57)
    second = mt.open_attempt(gid_d, attempt, "merge", "op:third", "third")
    verdict, details = mt.inspect_workspace(proj.pid, "develop")
    assert verdict == "mismatch" and details["merge_id"] in (attempt.merge_id, second.merge_id)
    assert _refused(proj, gid_a, "develop", own_merge_id=attempt.merge_id).code == "merge_target_owner_mismatch"
    assert _refused(proj, gid_d, "develop", own_merge_id=second.merge_id).code == "merge_target_owner_mismatch"
    assert _session(attempt.merge_id)["status"] == _session(second.merge_id)["status"] == "open"
    # another attempt into the project base: the base is in use
    cfg = db_git.get_config(proj.pid)
    mt.open_attempt(gid_c, mt.plan_finalize_target(gid_c, proj.pid, cfg, None), "merge", "op:other", "other")
    assert _refused(proj, gid_b, "main").code == "merge_target_busy"
    assert _check_view(proj, gid_b)["merge_target_options"] == []

