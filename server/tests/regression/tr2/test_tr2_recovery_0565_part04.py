"""Independently selectable scenarios; shared fixtures remain in test_tr2_recovery_0565.py."""
from __future__ import annotations

from tests.regression.tr2.test_tr2_recovery_0565 import (
    CREATED,
    GATE_OK,
    MODULE,
    PROJECT,
    USER,
    _db_path,
    _proposal_with_history,
    api,
    approve,
    assert_no_host_path,
    auto_approved_t2,
    canonical,
    dedicated_document_write_ports_0127,
    doc_row,
    edit_spec,
    env,
    full_api,
    git,
    json,
    make_group,
    next_empty,
    pytest,
    recovery,
    repo,
    snapshot,
    sqlite3,
    store,
    submit_tr2,
    subprocess,
    sys,
    tr2_body,
    view,
)

def test_time_machine_reopen_reaches_open_tr2_screens(env, monkeypatch):
    """A rewind changes a 반영안's approval and source history outside the TR2 routes; open
    screens must hear about it (T0030 §11-6) and must not call it ready afterwards."""
    import modules.flow_gate.api.v1.events.publisher as publisher
    from modules.flow_gate.api.v1.events.event_types import EventType
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.services import workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal
    group = make_group("0111")
    client = full_api()
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="tm:0111").status_code == 200
    events = []
    monkeypatch.setattr(publisher, "broadcast_event_threadsafe", lambda event: events.append(event) or 1)

    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))

    assert doc_row(doc_id)["doc_review_status"] == "pending_review"
    row = [r for r in db_ledger.list_by_group(group["group_id"]) if r["doc_id"] == doc_id][0]
    assert row["state"] == "canceled"
    notified = [e for e in events if e.event_type == EventType.GROUP_VIEW_REFRESH
                and e.payload.get("reason") == "tr2_history_changed"]
    assert [(e.doc_id, e.audience) for e in notified] == [(doc_id, "*")]
    assert view(client, doc_id)["readiness"]["code"] == "tr2_history_revision_required"
