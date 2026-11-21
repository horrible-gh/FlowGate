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

def test_cas_matching_more_than_one_row_is_an_invariant_failure(env, monkeypatch):
    from modules.flow_gate.documents import tr2_service as tr2
    group = make_group("0108")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    before = canonical(doc_id).read_bytes()
    store = env["store"]
    monkeypatch.setattr(type(store), "_execute_affected", lambda self, sql, params=None: 2)
    with pytest.raises(tr2.Tr2ValidationError) as error:
        tr2.save(doc_id, tr2_body(t2), actor=USER, expected_revision=1)
    assert error.value.code == "tr2_history_invariant_error"
    assert canonical(doc_id).read_bytes() == before
def test_readiness_names_what_approval_would_refuse(env):
    group = make_group("0109")
    client = full_api()
    repo = env["repo"]
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    base = f"/api/v1/documents/{doc_id}/tr2"
    assert view(client, doc_id)["readiness"]["ready"] is True

    def put(spec, revision):
        resp = client.put(base, json={"expected_revision": revision, "body": tr2_body(t2, spec)})
        assert resp.status_code == 200, resp.text
        return view(client, doc_id)

    # no drift, termination ready_to_apply — the old screen called these green
    missing = dict(edit_spec()["edits"][0], anchor_old="not in the file")
    state = put(edit_spec(edits=[missing]), 1)
    assert state["derived"]["live_precheck"]["drift"] is False
    assert state["readiness"]["ready"] is False
    assert (state["readiness"]["code"], state["readiness"]["reason"]) == (
        "tr2_edit_not_applicable", "anchor_missing")

    (repo / "src" / "new_module.txt").write_bytes(b"already here\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "collision")
    state = put(edit_spec(edits=[CREATED]), 2)
    assert (state["readiness"]["code"], state["readiness"]["reason"]) == (
        "tr2_edit_not_applicable", "file_exists")

    state = put(edit_spec(gate={"commands": ["python -c \"print(1)\""], "apply": False}), 3)
    assert state["readiness"]["ready"] is True
    assert state["gate_admission"]["candidate_count"] == 1

    state = put(edit_spec(), 4)
    assert state["readiness"]["ready"] is True
    (repo / "src" / "stray.txt").write_bytes(b"uncommitted\n")
    state = view(client, doc_id)
    assert state["readiness"]["code"] == "tr2_worktree_dirty"
    assert approve(client, doc_id, 5, request_key="dirty:0109").json()["code"] == "tr2_worktree_dirty"
def test_readiness_after_a_reopen_requires_a_new_revision(env):
    """A Time Machine reopen puts an applied revision back in review; approval would refuse
    it (tr2_history_revision_required), so the read model must not call it ready."""
    from modules.flow_gate.services import workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal
    group = make_group("0110")
    client = full_api()
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="reopen:0110").status_code == 200
    # 0660 T0004 §3: a real reopen (status AND commit cancel). Flipping only the status
    # is the RC3 state, which test_tr2_lifecycle_0660 covers as revert_pending.
    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))
    state = view(client, doc_id)["readiness"]
    assert (state["ready"], state["code"]) == (False, "tr2_history_revision_required")
    again = approve(client, doc_id, 1, request_key="reopen:0110:again")
    assert again.json()["code"] == "tr2_history_revision_required"
