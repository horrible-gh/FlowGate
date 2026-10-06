"""TR2 re-approval after a Time Machine rewind (flowgate.default.0658 T0004), connected.

Same real boundaries as ``test_tr2_connected_e2e_0565``: SQLite with every migration, a
real Git worktree in scratch, the real HTTP routes. The rewind is the one the workflow
strip sends — ``POST /workflow/reopen`` from the step the group had already advanced to.

* L1 — the normal lifecycle, twice: approve → advance → rewind → the same revision is
  refused (the replay guard stays) → an unchanged spec saved as a new revision → approve.
* L2 — rewind, forward restore, rewind again: the second cancel takes the reapplied commit.
* L3 — rewind to the T2 step: the pair is re-pended and the TR2 re-approves after it.
* RC-A — a reopened TR2 whose approval commit is still live while the return point does
  not name it (the ``chat.0006`` shape): the cancel retry is the path ``revert_pending``
  leaves open, so it must reach that TR2 instead of answering "nothing to cancel".
* RC-B — an interrupted forward-restore reapply (Git commit, no ledger row): the rewind
  and the cancel retry settle it first, instead of reopening the TR2 over source it can
  never apply to again while approval is refused for an unresolved recovery. While one is
  unresolved the read model says so (``tr_history_recovery_required``) instead of "ready".
  When recovery cannot settle it and the TR2 has no live row, the rewind and every retry
  shape still refuse with ``history_recovery_required`` instead of "nothing to cancel".
"""
from __future__ import annotations

from test_tr2_connected_e2e_0565 import (  # noqa: F401 — fixtures are used by name
    PROJECT, approve, auto_approved_t2, env, git, make_group, repo, store, submit_tr2,
    tr2_body, view,
)
from test_tr2_lifecycle_0660 import doc_row, full_api, next_empty

APPROVED = b"greeting = 'hi'\ncount = 1\n"
BASE = b"greeting = 'hello'\ncount = 1\n"


def _ledger(group: dict, doc_id: str) -> list[dict]:
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    return [row for row in reversed(db_ledger.list_by_group(group["group_id"]))
            if row["doc_id"] == doc_id]


def _approved_then_advanced(env, group: dict, client) -> tuple[str, str, str]:
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    tr2 = submit_tr2(group, t2)
    assert approve(client, tr2, 1, request_key=f"0658:{group['code']}:r1").status_code == 200
    created = next_empty(client, group, tr2, type_code="T", title="다음 지시")
    assert created.status_code == 201, created.text
    return t2, tr2, created.json()["doc_id"]


