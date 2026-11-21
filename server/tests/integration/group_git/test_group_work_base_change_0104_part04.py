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

@pytest.mark.parametrize("stage", _STORE_FAULTS)
def test_patch_store_error_in_either_step_is_the_structured_500(env, monkeypatch, stage):
    _arm_store_fault(env, monkeypatch, stage)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert _error(response)["code"] == "group_work_base_update_failed"
    assert env.stored() == "feature-a"
    assert env.jobs() == []
@pytest.mark.parametrize("stage", _STORE_FAULTS)
def test_v01_store_error_in_either_step_is_an_errors_entry(env, monkeypatch, tmp_path, stage):
    _stub_outbox_io(monkeypatch, tmp_path)
    _arm_store_fault(env, monkeypatch, stage)
    result = _v01("feature-b")
    assert _v01_codes(result) == ["group_work_base_update_failed"]
    assert env.stored() == "feature-a"
def test_store_error_conversion_keeps_the_git_service_error_codes_and_order(env, monkeypatch):
    """A GitServiceError raised in step 1 still wins over a later repository error."""
    _arm_store_fault(env, monkeypatch, "row_lock")
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: True)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_disposed")
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: False)
    response = _patch({"work_base_ref": "missing", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (404, "group_work_base_not_found")
    assert env.stored() == "feature-a"
def test_patch_conditional_save_missing_its_row_is_changed(env, monkeypatch):
    monkeypatch.setattr(group_work_base, "_save_work_base_ref", lambda *_a: 0)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_changed")
    assert env.stored() == "feature-a"
def test_patch_trusts_the_group_row_not_the_body_project(env):
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a",
                       "project_id": "p"})
    assert response.status_code == 200
    assert response.json()["project_id"] == PROJECT
def test_group_row_lock_keeps_updated_at_and_reads_the_row(env):
    before = db_groups.get_by_id(GID)
    with env.store.transaction():
        row = group_work_base.lock_group_row(GID)
        assert group_work_base.lock_group_row("connected.default.9999") is None
    assert row["work_base_ref"] == "feature-a" and row["project_id"] == PROJECT
    assert db_groups.get_by_id(GID)["updated_at"] == before["updated_at"]
    with pytest.raises(RuntimeError):
        group_work_base.lock_group_row(GID)
