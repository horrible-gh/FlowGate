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

def test_rc_b_rewind_settles_an_interrupted_reapply_before_cancelling(env, monkeypatch):
    from modules.flow_gate.db import tr_history_recovery as db_recovery
    group = make_group("0807")
    client = full_api()
    repo = env["repo"]
    source = repo / "src" / "app.txt"
    t2, tr2, later = _approved_then_advanced(env, group, client)
    _rewind(client, later, tr2)
    _interrupted_restore(client, group, tr2, monkeypatch)
    assert source.read_bytes() == APPROVED  # the reapply commit is in the tree

    result = _rewind(client, later, tr2)
    assert not db_recovery.has_unresolved(group["group_id"])
    assert [c["doc_id"] for c in result["tr_commit_cancel"]["canceled"]] == [tr2]
    first, recovered = _ledger(group, tr2)
    assert recovered["restored_from_id"] == first["id"]  # the recovery's ledger row
    assert (first["state"], recovered["state"]) == ("canceled", "canceled")
    assert source.read_bytes() == BASE
    assert git(repo, "status", "--porcelain") == ""

    _resave_and_approve(client, t2, tr2, 1, "0658:0807:new")
    assert source.read_bytes() == APPROVED
    assert [row["state"] for row in _ledger(group, tr2)] == ["canceled", "canceled", "live"]
def test_rc_b_cancel_retry_settles_the_recovery_a_busy_rewind_could_not(env, monkeypatch):
    from modules.flow_gate.db import tr_history_recovery as db_recovery
    group = make_group("0808")
    client = full_api()
    repo = env["repo"]
    source = repo / "src" / "app.txt"
    t2, tr2, later = _approved_then_advanced(env, group, client)
    _rewind(client, later, tr2)
    _interrupted_restore(client, group, tr2, monkeypatch)

    # The rewind runs while another operation holds the lock: neither the recovery nor the
    # cancel can run, the reapply commit stays in the tree and approval is refused.
    busy = _hold_lock_and_rewind(group, client, later, tr2, monkeypatch)
    assert busy["tr_commit_cancel"]["blocked_reason"] == "history_recovery_required"
    assert db_recovery.has_unresolved(group["group_id"])
    assert source.read_bytes() == APPROVED
    # The read model names the refusal approval would give, never "ready".
    readiness = view(client, tr2)["readiness"]
    assert (readiness["ready"], readiness["code"]) == (False, "tr_history_recovery_required")
    refused = approve(client, tr2, 1, request_key="0658:0808:refused")
    assert refused.status_code == 409
    assert refused.json()["code"] == "tr_history_recovery_required"

    retry = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert not db_recovery.has_unresolved(group["group_id"])
    assert [c["doc_id"] for c in retry.json()["tr_commit_cancel"]["canceled"]] == [tr2]
    assert source.read_bytes() == BASE
    assert git(repo, "status", "--porcelain") == ""

    _resave_and_approve(client, t2, tr2, 1, "0658:0808:new")
    assert source.read_bytes() == APPROVED
def test_rc_b_unprovable_recovery_still_blocks_the_cancel(env):
    """Fail-closed: a journal recovery cannot prove stays unresolved, and neither the
    rewind's cancel nor its retry reverts anything over it."""
    from modules.flow_gate.db import tr_history_recovery as db_recovery
    group = make_group("0809")
    client = full_api()
    repo = env["repo"]
    _t2, tr2, later = _approved_then_advanced(env, group, client)
    live = _ledger(group, tr2)[0]
    head = git(repo, "rev-parse", "HEAD").strip()
    # A journal whose source row is not canceled: recovery has nothing it may prove.
    db_recovery.create_prepared(project_id=PROJECT, group_id=group["group_id"], doc_id=tr2,
                                source_ledger_row_id=int(live["id"]), pre_git_head_sha=head)

    result = _rewind(client, later, tr2)
    assert result["tr_commit_cancel"]["blocked_reason"] == "history_recovery_required"
    assert result["revert_pending"] == [tr2]
    retry = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert retry.json()["tr_commit_cancel"]["blocked_reason"] == "history_recovery_required"
    assert db_recovery.has_unresolved(group["group_id"])
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert [row["state"] for row in _ledger(group, tr2)] == ["live"]
def test_rc_b_unsettled_reapply_without_a_live_row_fails_closed(env, monkeypatch):
    """No live row to cancel is not "nothing to cancel" while the reapply journal is open.

    The interrupted reapply's commit is in the tree and the ledger has no row for it. With
    the project lock held by another operation recovery cannot run, so the rewind, the
    retry over the return point and the retry with no return point at all must each refuse
    with ``history_recovery_required`` — never the quiet no-op — and change nothing.
    """
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import tr_history_recovery as db_recovery
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.services import git_service
    group = make_group("0810")
    client = full_api()
    repo = env["repo"]
    source = repo / "src" / "app.txt"
    t2, tr2, later = _approved_then_advanced(env, group, client)
    _rewind(client, later, tr2)
    _interrupted_restore(client, group, tr2, monkeypatch)
    head = git(repo, "rev-parse", "HEAD").strip()

    monkeypatch.setattr(git_service, "CANCEL_LOCK_WAIT_SEC", 0)
    monkeypatch.setattr("modules.flow_gate.services.git.lock.LOCK_WAIT_SEC", 0)
    holder = "op:lock-fixture-0810"
    assert db_git.try_acquire_lock(PROJECT, holder)
    try:
        result = _rewind(client, later, tr2)
        _assert_recovery_block(result["tr_commit_cancel"])
        assert result["revert_pending"] == []  # no live row: only the journal says so

        retry = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
        assert retry.status_code == 200, retry.text
        _assert_recovery_block(retry.json()["tr_commit_cancel"])

        db_rp.delete(db_rp.get_by_group(group["group_id"])["id"])
        bare = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
        assert bare.status_code == 200, bare.text
        _assert_recovery_block(bare.json()["tr_commit_cancel"])

        assert db_recovery.has_unresolved(group["group_id"])
        assert git(repo, "rev-parse", "HEAD").strip() == head
        assert source.read_bytes() == APPROVED
        assert [row["state"] for row in _ledger(group, tr2)] == ["canceled"]
        assert view(client, tr2)["readiness"]["code"] == "tr_history_recovery_required"
    finally:
        db_git.release_lock(PROJECT, holder)

    # Not a dead end: once the lock is free the same retry settles the journal (its row is
    # now live on a reopened TR2, so revert_pending names it) and cancels the commit.
    settled = client.post(f"/api/v1/documents/workflow/{tr2}/return-point/cancel-commits")
    assert settled.status_code == 200, settled.text
    assert [c["doc_id"] for c in settled.json()["tr_commit_cancel"]["canceled"]] == [tr2]
    assert not db_recovery.has_unresolved(group["group_id"])
    assert source.read_bytes() == BASE
    assert git(repo, "status", "--porcelain") == ""
    _resave_and_approve(client, t2, tr2, 1, "0658:0810:new")
    assert source.read_bytes() == APPROVED
