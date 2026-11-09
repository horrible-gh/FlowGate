"""flowgate.v02.0157: the Designer's standalone (non-card) document read / revision by doc_id.

Real SQLite, files, CAS, revisions, events and contacts (the 0127 T#1 ``env`` fixture). Only auth,
permission and notifications are mocked there. A card-bound document keeps the coordinate routes.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_card_document_edit_0127 import env, F, D, HEADER  # noqa: F401
from flowgate.card_document_edit import guards, target
from flowgate.card_documents import repository
from modules.flow_gate.db import documents, document_revisions

URL = "/flowgate/api/v2/documents/" + D
USER = {"Authorization": "Bearer user"}


def non_card():
    repository.delete(F, "research", "instruction", "N")


def result(response):
    return response.status_code, response.json()


def error(res, status, code):
    assert res[0] == status, res
    assert res[1]["ok"] is False and res[1]["error"]["code"] == code, res
    return res[1]["error"]["params"]


def save(env, payload, headers=USER):
    return result(env.client.post(URL + "/revisions", headers=headers, json=payload))


def body(content="designer edit\n", revision=0, key="ui-save-1"):
    return {"expected_revision": revision, "content": HEADER + content, "idempotency_key": key}


def unchanged(env):
    return (documents.get_by_id(D)["revision_no"], env.path.read_bytes(), len(document_revisions.list_by_doc(D)))


def test_standalone_read_save_reread_keeps_id_history_and_records_the_designer(env):
    non_card()
    got = result(env.client.get(URL, headers=USER))
    assert got[0] == 200 and got[1]["ok"] is True, got
    assert got[1]["edit_path"] == "non_card" and got[1]["doc_id"] == D and got[1]["revision_no"] == 0
    assert got[1]["content"] == HEADER + "original\r\n" and got[1]["body_state"] == "readable"
    saved = save(env, body())
    assert saved[0] == 200, saved
    assert saved[1]["ok"] is True and saved[1]["doc_id"] == D and saved[1]["revision_no"] == 1
    assert saved[1]["replayed"] is False and saved[1]["edit_path"] == "non_card" and "line" not in saved[1]
    assert isinstance(saved[1]["updated_at"], str) and saved[1]["updated_at"]
    reread = result(env.client.get(URL, headers=USER))[1]
    assert reread["doc_id"] == D and reread["revision_no"] == 1 and reread["content"] == HEADER + "designer edit\n"
    assert env.path.read_bytes().decode() == reread["content"]
    history = document_revisions.list_by_doc(D)
    assert len(history) == 1 and history[0]["created_by"] == "user"
    row = env.db._fetch_one("SELECT actor_user_id, metadata FROM workflow_events WHERE event_type='doc_edited'")
    meta = json.loads(row["metadata"])
    assert row["actor_user_id"] == "user"
    assert meta["origin"] == "designer" and meta["actor_kind"] == "person" and meta["edit_reason"] == "user_comment"
    assert meta["idem_key"] == "ui-save-1" and meta["node_key"] is None and meta["slot"] is None


def test_same_key_resend_replays_once_and_another_user_cannot_reuse_it(env):
    non_card()
    first = save(env, body())
    again = save(env, body())
    assert first[0] == again[0] == 200, (first, again)
    assert again[1]["replayed"] is True and again[1]["revision_no"] == 1 and again[1]["doc_id"] == D
    assert len(document_revisions.list_by_doc(D)) == 1
    stolen = save(env, body(), headers={"Authorization": "Bearer other"})
    assert error(stolen, 409, "idempotency_key_reused")["revision_no"] == 1
    assert len(document_revisions.list_by_doc(D)) == 1


def test_stale_revision_is_a_conflict_and_writes_nothing(env):
    non_card()
    assert save(env, body())[0] == 200
    before = unchanged(env)
    stale = save(env, body("other\n", revision=0, key="ui-save-2"))
    params = error(stale, 409, "revision_conflict")
    assert params["revision_no"] == 1 and params["expected_revision"] == 0
    assert unchanged(env) == before


def test_permission_is_checked_for_read_and_update(env, monkeypatch):
    non_card()
    before = unchanged(env)
    asked = []

    def deny(user, project, perm):
        asked.append((user, project, perm))
        return False
    monkeypatch.setattr(guards, "has_permission", deny)
    read = result(env.client.get(URL, headers=USER))
    assert error(read, 403, "permission_denied")["required"] == "perm_document_read"
    denied = save(env, body())
    assert error(denied, 403, "permission_denied")["required"] == "perm_document_update"
    assert asked == [("user", "flowgate", "perm_document_read"), ("user", "flowgate", "perm_document_update")]
    assert unchanged(env) == before


def test_card_bound_document_reads_as_card_and_is_refused_here(env):
    got = result(env.client.get(URL, headers=USER))
    assert got[0] == 200 and got[1]["edit_path"] == "card"
    before = unchanged(env)
    refused = save(env, body())
    params = error(refused, 422, "card_binding_missing")
    assert params["errors"][0]["validation"] == {"reason": "card_document"}
    assert unchanged(env) == before
    # The card route still saves the same document with its coordinates (existing contract untouched).
    card = env.client.post(f"/flowgate/api/v2/flows/{F}/nodes/research/slots/instruction/document/revisions",
                           headers=USER, json={"expected_doc_id": D, **body(key="card-save-1")})
    assert card.status_code == 200 and card.json()["line"]["slot"] == "instruction", card.text


def test_document_bound_to_a_card_during_the_save_is_refused_inside_the_boundary(env, monkeypatch):
    non_card()
    original = target.classify_card_slot
    calls = []

    def classify(doc):
        calls.append(doc["doc_id"])
        verdict = original(doc)
        if len(calls) == 2:  # the pre-boundary checks passed; a card takes the document now
            repository.insert(F, "research", "instruction", "N", D, True, 0, "user")
        return verdict
    monkeypatch.setattr(target, "classify_card_slot", classify)
    before = unchanged(env)
    refused = save(env, body())
    assert error(refused, 422, "card_binding_missing")
    assert len(calls) == 3  # route check, legacy_store outside, legacy_store guarded judgement
    assert unchanged(env) == before


def test_unavailable_classification_is_refused(env, monkeypatch):
    non_card()
    monkeypatch.setattr(target, "classify_card_slot", lambda doc: target.UNAVAILABLE)
    before = unchanged(env)
    refused = save(env, body())
    assert error(refused, 422, "card_binding_missing")["errors"][0]["validation"] == {"reason": "card_edit_unavailable"}
    assert result(env.client.get(URL, headers=USER))[1]["edit_path"] == "unavailable"
    assert unchanged(env) == before


def test_input_contract_auth_first_and_missing_document_not_created(env):
    non_card()
    before = unchanged(env)
    assert env.client.post(URL + "/revisions", content="{").status_code == 401
    assert env.client.get(URL).status_code == 401
    bad_json = result(env.client.post(URL + "/revisions", headers=USER, content="{"))
    assert error(bad_json, 422, "invalid_request")
    no_key = save(env, {k: v for k, v in body().items() if k != "idempotency_key"})
    assert error(no_key, 422, "invalid_request")["errors"][0] == {"field": "idempotency_key", "reason": "required"}
    bad_key = save(env, body(key="bad key"))
    assert error(bad_key, 422, "invalid_request")["errors"][0]["field"] == "idempotency_key"
    no_rev = save(env, {k: v for k, v in body().items() if k != "expected_revision"})
    assert error(no_rev, 422, "expected_revision_missing")
    coords = save(env, {**body(), "flow_id": F})
    assert error(coords, 422, "invalid_request")["errors"][0] == {"field": "flow_id", "reason": "unknown_field"}
    missing_id = F + ".0099-R"
    missing = result(env.client.post("/flowgate/api/v2/documents/" + missing_id + "/revisions", headers=USER, json=body()))
    assert error(missing, 404, "document_not_found")["doc_id"] == missing_id
    assert documents.get_by_id(missing_id) is None
    assert unchanged(env) == before


def test_not_editable_documents_stay_refused(env, monkeypatch):
    non_card()
    monkeypatch.setattr(guards.document_service, "is_final_approved", lambda d: True)
    before = unchanged(env)
    refused = save(env, body())
    assert error(refused, 422, "not_editable")["reason"] == "final_approved"
    assert unchanged(env) == before
