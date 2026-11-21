"""Independently selectable scenarios; shared fixtures remain in test_tr2_lifecycle_0660.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_tr2_lifecycle_0660 import (
    MODULE,
    PROJECT,
    USER,
    USER_ROW,
    _approved_tr2,
    _blocked_reopen,
    _later_step,
    _reopen,
    _unprovision,
    api,
    approve,
    assert_nothing_left,
    assert_public,
    auto_approved_t2,
    doc_row,
    edit_spec,
    env,
    full_api,
    git,
    group_doc_ids,
    group_storage_files,
    hold_group_lock,
    make_group,
    next_empty,
    pytest,
    repo,
    revision_rows,
    slot,
    store,
    submit_tr2,
    tr2_body,
    view,
)

def test_t3_git_off_missing_source_is_not_retryable_until_the_source_exists(env):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.storage import paths as storage_paths
    group = make_group("0605")
    client = full_api()
    t2 = auto_approved_t2(group)
    db_git.upsert_config(PROJECT, {"repo_url": "https://example.invalid/tr2.git",
                                   "enabled": False, "base_branch": "main", "secret_enc": None})
    docs_before = group_doc_ids(group)

    pre = client.get("/api/v1/documents/next-empty/preflight",
                     params={"prev_doc_id": t2, "type_code": "TR2"})
    assert pre.status_code == 200, pre.text
    assert pre.json()["ok"] is False
    assert pre.json()["error"]["code"] == "tr2_source_root_missing"
    assert pre.json()["error"]["retryable"] is False

    for _attempt in range(3):  # retrying without placing the source never helps
        resp = next_empty(client, group, t2)
        assert resp.status_code == 409, resp.text
        body = assert_public(resp)
        assert (body["code"], body["retryable"]) == ("tr2_source_root_missing", False)
        assert body["details"]["reason"] == "project_source_missing"
        assert_nothing_left(group, docs_before)

    source = storage_paths.src_root("TR2 E2E", "main")
    source.mkdir(parents=True, exist_ok=True)
    assert client.get("/api/v1/documents/next-empty/preflight",
                      params={"prev_doc_id": t2, "type_code": "TR2"}).json() == {"ok": True}
    resp = next_empty(client, group, t2)
    assert resp.status_code == 201, resp.text
def test_t3_git_on_unready_worktree_preflight_defers_to_create(env):
    group = make_group("0606")
    client = full_api()
    t2 = auto_approved_t2(group)
    _unprovision(group)
    pre = client.get("/api/v1/documents/next-empty/preflight",
                     params={"prev_doc_id": t2, "type_code": "TR2"})
    assert pre.json() == {"ok": True, "provisioning": True}
def test_public_reason_is_a_closed_enum():
    from modules.flow_gate.documents.tr2_errors import error_payload
    leaked = error_payload("tr2_git_unavailable", details={"reason": "fatal: C:/x stderr"})
    assert "reason" not in leaked["details"]
    kept = error_payload("tr2_git_unavailable", details={"reason": "git_busy"})
    assert (kept["retryable"], kept["details"]["reason"]) == (True, "git_busy")
    missing = error_payload("tr2_source_root_missing", details={"reason": "project_source_missing"})
    assert (missing["retryable"], missing["details"]["reason"]) == (False, "project_source_missing")
def test_t4_workflow_slot_conflict_is_an_identified_409(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    group = make_group("0607")
    client = full_api()
    t2 = auto_approved_t2(group)
    docs_before = group_doc_ids(group)

    def occupied(*, item_id, registered_doc_id, **_kw):
        raise pipeline_service.WorkflowSlotConflictError(
            item_id=item_id, existing_doc_id=f"{group['group_id']}.0009-TR2",
            requested_doc_id=registered_doc_id)

    monkeypatch.setattr(pipeline_service, "register_workflow_result", occupied)
    resp = next_empty(client, group, t2)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "workflow_slot_occupied"
    assert_nothing_left(group, docs_before)