@pytest.mark.parametrize("breakage", ["unregistered", "dir_missing"])
def test_read_model_loads_the_proposal_when_the_worktree_is_unavailable(env, monkeypatch, breakage):
    """Loading a stored 반영안 does not depend on the source (T0030 §7): with Git integration
    on but the group worktree released or gone, GET still returns the proposal, the
    source-derived facts are unknown, and readiness names the approval block."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service
    group = make_group("0112" if breakage == "unregistered" else "0113")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert view(client, doc_id)["readiness"]["ready"] is True

    if breakage == "unregistered":
        db_git.unregister_worktree(group["group_id"])
    else:
        monkeypatch.setattr(git_service, "src_root", lambda _name, _branch: env["repo"] / "gone")
    resp = client.get(f"/api/v1/documents/{doc_id}/tr2")
    assert resp.status_code == 200, resp.text
    assert_no_host_path(resp, env["repo"])
    state = resp.json()
    assert state["document"]["revision_no"] == 1
    assert [edit["id"] for edit in state["body"]["edit_spec"]["edits"]] == ["e1"]
    assert (state["readiness"]["ready"], state["readiness"]["code"]) == (
        False, "tr2_git_unavailable")
    live = state["derived"]["live_precheck"]
    assert (live["source_available"], live["live_fingerprint"], live["drift"]) == (
        False, None, None)
    assert live["baseline_fingerprint"] == state["body"]["baseline_fingerprint"]
    assert live["anchors"] is None
    assert [(f["path"], f["exists"], f["edit_ids"]) for f in state["derived"]["files"]] == [
        ("src/app.txt", None, ["e1"])]
    refused = approve(client, doc_id, 1, request_key=f"unavailable:{breakage}")
    assert refused.json()["code"] == "tr2_git_unavailable"
def test_time_machine_new_revision_reapproves_from_reverted_source(env):
    """Cancel releases ownership (0641 file policy: a canceled lineage is inactive) and the
    new approval takes it back; a newly saved edit uses the reverted source baseline."""
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.services import tr2_file_policy, workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal

    group = make_group("0141")
    client = full_api()
    source = env["repo"] / "src" / "app.txt"
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="tm:0141:first").status_code == 200
    first_head = git(env["repo"], "rev-parse", "HEAD").strip()
    assert source.read_bytes() == b"greeting = 'hi'\ncount = 1\n"

    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))
    canceled_head = git(env["repo"], "rev-parse", "HEAD").strip()
    assert canceled_head != first_head
    assert source.read_bytes() == b"greeting = 'hello'\ncount = 1\n"
    assert git(env["repo"], "status", "--porcelain") == ""
    assert tr2_file_policy.managed_paths(group["group_id"]) == set()
    unchanged = approve(client, doc_id, 1, request_key="tm:0141:same")
    assert unchanged.status_code == 409
    assert unchanged.json()["code"] == "tr2_history_revision_required"
    assert git(env["repo"], "rev-parse", "HEAD").strip() == canceled_head

    changed = edit_spec()
    changed["edits"] = [dict(changed["edits"][0], replacement_new="greeting = 'welcome'")]
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2, changed)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 2, saved.text
    state = view(client, doc_id)
    assert state["readiness"]["ready"] is True
    assert state["body"]["baseline_fingerprint"] == state["derived"]["live_precheck"]["live_fingerprint"]
    assert state["body"]["baseline_fingerprint"] == db_attempts.latest_success(doc_id)["baseline_fingerprint"]
    assert approve(client, doc_id, 2, request_key="tm:0141:new").status_code == 200
    second_head = git(env["repo"], "rev-parse", "HEAD").strip()
    assert second_head != canceled_head
    assert git(env["repo"], "rev-parse", "HEAD^").strip() == canceled_head
    assert source.read_bytes() == b"greeting = 'welcome'\ncount = 1\n"
    assert git(env["repo"], "status", "--porcelain") == ""
    assert [(row["document_revision"], row["state"]) for row in db_attempts.list_by_doc(doc_id)] == [
        (2, "succeeded"), (1, "succeeded")]
    rows = [row for row in db_ledger.list_by_group(group["group_id"]) if row["doc_id"] == doc_id]
    assert len(rows) == 2
    assert sorted(row["state"] for row in rows) == ["canceled", "live"]
    assert tr2_file_policy.managed_paths(group["group_id"]) == {"src/app.txt"}
def test_time_machine_new_revision_reports_real_precheck_failure(env):
    """An incomplete or ungrounded new revision never writes source or a commit."""
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.services import workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal

    group = make_group("0142")
    client = full_api()
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="tm:0142:first").status_code == 200
    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))
    canceled_head = git(env["repo"], "rev-parse", "HEAD").strip()
    source = env["repo"] / "src" / "app.txt"
    baseline_source = source.read_bytes()

    empty = edit_spec(termination="needs_more_work", edits=[])
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2, empty)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 2, saved.text
    state = view(client, doc_id)
    assert state["readiness"]["code"] == "tr2_edit_not_applicable"
    assert state["readiness"]["reason"] == "needs_more_work"
    refused = approve(client, doc_id, 2, request_key="tm:0142:empty")
    assert refused.status_code == 422 and refused.json()["code"] == "tr2_edit_not_applicable"
    assert refused.json()["details"]["loc"] == "edit_spec.edits"
    assert db_attempts.latest_by_doc(doc_id)["error_code"] == "tr2_edit_not_applicable"
    assert source.read_bytes() == baseline_source
    assert git(env["repo"], "rev-parse", "HEAD").strip() == canceled_head

    wrong = edit_spec()
    wrong["edits"] = [dict(wrong["edits"][0], anchor_old="not present in source")]
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 2, "body": tr2_body(t2, wrong)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 3, saved.text
    assert view(client, doc_id)["readiness"]["reason"] == "anchor_missing"
    refused = approve(client, doc_id, 3, request_key="tm:0142:wrong")
    assert refused.status_code == 422 and refused.json()["code"] == "tr2_edit_not_applicable"
    assert db_attempts.latest_by_doc(doc_id)["error_code"] == "tr2_edit_not_applicable"
    assert source.read_bytes() == baseline_source
    assert git(env["repo"], "rev-parse", "HEAD").strip() == canceled_head
    assert git(env["repo"], "status", "--porcelain") == ""
