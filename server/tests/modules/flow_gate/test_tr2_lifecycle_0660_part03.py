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

def test_t6_blocked_cancel_keeps_tr2_revert_pending_until_the_retry_succeeds(env, monkeypatch):
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.services import git_service
    group = make_group("0608")
    client = full_api()
    repo = env["repo"]
    t2, doc_id = _approved_tr2(env, group, client)
    approved_head = git(repo, "rev-parse", "HEAD").strip()
    source = repo / "src" / "app.txt"
    assert source.read_bytes() == b"greeting = 'hi'\ncount = 1\n"
    monkeypatch.setattr(git_service, "CANCEL_LOCK_WAIT_SEC", 0)
    monkeypatch.setattr("modules.flow_gate.services.git.lock.LOCK_WAIT_SEC", 0)

    with hold_group_lock(PROJECT, group["group_id"]):
        result = _reopen(doc_id)

    # The rewind stands (D0005 K8) but the TR2 is not reopened for editing.
    assert result["tr_commit_cancel"]["blocked_reason"] == "git_busy"
    assert result["tr_commit_cancel"]["retryable"] is True
    assert result["revert_pending"] == [doc_id]
    assert doc_row(doc_id)["doc_review_status"] == "pending_review"
    assert [r["state"] for r in db_ledger.live_rows(group["group_id"], [doc_id])] == ["live"]
    assert git(repo, "rev-parse", "HEAD").strip() == approved_head

    state = view(client, doc_id)
    assert state["mutation"] == {"allowed": False, "reason": "revert_pending"}
    assert (state["readiness"]["ready"], state["readiness"]["code"]) == (False, "tr2_revert_pending")
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 409 and saved.json()["code"] == "tr2_revert_pending", saved.text
    restored = client.post(f"/api/v1/documents/{doc_id}/tr2/recovery/restore",
                           json={"revision_no": 1, "expected_revision": 1})
    assert restored.status_code == 409 and restored.json()["code"] == "tr2_revert_pending", restored.text
    refused = approve(client, doc_id, 1, request_key="0660:0608:blocked")
    assert refused.status_code == 409 and refused.json()["code"] == "tr2_revert_pending"
    assert doc_row(doc_id)["revision_no"] == 1
    assert git(repo, "rev-parse", "HEAD").strip() == approved_head
    assert git(repo, "status", "--porcelain") == ""

    # Lock released: the product's own retry path cancels the commit, then editing works.
    retry = client.post(f"/api/v1/documents/workflow/{doc_id}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert [c["doc_id"] for c in retry.json()["tr_commit_cancel"]["canceled"]] == [doc_id]
    assert db_ledger.live_rows(group["group_id"], [doc_id]) == []
    assert source.read_bytes() == b"greeting = 'hello'\ncount = 1\n"
    assert git(repo, "rev-parse", "HEAD").strip() != approved_head
    state = view(client, doc_id)
    assert state["mutation"] == {"allowed": True, "reason": None}
    assert state["readiness"]["code"] == "tr2_history_revision_required"

    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 2, saved.text
    assert view(client, doc_id)["readiness"]["ready"] is True
    assert approve(client, doc_id, 2, request_key="0660:0608:after").status_code == 200
    assert source.read_bytes() == b"greeting = 'hi'\ncount = 1\n"
    assert git(repo, "status", "--porcelain") == ""
    rows = [r for r in db_ledger.list_by_group(group["group_id"]) if r["doc_id"] == doc_id]
    assert sorted(r["state"] for r in rows) == ["canceled", "live"]
def test_t6_contaminated_state_without_cancel_is_guarded(env):
    """The chat.0006 shape: reopened status, commit never canceled. Guarded, not editable."""
    from modules.flow_gate.db import documents as db_docs
    group = make_group("0609")
    client = full_api()
    t2, doc_id = _approved_tr2(env, group, client)
    db_docs.update(doc_id, {"doc_review_status": "pending_review"})
    state = view(client, doc_id)
    assert state["mutation"]["reason"] == "revert_pending"
    assert state["readiness"]["code"] == "tr2_revert_pending"
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 409 and saved.json()["code"] == "tr2_revert_pending"
def test_t5_unblocked_reopen_reports_nothing_pending(env):
    group = make_group("0610")
    client = full_api()
    _t2, doc_id = _approved_tr2(env, group, client)
    result = _reopen(doc_id)
    assert result["revert_pending"] == []
    assert [c["doc_id"] for c in result["tr_commit_cancel"]["canceled"]] == [doc_id]
    again = approve(client, doc_id, 1, request_key="0660:0610:same")
    assert again.status_code == 409 and again.json()["code"] == "tr2_history_revision_required"
def test_s2_reopen_names_the_documents_it_deleted(env):
    from modules.flow_gate.db import documents as db_docs
    group = make_group("0611")
    client = full_api()
    _t2, doc_id = _approved_tr2(env, group, client)
    ac_id = f"{group['group_id']}.0099-AC"
    db_docs.create({"doc_id": ac_id, "project_id": PROJECT, "type_code": "AC", "seq": 99,
                    "title": "Final Approval", "group_id": group["group_id"], "module": MODULE,
                    "owner_id": USER, "revision_no": 0})
    result = _reopen(doc_id)
    assert result["deleted"] == [ac_id]
    assert doc_row(ac_id) is None
