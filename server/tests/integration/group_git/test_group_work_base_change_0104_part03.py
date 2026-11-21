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

@pytest.mark.parametrize("body, status, code", [
    ({"expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": "", "expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": "   ", "expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": " feature-b", "expected_work_base_ref": "feature-a"}, 422,
     "group_work_base_invalid"),
    ({"work_base_ref": "bad..name", "expected_work_base_ref": "feature-a"}, 422,
     "group_work_base_invalid"),
    ({"work_base_ref": 7, "expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": "feature-b"}, 422, "group_work_base_expected_required"),
    ({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-b"}, 409,
     "group_work_base_changed"),
    ({"work_base_ref": "missing", "expected_work_base_ref": "feature-a"}, 404,
     "group_work_base_not_found"),
    ({"work_base_ref": "remote-only", "expected_work_base_ref": "feature-a"}, 409,
     "group_work_base_remote_only"),
    ({"work_base_ref": "owned", "expected_work_base_ref": "feature-a"}, 409,
     "group_work_base_internal"),
    ({"work_base_ref": "blob-ref", "expected_work_base_ref": "feature-a"}, 422,
     "group_work_base_not_commit"),
])
def test_patch_input_and_validation_codes(env, body, status, code):
    _git(env.repo, "update-ref", "refs/remotes/origin/remote-only", env.sha("main"))
    with env.store.transaction():
        env.store._execute(
            "INSERT INTO group_git_state (group_id, project_id, branch, worktree_registered, "
            "status, created_at, updated_at) VALUES (?, ?, 'owned', 1, 'none', ?, ?)",
            [OTHER_GID, PROJECT, NOW, NOW])
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=env.repo, input="blob\n",
                          text=True, capture_output=True, check=True).stdout.strip()
    (env.repo / ".git" / "refs" / "heads" / "blob-ref").write_text(blob + "\n", encoding="utf-8")
    response = _patch(body)
    assert response.status_code == status, response.json()
    assert _error(response)["code"] == code
    assert env.stored() == "feature-a"
def test_patch_state_codes(env, monkeypatch):
    def code():
        response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
        return response.status_code, _error(response)["code"]

    assert _patch({"work_base_ref": "feature-b", "expected_work_base_ref": None},
                  group_id="connected.default.9999").status_code == 404
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: True)
    assert code() == (409, "group_disposed")
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: False)
    env.cfg["enabled"] = 0
    assert code() == (409, "group_work_base_git_disabled")
    env.cfg["enabled"] = 1
    monkeypatch.setattr(git_service, "_base_root_of", lambda _pid: env.repo.parent / "absent")
    assert code() == (409, "group_work_base_checkout_unavailable")
    assert env.stored() == "feature-a"
def test_patch_403_is_the_plain_string_of_the_group_apis(env):
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"}, WORKER)
    assert response.status_code == 403
    assert response.json()["detail"] == "project.group.manage permission required."
    assert env.stored() == "feature-a"
@pytest.mark.parametrize("content, field", [
    (None, "work_base_ref"),            # no body at all: the new ref is missing
    (b"", "work_base_ref"),
    (b"   ", "work_base_ref"),
    (b"null", "body"),
    (b"[]", "body"),
    (b'["feature-b", "feature-a"]', "body"),
    (b'"feature-b"', "body"),
    (b"7", "body"),
    (b"{not json", "body"),
    (b"\xff\xfe{", "body"),
])
def test_patch_body_that_is_not_an_object_is_the_structured_422(env, content, field):
    kwargs = {} if content is None else {
        "content": content, "headers": {"content-type": "application/json"}}
    response = _client().patch(f"/api/v1/groups/{GID}/work-base", **kwargs)
    assert response.status_code == 422
    detail = _error(response)
    assert (detail["code"], detail["details"]) == ("group_work_base_invalid", {"field": field})
    assert env.stored() == "feature-a"
def test_patch_body_errors_keep_403_first_and_the_other_group_apis_unchanged(env):
    denied = _client(WORKER).patch(f"/api/v1/groups/{GID}/work-base")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "project.group.manage permission required."
    # PUT /groups/{id} keeps its pydantic validation (the app's RequestValidationError list).
    put = _client().put(f"/api/v1/groups/{GID}", json={"graph_execution_limit": "x"})
    assert put.status_code == 422
    assert isinstance(put.json()["detail"], list)
    assert env.stored() == "feature-a"
def test_patch_store_failure_is_500_and_changes_nothing(env, monkeypatch):
    def boom(*_args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(group_work_base, "_save_work_base_ref", boom)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 500
    assert _error(response)["code"] == "group_work_base_update_failed"
    assert env.stored() == "feature-a"
