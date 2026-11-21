"""Independently selectable scenarios; shared fixtures remain in test_group_work_base_change_0104.py."""
from __future__ import annotations

from tests.integration.group_git.test_group_work_base_change_0104 import (
    ACTIVE_STATUSES,
    ADMIN,
    BRANCH,
    Env,
    FastAPI,
    GID,
    GitServiceError,
    NOW,
    OTHER_GID,
    PROJECT,
    PROJECT_NAME,
    Path,
    TestClient,
    WORKER,
    _STORE_FAULTS,
    _arm_store_fault,
    _assert_next_provision_uses,
    _assert_race_lost_without_git_effect,
    _client,
    _error,
    _get,
    _git,
    _job_settles_during_validation,
    _patch,
    _race,
    _stub_outbox_io,
    _v01,
    _v01_codes,
    base_slot,
    db_groups,
    env,
    get_current_user,
    git_service,
    group_store,
    group_work_base,
    hold_group_lock,
    in_transaction,
    job_creates,
    job_store,
    lock_manager,
    process_service,
    pytest,
    pytestmark,
    sqlite3,
    subprocess,
    threading,
    workflow,
    worktree_provision,
    worktree_service,
)

def test_g_v01_job_failing_before_the_branch_still_changes(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=False)
    assert _v01("feature-b")["status"] == "success"
    assert calls == ["feature-b", "feature-b"]
    assert env.stored() == "feature-b"
def test_g_jobs_that_keep_settling_end_busy_without_a_save(env, monkeypatch):
    reads = []

    def always_new(_group_id):
        reads.append(1)
        return frozenset({len(reads)})

    monkeypatch.setattr(group_work_base, "_settled_provision_jobs", always_new)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_busy")
    assert len(reads) == 2 * group_work_base._CHANGE_ATTEMPTS
    assert env.stored() == "feature-a"
@pytest.mark.parametrize("evidence", ["work_base_record", "provision_succeeded",
                                      "group_branch_exists", "ledger"])
def test_f_v01_extended_lock_refuses_what_groups_list_shows_editable(
    env, monkeypatch, tmp_path, evidence,
):
    _stub_outbox_io(monkeypatch, tmp_path)
    if evidence == "work_base_record":
        env.state(work_base_sha="0" * 40, work_base_state="verified")
    elif evidence == "provision_succeeded":
        env.register_job("succeeded")
    elif evidence == "group_branch_exists":
        _git(env.repo, "branch", BRANCH, "main")
    else:
        env.state(status="waiting")
    listed = next(g for g in _client().get(f"/api/v1/groups?project_id={PROJECT}").json()["groups"]
                  if g["group_id"] == GID)
    assert listed["work_base_locked"] is (evidence == "ledger")
    result = _v01("feature-b")
    assert _v01_codes(result) == ["group_work_base_locked"]
    assert result["errors"][0]["details"] == {"lock_evidence": [evidence]}
    assert env.stored() == "feature-a"
def test_v01_and_patch_share_the_one_change_path(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    calls = []
    real = group_work_base.change_group_work_base_ref

    def spy(group_id, value, **kwargs):
        calls.append(("expected" in kwargs, kwargs.get("project_id")))
        return real(group_id, value, **kwargs)

    monkeypatch.setattr(git_service, "change_group_work_base_ref", spy)
    assert _v01("feature-b")["status"] == "success"
    assert _patch({"work_base_ref": "feature-a",
                   "expected_work_base_ref": "feature-b"}).status_code == 200
    assert calls == [(False, PROJECT), (True, None)]
    assert git_service.apply_group_work_base_ref(PROJECT, GID, "feature-b") == "feature-b"
    with pytest.raises(GitServiceError) as caught:
        git_service.apply_group_work_base_ref("p", GID, "feature-a")
    assert caught.value.code == "group_not_found"
def test_job_registration_inside_a_transaction_is_still_refused(env, job_creates):
    with env.store.transaction():
        with pytest.raises(job_store.JobProgramError) as caught:
            job_store.create_or_get_job(
                worktree_provision.KIND, PROJECT, {"group_id": GID, "group_generation": "x"},
                group_id=GID, payload={}, wake=False)
    assert caught.value.code == "job_create_inside_transaction"
    assert job_creates == [True]      # the spy sees it; this deliberate call is not a finding
    job_creates.clear()
    assert env.jobs() == []
def test_missing_base_checkout_provisions_the_base_then_takes_the_job_path(monkeypatch):
    order = []
    slot = {"state": "missing"}
    cfg = {"enabled": 1, "base_branch": "main"}
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: cfg)
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: PROJECT_NAME)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "get_branch_merge_group_claim", lambda _gid: None)
    monkeypatch.setattr(git_service.db_git, "get_state", lambda _gid: None)
    monkeypatch.setattr(base_slot, "_judge_base_slot", lambda *_a: (
        order.append(("judge", slot["state"])) or slot["state"]))

    def provision_base(project_id, trigger):
        order.append(("provision_base", project_id))
        slot["state"] = "checkout"
        return {"status": "ready", "reason": None}

    monkeypatch.setattr(git_service, "provision_base", provision_base)
    monkeypatch.setattr(worktree_provision, "provision",
                        lambda *args: order.append(("job", args[3])) or "ok")
    for owner in (worktree_service, git_service):
        monkeypatch.setattr(owner, "_ensure_worktree_locked",
                            lambda *_a, **_k: pytest.fail("the old mutex path was taken"))
    assert git_service.ensure_worktree(PROJECT, "default", GID) == "ok"
    assert order == [("judge", "missing"), ("provision_base", PROJECT),
                     ("judge", "checkout"), ("job", GID)]
