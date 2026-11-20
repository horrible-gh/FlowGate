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
@pytest.mark.parametrize('status,effect_status', [('accepted', 'pending')])
def test_0098_base_unmerge_ignores_unfinished_decisions_accepted_at_its_result(proj, status, effect_status):
    from flowgate.gate_decisions import repository as decisions
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    (proj.wt(gid) / "base-before-gate.txt").write_text("base merge\n", encoding="utf-8")
    proj.ready(gid)
    before = proj.base_sha()
    out = svc.finalize(gid, "merge_only")
    assert out["result"]["status"] == "merged"
    sha = out["result"]["merge_commit"]
    state = db_git.get_state(gid)
    session = db_git.get_session(state["merge_id"])
    assert not db_git.session_context(session).get("gate_decision_id")
    assert "accepted_target_sha" not in mt.target_record(session)

    # A later gate accepts this HEAD as its input. It did not produce this merge.
    decision = decisions.insert_decision({
        "group_id": gid, "run_id": gid + ":run", "node_key": "gate", "attempt_id": gid + ":attempt",
        "revision_no": 0, "actor_user_id": "test", "request_id": "later-gate",
        "request_fingerprint": "0" * 64, "decision": decisions.CONFIRM,
        "target_branch": "main", "target_sha": sha,
        "settings": {"shown": {"merge_target_branch": "main"}, "effective": {"merge_mode": "merge"},
                     "target_basis": {"target_branch": "main", "project_base_branch": "main",
                                      "is_project_base": True, "unmerge_supported": True,
                                      "accepted_target_sha": sha}},
    })
    did = decision["decision_id"]
    if status != decisions.ACCEPTED:
        assert decisions.move(did, decision["decision_version"], status)
    decisions.record_effect(did, "merge", status=effect_status, sha_before=sha,
                            sha_after=None if effect_status == "pending" else sha)
    pending_before = decisions.get(did), decisions.effect(did, "merge")

    result = svc.unmerge(gid, sha)
    assert result["result"]["unmerged"] is True
    assert proj.base_sha() == before
    assert db_git.get_state(gid)["status"] == "awaiting_choice"
    assert (decisions.get(did), decisions.effect(did, "merge")) == pending_before