def _rewind(client, from_doc: str, target_doc: str) -> dict:
    resp = client.post("/api/v1/documents/workflow/reopen",
                       json={"doc_id": from_doc, "target_seq": doc_row(target_doc)["seq"]})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _resave_and_approve(client, t2: str, tr2: str, revision: int, key: str) -> None:
    """The documented way back: the same spec saved as a new revision, then approved."""
    saved = client.put(f"/api/v1/documents/{tr2}/tr2",
                       json={"expected_revision": revision, "body": tr2_body(t2)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == revision + 1, saved.text
    state = view(client, tr2)
    assert state["readiness"]["ready"] is True, state["readiness"]
    assert state["body"]["baseline_fingerprint"] == state["derived"]["live_precheck"]["live_fingerprint"]
    resp = approve(client, tr2, revision + 1, request_key=key)
    assert resp.status_code == 200, resp.text


# ── L1–L3 the lifecycle the strip actually drives ──────────────────────────────────

def test_l1_rewind_from_the_next_step_then_reapprove_twice(env):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.services import tr2_file_policy
    group = make_group("0801")
    client = full_api()
    repo = env["repo"]
    source = repo / "src" / "app.txt"
    t2, tr2, later = _approved_then_advanced(env, group, client)

    for round_no, revision in ((1, 1), (2, 2)):
        approved_head = git(repo, "rev-parse", "HEAD").strip()
        result = _rewind(client, later, tr2)
        assert [c["doc_id"] for c in result["tr_commit_cancel"]["canceled"]] == [tr2]
        assert result["revert_pending"] == []
        canceled_head = git(repo, "rev-parse", "HEAD").strip()
        assert git(repo, "rev-parse", "HEAD^").strip() == approved_head  # one revert commit
        assert source.read_bytes() == BASE
        assert git(repo, "status", "--porcelain") == ""
        assert tr2_file_policy.managed_paths(group["group_id"]) == set()
        assert doc_row(later)["doc_review_status"] == "pending_review"

        state = view(client, tr2)
        assert state["mutation"] == {"allowed": True, "reason": None}
        assert (state["readiness"]["code"], state["readiness"]["reason"]) == (
            "tr2_history_revision_required", "revision_already_applied")
        same = approve(client, tr2, revision, request_key=f"0658:0801:same:{round_no}")
        assert same.status_code == 409 and same.json()["code"] == "tr2_history_revision_required"
        assert git(repo, "rev-parse", "HEAD").strip() == canceled_head

        _resave_and_approve(client, t2, tr2, revision, f"0658:0801:new:{round_no}")
        assert git(repo, "rev-parse", "HEAD^").strip() == canceled_head  # exactly one commit
        assert source.read_bytes() == APPROVED
        assert git(repo, "status", "--porcelain") == ""
        assert tr2_file_policy.managed_paths(group["group_id"]) == {"src/app.txt"}
        assert db_attempts.latest_success(tr2)["document_revision"] == revision + 1
        assert view(client, tr2)["history"]["source_history_state"] == "aligned"

    assert [row["state"] for row in _ledger(group, tr2)] == ["canceled", "canceled", "live"]
    assert [(row["document_revision"], row["state"]) for row in db_attempts.list_by_doc(tr2)] == [
        (3, "succeeded"), (2, "succeeded"), (1, "succeeded")]


def test_l2_forward_restore_then_a_second_rewind_cancels_the_reapplied_commit(env):
    group = make_group("0802")
    client = full_api()
    repo = env["repo"]
    source = repo / "src" / "app.txt"
    t2, tr2, later = _approved_then_advanced(env, group, client)

    _rewind(client, later, tr2)
    restored = client.post("/api/v1/documents/workflow/restore", json={"doc_id": tr2})
    assert restored.status_code == 200, restored.text
    assert [r["doc_id"] for r in restored.json()["tr_commit_restore"]["reapplied"]] == [tr2]
    assert doc_row(tr2)["doc_review_status"] == "approved"
    assert source.read_bytes() == APPROVED
    first, reapplied = _ledger(group, tr2)
    assert (first["state"], reapplied["state"]) == ("canceled", "live")
    assert reapplied["restored_from_id"] == first["id"]

    again = _rewind(client, later, tr2)
    assert [c["doc_id"] for c in again["tr_commit_cancel"]["canceled"]] == [tr2]
    assert source.read_bytes() == BASE
    assert [row["state"] for row in _ledger(group, tr2)] == ["canceled", "canceled"]
    assert view(client, tr2)["readiness"]["code"] == "tr2_history_revision_required"

    _resave_and_approve(client, t2, tr2, 1, "0658:0802:new")
    assert source.read_bytes() == APPROVED
    assert git(repo, "status", "--porcelain") == ""
    assert [row["state"] for row in _ledger(group, tr2)] == ["canceled", "canceled", "live"]


def test_l3_rewind_to_the_t2_step_then_reapprove_t2_and_tr2(env):
    group = make_group("0803")
    client = full_api()
    repo = env["repo"]
    t2, tr2, later = _approved_then_advanced(env, group, client)

    result = _rewind(client, later, t2)
    assert sorted(result["reopened"]) == sorted([t2, tr2, later])
    assert [c["doc_id"] for c in result["tr_commit_cancel"]["canceled"]] == [tr2]
    refused = approve(client, tr2, 1, request_key="0658:0803:early")
    assert refused.status_code == 409, refused.text  # T2 is the head again

    assert approve(client, t2, int(doc_row(t2)["revision_no"] or 0)).status_code == 200
    _resave_and_approve(client, t2, tr2, 1, "0658:0803:new")
    assert (repo / "src" / "app.txt").read_bytes() == APPROVED
    assert git(repo, "status", "--porcelain") == ""


# ── RC-A the cancel retry reaches every TR2 the revert_pending guard holds ────────

def _hold_lock_and_rewind(group: dict, client, later: str, tr2: str, monkeypatch) -> dict:
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service
    monkeypatch.setattr(git_service, "CANCEL_LOCK_WAIT_SEC", 0)
    monkeypatch.setattr("modules.flow_gate.services.git.lock.LOCK_WAIT_SEC", 0)
    holder = f"op:lock-fixture-{group['code']}"
    assert db_git.try_acquire_lock(PROJECT, holder)
    try:
        return _rewind(client, later, tr2)
    finally:
        db_git.release_lock(PROJECT, holder)


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


# ── RC-B an interrupted reapply is settled before the cancel reads its targets ─────

def _interrupted_restore(client, group: dict, tr2: str, monkeypatch) -> None:
    """Forward restore whose reapply commit lands but whose ledger finalize never does."""
    from modules.flow_gate.db import tr_history_recovery as db_recovery
    from modules.flow_gate.services import tr_commit_service
    with monkeypatch.context() as patch:
        def crash(*_a, **_kw):
            raise RuntimeError("process died before the ledger row")
        patch.setattr(tr_commit_service, "_finalize_reapply", crash)
        patch.setattr(tr_commit_service, "_compensate_reapply", lambda *_a, **_kw: False)
        restored = client.post("/api/v1/documents/workflow/restore", json={"doc_id": tr2})
    assert restored.status_code == 200, restored.text
    assert restored.json()["tr_commit_restore"]["blocked_reason"] == "history_recovery_required"
    assert db_recovery.has_unresolved(group["group_id"])
    assert doc_row(tr2)["doc_review_status"] == "approved"
    assert [row["state"] for row in _ledger(group, tr2)] == ["canceled"]  # no ledger row


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


def _assert_recovery_block(cancel: dict) -> None:
    assert cancel["blocked_reason"] == "history_recovery_required"
    assert cancel["attempted"] is False
    assert cancel["canceled"] == []


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
