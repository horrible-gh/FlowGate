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
@pytest.mark.parametrize('damage', ['branch'])
def test_0098_unmerge_new_decision_target_damage_never_uses_legacy_base(proj, monkeypatch, damage):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt
    from flowgate.gate_decisions import repository as decisions
    gid = proj.group(1)
    proj.local_branch("review/unmerge")
    (proj.wt(gid) / "new-target.txt").write_text("new\n", encoding="utf-8")
    proj.ready(gid)
    out = svc.finalize(gid, "merge_only", target_branch="review/unmerge")
    state = svc.db_git.get_state(gid)
    sha = out["result"]["merge_commit"]
    session = svc.db_git.get_session(state["merge_id"])
    context = svc.db_git.session_context(session)
    rec = mt.target_record(session)
    basis = {"target_branch": "review/unmerge", "accepted_target_sha": "a" * 40,
             "project_base_branch": "main", "is_project_base": False, "unmerge_supported": False}
    context["gate_decision_id"] = "gd0098-unmerge"
    context[mt.TARGET_RECORD_KEY] = {**rec, **{k: basis[k] for k in (
        "accepted_target_sha", "project_base_branch", "is_project_base", "unmerge_supported")}}
    if damage == "missing":
        context.pop(mt.TARGET_RECORD_KEY)
    elif damage == "flags":
        context[mt.TARGET_RECORD_KEY]["is_project_base"] = True
    else:
        context[mt.TARGET_RECORD_KEY]["branch"] = "main"
    svc.db_git.set_session_context(state["merge_id"], context)
    monkeypatch.setattr(decisions, "get", lambda did: {"decision_id": did, "target_branch": "review/unmerge",
        "settings": {"shown": {"merge_target_branch": "review/unmerge"}, "target_basis": basis}})
    monkeypatch.setattr(decisions, "effect", lambda *a: {"evidence": {"merge_id": state["merge_id"]}})
    from modules.flow_gate.db.connection import get_store
    store = get_store()
    jobs = store._fetch_all("SELECT * FROM operation_job")
    base_before = proj.base_sha()
    target_before = _git(["rev-parse", "review/unmerge"], cwd=proj.base)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.unmerge(gid, sha)
    assert caught.value.code == "unmerge_target_unknown"
    assert caught.value.status == 409
    assert caught.value.details["target_branch"] == "review/unmerge"
    assert svc.db_git.get_state(gid) == state
    assert store._fetch_all("SELECT * FROM operation_job") == jobs
    assert proj.base_sha() == base_before
    assert _git(["rev-parse", "review/unmerge"], cwd=proj.base) == target_before

