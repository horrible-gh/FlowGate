"""Independently selectable scenarios; shared fixtures remain in test_tr2_time_machine_reapproval_0658.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_tr2_time_machine_reapproval_0658 import (
    APPROVED,
    BASE,
    PROJECT,
    _approved_then_advanced,
    _assert_recovery_block,
    _hold_lock_and_rewind,
    _interrupted_restore,
    _ledger,
    _resave_and_approve,
    _rewind,
    approve,
    auto_approved_t2,
    doc_row,
    env,
    full_api,
    git,
    make_group,
    next_empty,
    repo,
    store,
    submit_tr2,
    tr2_body,
    view,
)

def test_rc_a_cancel_retry_reaches_a_pending_tr2_the_return_point_does_not_name(env, monkeypatch):
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.documents import tr2_service
    group = make_group("0804")
    client = full_api()
    repo = env["repo"]
    t2, tr2, later = _approved_then_advanced(env, group, client)

    result = _hold_lock_and_rewind(group, client, later, tr2, monkeypatch)
    assert result["tr_commit_cancel"]["blocked_reason"] == "git_busy"
    assert result["revert_pending"] == [tr2]
    # The return point that named the TR2 is gone (a pre-0660 group, or one cleared since):
    # the guard still holds the whole group on the ledger's word.
    rp = db_rp.get_by_group(group["group_id"])
    db_rp.delete(rp["id"])
    assert tr2_service.group_revert_pending(group["group_id"]) == [tr2]
    blocked = client.post("/api/v1/documents/workflow/reopen",
                          json={"doc_id": later, "target_seq": doc_row(t2)["seq"]})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "workflow_revert_pending"

    retry = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert [c["doc_id"] for c in retry.json()["tr_commit_cancel"]["canceled"]] == [tr2]
    assert tr2_service.group_revert_pending(group["group_id"]) == []
    assert (repo / "src" / "app.txt").read_bytes() == BASE
    assert git(repo, "status", "--porcelain") == ""
    assert view(client, tr2)["mutation"] == {"allowed": True, "reason": None}

    _resave_and_approve(client, t2, tr2, 1, "0658:0804:new")
    assert (repo / "src" / "app.txt").read_bytes() == APPROVED
    assert [row["state"] for row in _ledger(group, tr2)] == ["canceled", "live"]
def test_rc_a_contaminated_pending_tr2_without_any_rewind_is_released_by_the_retry(env):
    """The chat.0006 shape exactly: reopened status, live commit, no return point at all."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_return_points as db_rp
    group = make_group("0805")
    client = full_api()
    repo = env["repo"]
    t2, tr2, _later = _approved_then_advanced(env, group, client)
    db_docs.update(tr2, {"doc_review_status": "pending_review"})
    assert db_rp.get_by_group(group["group_id"]) is None
    assert view(client, tr2)["mutation"]["reason"] == "revert_pending"

    retry = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert [c["doc_id"] for c in retry.json()["tr_commit_cancel"]["canceled"]] == [tr2]
    assert (repo / "src" / "app.txt").read_bytes() == BASE
    _resave_and_approve(client, t2, tr2, 1, "0658:0805:new")
    assert (repo / "src" / "app.txt").read_bytes() == APPROVED
def test_rc_a_retry_never_cancels_a_reapproved_tr2(env, monkeypatch):
    """Control: an approved TR2's live commit is not a revert_pending target."""
    group = make_group("0806")
    client = full_api()
    repo = env["repo"]
    _t2, tr2, _later = _approved_then_advanced(env, group, client)
    head = git(repo, "rev-parse", "HEAD").strip()
    retry = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert retry.json()["tr_commit_cancel"]["canceled"] == []
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert [row["state"] for row in _ledger(group, tr2)] == ["live"]
