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

def test_rc3_service_level_reopen_is_refused_too(env, monkeypatch):
    """Automatic callers (failed-TS auto reopen) go through the same service boundary."""
    from modules.flow_gate.services.git.credentials import GitServiceError
    group = make_group("0615")
    client = full_api()
    t2, tr2_id = _approved_tr2(env, group, client)
    _blocked_reopen(group, tr2_id, monkeypatch)
    with pytest.raises(GitServiceError) as caught:
        _reopen(t2)
    assert (caught.value.status, caught.value.code) == (409, "workflow_revert_pending")
    assert doc_row(t2)["doc_review_status"] == "approved"
def test_s3_head_lookup_failure_refuses_the_approval(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    group = make_group("0616")
    client = full_api()
    later = _later_step(env, group, client)

    def broken(_doc_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pipeline_service.db_wfseq, "get_item_by_result_doc_id", broken)
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "확인할 수 없어" in refused.json()["detail"]
    assert "database is locked" not in refused.text
    assert doc_row(later)["doc_review_status"] == "pending_review"
def test_s3_missing_head_refuses_the_approval(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    group = make_group("0617")
    client = full_api()
    later = _later_step(env, group, client)
    monkeypatch.setattr(pipeline_service.db_wfseq, "get_effective_head", lambda _sid: None)
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "확인할 수 없어" in refused.json()["detail"]
    assert doc_row(later)["doc_review_status"] == "pending_review"
def test_s3_readable_head_passes_the_guard_to_the_next_rule(env):
    """Control: with a readable head the guard lets the in-order step through; the empty
    next-empty T is then refused by the ordinary empty-body rule, not by the head guard."""
    group = make_group("0618")
    client = full_api()
    later = _later_step(env, group, client)
    resp = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert resp.status_code == 409, resp.text
    assert "본문이 비어" in resp.json()["detail"]
    assert "헤드" not in resp.json()["detail"]
