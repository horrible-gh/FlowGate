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

def test_s3_step_after_the_reopened_head_cannot_be_approved(env):
    from modules.flow_gate.documents import tr2_service
    group = make_group("0612")
    client = full_api()
    _t2, tr2_id = _approved_tr2(env, group, client)
    created = next_empty(client, group, tr2_id, type_code="T", title="반영지시")
    assert created.status_code == 201, created.text
    later = created.json()["doc_id"]

    _reopen(tr2_id)
    head_before = tr2_service.effective_head_for(tr2_id)
    assert head_before["result_doc_id"] == tr2_id
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "헤드" in refused.json()["detail"]
    assert doc_row(later)["doc_review_status"] == "pending_review"
    assert tr2_service.effective_head_for(tr2_id)["id"] == head_before["id"]
def test_rc1_save_with_unready_worktree_answers_only_public_detail_keys(env):
    """An existing TR2 saved while its worktree is unregistered: the internal SRC_ROOT
    name (``worktree_unregistered``) stays in the log, the response names the public enum."""
    from modules.flow_gate.db import git_integration as db_git
    group = make_group("0613")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    db_git.unregister_worktree(group["group_id"])

    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 503, saved.text
    body = assert_public(saved)
    assert (body["code"], body["retryable"]) == ("tr2_git_unavailable", True)
    assert body["details"] == {"loc": "source_root", "reason": "worktree_provisioning"}
    assert "worktree_unregistered" not in saved.text
    assert doc_row(doc_id)["revision_no"] == 1
def test_rc1_public_details_drop_every_undocumented_key():
    from modules.flow_gate.documents.tr2_errors import error_payload
    for code, reason in (("tr2_git_unavailable", "worktree_provisioning"),
                         ("tr2_source_root_missing", "project_source_missing"),
                         ("tr2_worktree_provision_failed", "worktree_provision_failed"),
                         ("tr2_revert_pending", "commit_cancel_blocked")):
        payload = error_payload(code, details={
            "loc": "source_root", "reason": reason, "source_root": "resolution_error",
            "provision_error": "fatal: x", "group_id": "g"})
        assert payload["details"] == {"loc": "source_root", "reason": reason}, code
def test_rc3_second_reopen_while_cancel_pending_is_refused_until_the_retry(env, monkeypatch):
    """Blocked cancel, then a rewind to an EARLIER step: refused, and nothing in the group
    advances (approve / next step / final approval / forward restore / workflow edit /
    reapply) until the cancel retry succeeds. Only then does the earlier rewind run."""
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.documents import tr2_service
    group = make_group("0614")
    client = full_api()
    repo = env["repo"]
    t2, tr2_id = _approved_tr2(env, group, client)
    created = next_empty(client, group, tr2_id, type_code="T", title="반영지시")
    assert created.status_code == 201, created.text
    later = created.json()["doc_id"]
    approved_head = git(repo, "rev-parse", "HEAD").strip()

    _blocked_reopen(group, tr2_id, monkeypatch)
    head = tr2_service.effective_head_for(tr2_id)
    rp_before = db_rp.get_by_group(group["group_id"])
    steps_before = doc_row(group["root"]).get("workflow_steps")
    assert tr2_service.group_revert_pending(group["group_id"]) == [tr2_id]
    t2_seq = doc_row(t2)["seq"]

    # A second rewind to the earlier T2 step over HTTP: identified 409, nothing moved.
    again = client.post("/api/v1/documents/workflow/reopen",
                        json={"doc_id": tr2_id, "target_seq": t2_seq})
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "workflow_revert_pending"
    assert again.json()["error"]["details"] == {"doc_ids": [tr2_id]}
    assert doc_row(t2)["doc_review_status"] == "approved"
    assert doc_row(tr2_id)["doc_review_status"] == "pending_review"
    assert tr2_service.effective_head_for(tr2_id)["id"] == head["id"]
    assert db_rp.get_by_group(group["group_id"]) == rp_before
    assert [r["state"] for r in db_ledger.live_rows(group["group_id"], [tr2_id])] == ["live"]
    assert git(repo, "rev-parse", "HEAD").strip() == approved_head

    # No other group workflow mutation gets through either.
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "커밋 취소" in refused.json()["detail"]
    assert doc_row(later)["doc_review_status"] == "pending_review"
    own = approve(client, tr2_id, 1, request_key="0660:0614:pending")
    assert own.status_code == 409 and own.json()["code"] == "tr2_revert_pending", own.text
    nxt = next_empty(client, group, tr2_id, type_code="T", title="또 다른 지시")
    assert nxt.status_code == 409, nxt.text
    assert nxt.json()["error"]["code"] == "workflow_revert_pending"
    for path, payload in (
        ("/api/v1/documents/workflow/final-approval", {"doc_id": group["root"]}),
        ("/api/v1/documents/workflow/restore", {"doc_id": tr2_id, "destination_seq": 99}),
        (f"/api/v1/documents/workflow/{tr2_id}/return-point/reapply-commits", {}),
    ):
        resp = client.post(path, json=payload)
        assert resp.status_code == 409, (path, resp.text)
        assert resp.json()["error"]["code"] == "workflow_revert_pending", path
    edit = client.patch(f"/api/v1/documents/{group['root']}/workflow", json={"workflow_steps": []})
    assert edit.status_code == 409, edit.text
    assert edit.json()["error"]["code"] == "workflow_revert_pending"
    assert doc_row(group["root"]).get("workflow_steps") == steps_before
    assert tr2_service.effective_head_for(tr2_id)["id"] == head["id"]
    assert db_rp.get_by_group(group["group_id"]) == rp_before

    # The one path left open: the cancel retry. It clears the group, and the earlier
    # rewind that was refused above now runs normally.
    retry = client.post(f"/api/v1/documents/workflow/{tr2_id}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert [c["doc_id"] for c in retry.json()["tr_commit_cancel"]["canceled"]] == [tr2_id]
    assert tr2_service.group_revert_pending(group["group_id"]) == []
    assert git(repo, "status", "--porcelain") == ""
    again = client.post("/api/v1/documents/workflow/reopen",
                        json={"doc_id": tr2_id, "target_seq": t2_seq})
    assert again.status_code == 200, again.text
    assert again.json()["revert_pending"] == []
    assert doc_row(t2)["doc_review_status"] == "pending_review"
