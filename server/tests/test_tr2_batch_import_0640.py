"""0640 T#2: batch JSON preview/import, actual writer/DB/files and approval contracts."""
import copy

import pytest

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.documents import tr2_service as service
from test_tr2_connected_e2e_0565 import (
    env, store, repo, api, make_group, auto_approved_t2, submit_tr2, view,
    approve, git_head, CREATED, PROJECT, USER,
)


EDIT = {"id": "e2", "kind": "edit", "file": "src/app.txt", "anchor_old": "count = 1",
        "replacement_new": "count = 2", "rationale": "count", "confidence": "high"}
DEFERRED = {"id": "d2", "file": "src/later.txt", "reason": "needs_runtime",
            "rationale": "measure"}


@pytest.fixture
def proposal(env, monkeypatch):
    # The legacy helper replaces _scratch_dir with an arbitrary directory. Issue
    # test tokens through the current managed scratch contract instead. This only
    # fixes this fixture; the unchanged legacy suite retains its own diagnostics.
    import test_tr2_connected_e2e_0565 as connected
    from modules.flow_gate.services import token_service

    def issue_token(group, scope, doc_ref):
        return token_service.issue(project=PROJECT, group_id=group["group_id"],
                                   action_scope=scope, doc_ref=doc_ref,
                                   issued_to=USER)["raw_token"]

    monkeypatch.setattr(connected, "issue_token", issue_token)
    group = make_group("0640")
    doc_id = submit_tr2(group, auto_approved_t2(group))
    client = api()
    base = f"/api/v1/documents/{doc_id}/tr2"
    return env, doc_id, client, base


def entries():
    return [{"collection": "edits", "item": copy.deepcopy(EDIT)},
            {"collection": "edits", "item": copy.deepcopy(CREATED)},
            {"collection": "deferred", "item": copy.deepcopy(DEFERRED)}]


def unchanged_state(doc_id, source):
    doc = db_docs.get_by_id(doc_id)
    path = service.canonical_path_for_doc(doc)
    snapshots = {p.name: p.read_bytes() for p in service.revisions_dir(doc).glob("*.json")}
    rows = service.get_store()._fetch_all(
        "SELECT * FROM document_revisions WHERE doc_id = ? ORDER BY revision_no", [doc_id])
    return (doc, path.read_bytes(), snapshots, rows, git_head(source),
            (source / "src/app.txt").read_bytes(),
            (source / "src/new_module.txt").exists())


def test_preview_mixed_errors_then_one_atomic_revision_and_approval(proposal):
    env, doc_id, client, base = proposal
    before = unchanged_state(doc_id, env["repo"])
    source_before = (env["repo"] / "src/app.txt").read_bytes()
    incoming = entries() + [
        {"collection": "edits", "item": {"id": "bad"}},
        {"collection": "edits", "item": {**EDIT, "id": "e1"}},
        {"collection": "edits", "item": {**EDIT, "id": "unsafe", "file": "../outside"}},
    ]
    preview = client.post(f"{base}/items/batch/preview", json={"expected_revision": 1, "items": incoming})
    assert preview.status_code == 200, preview.text
    assert [r["valid"] for r in preview.json()["items"]] == [True, True, True, False, False, False]
    assert unchanged_state(doc_id, env["repo"]) == before
    # A malicious client posting the entire mixed set gets no partial save.
    rejected = client.post(f"{base}/items/batch", json={"expected_revision": 1, "items": incoming})
    assert rejected.status_code == 422
    assert unchanged_state(doc_id, env["repo"]) == before
    selected = [incoming[r["index"]] for r in preview.json()["items"] if r["valid"]]
    saved = client.post(f"{base}/items/batch", json={"expected_revision": 1, "items": selected})
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result["new_revision"] == 2 and result["imported_count"] == 3
    assert result["operation"] == "import_items"
    current = view(client, doc_id)
    assert [i["id"] for i in current["body"]["edit_spec"]["edits"]] == ["e1", "e2", "c1"]
    assert [i["id"] for i in current["body"]["edit_spec"]["deferred"]] == ["d1", "d2"]
    assert current["body"]["edit_spec"]["gate"] == view_body_gate(before[1])
    assert (env["repo"] / "src/app.txt").read_bytes() == source_before
    assert not (env["repo"] / "src/new_module.txt").exists()
    assert current["approval"]["attempts"] == [] and current["history"]["ledger"] == []
    assert service.snapshot_path(db_docs.get_by_id(doc_id), 2).read_bytes() == service.canonical_path_for_doc(db_docs.get_by_id(doc_id)).read_bytes()
    assert len(unchanged_state(doc_id, env["repo"])[3]) == len(before[3]) + 1
    # Replaying the same revision is CAS failure, not another revision or duplicate append.
    after = unchanged_state(doc_id, env["repo"])
    stale = client.post(f"{base}/items/batch", json={"expected_revision": 1, "items": selected})
    assert stale.status_code == 409 and stale.json()["code"] == "tr2_spec_changed"
    assert unchanged_state(doc_id, env["repo"]) == after
    (env["repo"] / "gate.ok").write_text("ok", encoding="utf-8")
    approved = approve(client, doc_id, 2)
    assert approved.status_code == 200, approved.text
    assert "count = 2" in (env["repo"] / "src/app.txt").read_text()
    assert "greeting = 'hi'" in (env["repo"] / "src/app.txt").read_text()
    assert (env["repo"] / "src/new_module.txt").read_text() == CREATED["content"]
    final = view(client, doc_id)
    assert final["document"]["doc_review_status"] == "approved"
    assert len(final["approval"]["attempts"]) == 1
    assert len(final["history"]["ledger"]) == 1


