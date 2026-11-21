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

def test_recheck_catches_a_project_base_change_for_a_legacy_group(env, monkeypatch):
    """§3.3.4: the project base is not serialized with the row lock, but a NULL group's
    effective ref that moved under a queued job still fails the recheck."""
    with env.store.transaction():
        env.store._execute("UPDATE groups SET work_base_ref = NULL WHERE group_id = ?", [GID])
    monkeypatch.setattr(git_service, "src_root", lambda _name, branch: (
        env.repo if branch in ("main", "feature-b") else env.slots / branch))
    job = env.register_job()
    assert worktree_provision._payload(job)["work_base_ref"] == "main"
    env.cfg["base_branch"] = "feature-b"
    before = env.snapshot()
    assert worktree_provision.attempt(job) == "failed"
    assert env.jobs()[-1]["last_error_code"] == "work_base_changed"
    assert env.snapshot() == before and not env.wt.exists()
def test_f_v01_existing_group_follows_the_same_boundary(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    # success path: common change without the expected compare
    assert _v01("feature-b")["status"] == "success"
    assert env.stored() == "feature-b"
    # (b) a registered job
    job = env.register_job()
    assert _v01_codes(_v01("feature-a")) == ["group_work_base_busy"]
    assert env.stored() == "feature-b"
    with env.store.transaction():
        env.store._execute("UPDATE operation_job SET status = 'cancelled' WHERE job_id = ?",
                           [job["job_id"]])
    # (d) every active status
    for status in ACTIVE_STATUSES:
        active = env.register_job(status)
        assert _v01_codes(_v01("feature-a")) == ["group_work_base_busy"], status
        with env.store.transaction():
            env.store._execute("UPDATE operation_job SET status = 'cancelled' WHERE job_id = ?",
                               [active["job_id"]])
    assert env.stored() == "feature-b"
def test_f_v01_race_fails_the_provision_at_the_recheck(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    before = env.snapshot()
    result, seen = _race(monkeypatch, lambda: _v01("feature-b"))
    assert result["status"] == "success", result
    assert env.stored() == "feature-b"
    _assert_race_lost_without_git_effect(env, seen, before)
    _assert_next_provision_uses(env, "feature-b")
def test_g_job_settling_after_creating_the_branch_locks_the_patch(env, monkeypatch):
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=True)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_locked")
    assert _error(response)["details"] == {"lock_evidence": ["group_branch_exists"]}
    assert calls == ["feature-b"]                      # the second run stopped at the lock
    assert env.stored() == "feature-a"
    assert _git(env.repo, "rev-parse", BRANCH) == env.sha("feature-a")
    view = _get().json()
    assert (view["work_base_ref"], view["reason"]) == ("feature-a", "history_locked")
def test_g_job_failing_before_the_branch_still_lets_the_patch_change(env, monkeypatch):
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=False)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 200, response.json()
    assert calls == ["feature-b", "feature-b"]         # rolled back once, read again
    assert env.stored() == "feature-b"
    _assert_next_provision_uses(env, "feature-b")
def test_g_v01_job_settling_after_creating_the_branch_is_locked(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=True)
    result = _v01("feature-b")
    assert _v01_codes(result) == ["group_work_base_locked"]
    assert result["errors"][0]["details"] == {"lock_evidence": ["group_branch_exists"]}
    assert calls == ["feature-b"]
    assert env.stored() == "feature-a"
