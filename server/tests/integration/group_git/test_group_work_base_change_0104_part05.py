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

def test_a_change_committed_before_registration_reaches_the_payload(env, job_creates):
    assert _patch({"work_base_ref": "feature-b",
                   "expected_work_base_ref": "feature-a"}).status_code == 200
    _assert_next_provision_uses(env, "feature-b")
    assert (env.wt / "feature-b.txt").exists() and not (env.wt / "feature-a.txt").exists()
    assert job_creates and True not in job_creates
    view = _get().json()
    assert view["reason"] == "history_locked"
    assert {"ledger", "provision_succeeded", "group_branch_exists"} <= set(view["lock_evidence"])
def test_b_registered_job_makes_the_change_busy(env):
    job = env.register_job()
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_busy")
    assert env.stored() == "feature-a"
    assert worktree_provision.attempt(job) == "ok"
    assert _git(env.wt, "rev-parse", "HEAD") == env.sha("feature-a")
def test_c_registration_after_the_job_check_fails_at_the_start_recheck(env, monkeypatch):
    before = env.snapshot()
    response, seen = _race(monkeypatch, lambda: _patch(
        {"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"}))
    assert response.status_code == 200, response.json()
    assert env.stored() == "feature-b"
    _assert_race_lost_without_git_effect(env, seen, before)
    _assert_next_provision_uses(env, "feature-b")
@pytest.mark.parametrize("status", ACTIVE_STATUSES)
def test_d_every_active_job_status_is_busy(env, status):
    env.register_job(status)
    assert _get().json()["reason"] == "provision_busy"
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_busy")
    assert env.stored() == "feature-a"
def test_e_change_after_the_recheck_passed_is_busy(env, monkeypatch):
    real = worktree_provision._recheck_work_base
    refused = []

    def recheck_then_change(slot):
        real(slot)
        try:
            git_service.change_group_work_base_ref(GID, "feature-b", expected="feature-a")
        except GitServiceError as exc:
            refused.append(exc.code)

    monkeypatch.setattr(worktree_provision, "_recheck_work_base", recheck_then_change)
    assert env.provision() == "ok"
    assert refused == ["group_work_base_busy"]
    assert env.stored() == "feature-a"
    assert _git(env.wt, "rev-parse", "HEAD") == env.sha("feature-a")
    monkeypatch.setattr(worktree_provision, "_recheck_work_base", real)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert _error(response)["code"] == "group_work_base_locked"
def test_recheck_runs_on_every_start_attempt_and_not_after_intent(env, monkeypatch):
    real = worktree_provision._recheck_work_base
    rechecks = []
    monkeypatch.setattr(worktree_provision, "_recheck_work_base",
                        lambda slot: (rechecks.append(slot.work_base_ref), real(slot)))
    monkeypatch.setattr(lock_manager, "wait_budget", lambda *_a, **_k: 0.0)
    with hold_group_lock(PROJECT, GID):
        assert env.provision() == "failed"               # blocked on G, still at start
    assert env.jobs()[-1]["phase"] == "start" and rechecks == ["feature-a"]

    real_run_git = git_service._run_git
    fetches = []

    def run_git(args, **kwargs):
        if list(args[:2]) == ["fetch", "origin"]:
            fetches.append(1)
            if len(fetches) == 1:
                return subprocess.CompletedProcess(args, 1, "", "injected")
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(git_service, "_run_git", run_git)
    assert env.provision() == "failed"                   # start ran again, then fetch failed
    assert rechecks == ["feature-a", "feature-a"]
    assert env.jobs()[-1]["phase"] == "intent_recorded"
    with env.store.transaction():
        env.store._execute("UPDATE operation_job SET available_at = ? WHERE group_id = ?",
                           ["2000-01-01T00:00:00+00:00", GID])
    assert env.provision() == "ok"                       # resumed after start: no recheck
    assert rechecks == ["feature-a", "feature-a"]