def view_body_gate(raw):
    import json
    return json.loads(raw)["edit_spec"]["gate"]


@pytest.mark.parametrize("incoming", [
    [],
    [{"collection": "edits", "item": EDIT}] * 201,
    [{"collection": "unknown", "item": EDIT}],
    [{"collection": "edits", "item": EDIT, "baseline_fingerprint": "forged"}],
    [{"collection": "edits", "item": {**EDIT, "confidence": 0.5}}],
    [{"collection": "edits", "item": {**EDIT, "replacement_new": EDIT["anchor_old"]}}],
    [{"collection": "edits", "item": {**EDIT, "id": "d1"}}],
    [{"collection": "edits", "item": EDIT}, {"collection": "deferred", "item": {**DEFERRED, "id": "e2"}}],
    [{"collection": "edits", "item": CREATED}, {"collection": "edits", "item": {**CREATED, "id": "c2"}}],
    [{"collection": "edits", "item": {**CREATED, "file": "src/./app.txt"}}],
])
def test_invalid_batch_never_saves_anything(proposal, incoming):
    env, doc_id, client, base = proposal
    before = unchanged_state(doc_id, env["repo"])
    response = client.post(f"{base}/items/batch", json={"expected_revision": 1, "items": incoming})
    assert response.status_code == 422, response.text
    assert unchanged_state(doc_id, env["repo"]) == before


def test_duplicate_import_ids_all_excluded_and_same_path_edits_allowed(proposal):
    _env, _doc_id, client, base = proposal
    incoming = [{"collection": "edits", "item": EDIT},
                {"collection": "deferred", "item": {**DEFERRED, "id": "e2"}},
                {"collection": "edits", "item": {**EDIT, "id": "e3"}}]
    response = client.post(f"{base}/items/batch/preview", json={"expected_revision": 1, "items": incoming})
    assert response.status_code == 200, response.text
    assert [i["valid"] for i in response.json()["items"]] == [False, False, True]
    # Two distinct EDIT IDs on the same normalized path remain separate items.
    response = client.post(f"{base}/items/batch", json={"expected_revision": 1, "items": [
        {"collection": "edits", "item": EDIT},
        {"collection": "edits", "item": {**EDIT, "id": "e3", "file": "src/./app.txt"}},
    ]})
    assert response.status_code == 200, response.text
    assert len(response.json()["body"]["edit_spec"]["edits"]) == 3


def test_snapshot_failure_rolls_back_entire_batch_and_revision(proposal, monkeypatch):
    env, doc_id, client, base = proposal
    before = unchanged_state(doc_id, env["repo"])
    original = service._record_snapshot

    def fail_after_snapshot(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated snapshot failure")

    monkeypatch.setattr(service, "_record_snapshot", fail_after_snapshot)
    response = client.post(f"{base}/items/batch", json={"expected_revision": 1, "items": entries()})
    assert response.status_code == 500 and response.json()["code"] == "tr2_internal_error"
    assert unchanged_state(doc_id, env["repo"]) == before


@pytest.mark.parametrize("reason, code", [
    ("approved", "tr2_spec_immutable"), ("applying", "tr2_in_progress"),
    ("recovery_required", "tr2_spec_immutable"), ("revert_pending", "tr2_revert_pending"),
])
def test_batch_domain_keeps_existing_mutation_locks(proposal, monkeypatch, reason, code):
    env, doc_id, _client, _base = proposal
    before = unchanged_state(doc_id, env["repo"])
    monkeypatch.setattr(service, "mutation_block", lambda _doc: reason)
    for call in (
        lambda: service.preview_items(doc_id, entries(), expected_revision=1),
        lambda: service.import_items(doc_id, entries(), actor="human", expected_revision=1),
    ):
        with pytest.raises(service.Tr2ValidationError) as error:
            call()
        assert error.value.code == code
    assert unchanged_state(doc_id, env["repo"]) == before


def test_preview_cas_conflict_and_single_item_roundtrip_still_work(proposal):
    env, doc_id, client, base = proposal
    added = client.post(f"{base}/items", json={"expected_revision": 1, "collection": "edits", "item": EDIT})
    assert added.status_code == 200 and added.json()["new_revision"] == 2
    before = unchanged_state(doc_id, env["repo"])
    preview = client.post(f"{base}/items/batch/preview", json={"expected_revision": 1, "items": entries()})
    assert preview.status_code == 409 and preview.json()["code"] == "tr2_spec_changed"
    assert unchanged_state(doc_id, env["repo"]) == before
    replaced = client.put(f"{base}/items/e2", json={"expected_revision": 2, "item": {**EDIT, "replacement_new": "count = 3"}})
    assert replaced.status_code == 200 and replaced.json()["new_revision"] == 3
    assert client.get(f"{base}/items/e2").json()["item"]["replacement_new"] == "count = 3"
