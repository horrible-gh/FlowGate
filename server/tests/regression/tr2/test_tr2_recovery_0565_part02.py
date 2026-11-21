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

def test_damaged_and_schema_invalid_bodies_are_distinct_and_recoverable(env):
    group = make_group("0105")
    client = full_api()
    _t2, doc_id, base = _proposal_with_history(client, group)

    canonical(doc_id).write_bytes(b'{"tr2_version": 1, "edit_spec": ')
    resp = client.get(base)
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_body_corrupt", resp.text
    raw = client.get(f"{base}/raw").json()
    assert raw["content"] == '{"tr2_version": 1, "edit_spec": '  # inspectable, downloadable
    assert recovery(client, doc_id)["raw"]["available"] is True

    canonical(doc_id).write_bytes(json.dumps({"tr2_version": 1}).encode("utf-8"))
    resp = client.get(base)
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_body_schema_invalid", resp.text
    assert resp.json()["details"]["loc"] == "body"

    # a snapshot whose bytes are not what was saved is refused, not "repaired"
    snapshot(doc_id, 1).write_bytes(snapshot(doc_id, 1).read_bytes().replace(b'"high"', b'"low" '))
    info = {r["revision_no"]: r for r in recovery(client, doc_id)["revisions"]}
    assert info[1]["usable"] is False and info[1]["problem"] == "tr2_body_corrupt"
    refused = client.post(f"{base}/recovery/restore", json={"expected_revision": 3, "revision_no": 1})
    assert refused.status_code == 409 and refused.json()["code"] == "tr2_revision_unusable"

    resp = client.post(f"{base}/recovery/restore", json={"expected_revision": 3, "revision_no": 3})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 4, resp.text
    assert view(client, doc_id)["document"]["revision_no"] == 4
def test_no_recovery_source_fails_closed_until_a_new_proposal_is_written(env):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    group = make_group("0106")
    client = full_api()
    t2, doc_id, base = _proposal_with_history(client, group)
    canonical(doc_id).unlink()
    for revision in (1, 2, 3):
        snapshot(doc_id, revision).unlink()

    state = recovery(client, doc_id)
    assert state["recoverable"] is False and state["recommended_revision_no"] is None
    assert state["approval_blocked"] is True
    assert client.get(f"{base}/raw").json()["code"] == "tr2_body_missing"
    assert approve(client, doc_id, 3, request_key="gone:0106").json()["code"] == "tr2_body_missing"
    assert db_attempts.list_by_doc(doc_id) == []

    resp = client.post(f"{base}/recovery/new", json={"expected_revision": 3})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 4, resp.text
    fresh = view(client, doc_id)
    assert fresh["body"]["edit_spec"]["edits"] == [] and fresh["readiness"]["ready"] is False
    # still not approvable: an empty proposal is needs_more_work
    refused = approve(client, doc_id, 4, request_key="gone:0106:new")
    assert refused.status_code != 200
    assert doc_row(doc_id)["doc_review_status"] == "pending_review"
def test_revision_cas_is_decided_by_the_database_not_the_process_lock(env, monkeypatch):
    from modules.flow_gate.documents import tr2_service as tr2
    group = make_group("0107")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    before = canonical(doc_id).read_bytes()
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    real_root = tr2.resolve_source_root
    entered, release, second_started = Event(), Event(), Event()
    calls = []
    def held_build(*args):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return real_root(*args)
    monkeypatch.setattr(tr2, "resolve_source_root", held_build)
    def save(second=False):
        if second:
            second_started.set()
        return tr2.save(doc_id, tr2_body(t2, edit_spec(edits=[CREATED])), actor=USER, expected_revision=1)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(save)
        assert entered.wait(10)
        second = pool.submit(save, True)
        assert second_started.wait(10) and not second.done()
        release.set()
        assert first.result(timeout=15)["new_revision"] == 2
        with pytest.raises(tr2.Tr2ValidationError) as error:
            second.result(timeout=15)
    assert error.value.code == "tr2_spec_changed" and error.value.details["current_revision_no"] == 2
    assert doc_row(doc_id)["revision_no"] == 2 and snapshot(doc_id, 2).read_bytes() == canonical(doc_id).read_bytes()
    assert canonical(doc_id).read_bytes() != before and len(calls) == 1
