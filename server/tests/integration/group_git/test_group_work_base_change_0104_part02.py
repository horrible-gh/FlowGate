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

def test_cleanup_leaving_only_a_succeeded_job_locks(env):
    env.register_job("succeeded")
    assert _get().json()["lock_evidence"] == ["provision_succeeded"]
    assert _patch({"work_base_ref": "feature-b",
                   "expected_work_base_ref": "feature-a"}).status_code == 409
@pytest.mark.parametrize("where", ["local", "origin"])
def test_existing_group_branch_locks(env, where):
    if where == "local":
        _git(env.repo, "branch", BRANCH, "main")
    else:
        _git(env.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", env.sha("main"))
    assert _get().json()["lock_evidence"] == ["group_branch_exists"]
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert _error(response)["code"] == "group_work_base_locked"
@pytest.mark.parametrize("trace", ["failed_job", "cancelled_job", "failure_ledger", "backfill"])
def test_traces_without_git_work_stay_editable(env, trace):
    if trace in ("failed_job", "cancelled_job"):
        env.register_job(trace.split("_")[0])
    elif trace == "failure_ledger":
        git_service.db_git.upsert_provision_failure(GID, PROJECT, BRANCH, "fetch_failed")
    else:
        env.state(work_base_state="unverified")
    view = _get().json()
    assert (view["lock_evidence"], view["reason"], view["work_base_editable"]) == ([], None, True)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 200, response.json()
    assert env.stored() == "feature-b"
def test_groups_list_lock_flag_keeps_the_ledger_only_judgment(env):
    env.state(work_base_sha="0" * 40, work_base_state="verified")
    listed = _client().get(f"/api/v1/groups?project_id={PROJECT}")
    assert listed.status_code == 200
    row = next(g for g in listed.json()["groups"] if g["group_id"] == GID)
    assert row["work_base_locked"] is False
    assert git_service.locked_group_ids(PROJECT) == set()
    assert _get().json()["work_base_locked"] is True
    with env.store.transaction():
        env.store._execute("UPDATE group_git_state SET worktree_registered = 1 WHERE group_id = ?",
                           [GID])
    row = next(g for g in _client().get(f"/api/v1/groups?project_id={PROJECT}").json()["groups"]
               if g["group_id"] == GID)
    assert row["work_base_locked"] is True
def test_patch_changes_an_unstarted_group_and_reads_back(env):
    before = env.snapshot()
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in ("group_id", "work_base_ref", "effective_work_base_ref",
                                 "work_base_locked", "work_base_editable", "reason")} == {
        "group_id": GID, "work_base_ref": "feature-b", "effective_work_base_ref": "feature-b",
        "work_base_locked": False, "work_base_editable": True, "reason": None}
    assert body["updated_at"] == db_groups.get_by_id(GID)["updated_at"]
    view = _get().json()
    assert (view["work_base_ref"], view["effective_work_base_ref"]) == ("feature-b", "feature-b")
    assert env.snapshot() == before                      # no checkout switch, no Git write
def test_patch_compares_a_legacy_null_with_null(env):
    assert env.stored(OTHER_GID) is None
    assert _get(group_id=OTHER_GID).json()["effective_work_base_ref"] == "main"
    mismatch = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "main"},
                      group_id=OTHER_GID)
    assert _error(mismatch)["code"] == "group_work_base_changed"
    assert env.stored(OTHER_GID) is None
    ok = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": None}, group_id=OTHER_GID)
    assert ok.status_code == 200
    assert env.stored(OTHER_GID) == "feature-b"
