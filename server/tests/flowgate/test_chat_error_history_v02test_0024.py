"""Chat error history and the applied read/edit access of a chat run (flowgate.v02test.0024 B, D).

B (decision 1): a refused send / AI call / command decision is recorded per user and read back
with the chat's activity, so it stays in the conversation history across F5 and reopening.
The record reuses ``workflow_events`` (group_id NULL), keeps only code / redacted message /
time, is idempotent per client id and never refused by the group AI-run lease.

D (decision 2): ``GET /ai-invoke/{run_id}`` answers the run's applied ``source_access`` from
its token row (``read`` / ``read_write``), on the live and the persisted half alike.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    CHAT_DOC, GROUP, OTHER_USER, PROJECT, USER, fg_store, seed,  # noqa: F401
)

from flowgate.conversation import chat_errors  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import routes  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402
from modules.flow_gate.services.ai_invoke import diagnostics  # noqa: E402


def _client(user):
    app = FastAPI()
    app.include_router(routes.router, prefix="/flowgate/api/v1")
    app.dependency_overrides[routes.get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def env(fg_store, monkeypatch):
    from flowgate.card_document_edit import integration as edit_integration
    from modules.flow_gate.graph_workflow.services import card_document_ports
    monkeypatch.setattr(card_document_ports, "_handlers", dict(card_document_ports._handlers))
    edit_integration.register()  # The same required document write port as startup.
    seed(fg_store)
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: True)
    return {"store": fg_store}


def _post(client, body, doc=CHAT_DOC):
    return client.post(f"/flowgate/api/v1/chat-activity/{doc}/errors", json=body)


def test_a_recorded_error_comes_back_with_the_activity_read(env):
    owner = _client({"user_id": USER})
    res = _post(owner, {"id": "err-0001-aaaa", "code": "invoke_failed", "message": "AI provider unavailable",
                        "occurred_at": "2026-11-12T07:30:00+09:00"})
    assert res.status_code == 200, res.text
    assert res.json()["replayed"] is False
    body = owner.get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}").json()
    assert body["ok"] is True
    assert body["errors"] == [{"id": "err-0001-aaaa", "code": "invoke_failed", "tone": "danger",
                               "message": "AI provider unavailable", "occurred_at": "2026-11-12T07:30:00+09:00"}]


def test_errors_are_oldest_first_and_per_user(env):
    owner, other = _client({"user_id": USER}), _client({"user_id": OTHER_USER})
    _post(owner, {"id": "err-0001-first", "code": "send_failed", "message": "one"})
    _post(owner, {"id": "err-0002-second", "code": "commit_required", "message": "two"})
    _post(other, {"id": "err-0003-other1", "code": "send_failed", "message": "not yours"})
    mine = owner.get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}").json()["errors"]
    assert [e["id"] for e in mine] == ["err-0001-first", "err-0002-second"]
    assert [e["tone"] for e in mine] == ["danger", "warning"]
    theirs = other.get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}").json()["errors"]
    assert [e["id"] for e in theirs] == ["err-0003-other1"]


def test_a_repeat_of_the_same_id_is_answered_not_stored_twice(env):
    owner = _client({"user_id": USER})
    first = _post(owner, {"id": "err-0001-again", "code": "send_failed", "message": "first words"})
    again = _post(owner, {"id": "err-0001-again", "code": "send_failed", "message": "second words"})
    assert first.status_code == again.status_code == 200
    assert again.json()["replayed"] is True and again.json()["error"]["message"] == "first words"
    rows = env["store"]._fetch_all("SELECT * FROM workflow_events WHERE event_type = 'chat_error'", [])
    assert len(rows) == 1


def test_the_row_stays_out_of_group_listings_and_keeps_no_secret(env):
    owner = _client({"user_id": USER})
    _post(owner, {"id": "err-0001-secret", "code": "invoke_failed",
                  "message": "401 Authorization: Bearer abc.def-ghi token=s3cr3t rest"})
    row = env["store"]._fetch_one("SELECT * FROM workflow_events WHERE event_type = 'chat_error'", [])
    assert row["group_id"] is None and row["document_id"] is None
    assert row["project_id"] == PROJECT and row["actor_user_id"] == USER
    meta = json.loads(row["metadata"])
    assert set(meta) == {"doc_id", "client_error_id", "code", "message", "occurred_at"}
    assert meta["doc_id"] == CHAT_DOC
    assert "abc.def-ghi" not in meta["message"] and "s3cr3t" not in meta["message"]
    assert "rest" in meta["message"]
    # The stored text reads like the browser's own redaction (stg check: F5 showed [REDACTED]).
    assert meta["message"] == "401 Authorization: Bearer *** token=*** rest"


@pytest.mark.parametrize("raw, masked", [
    ("401 Bearer abc.DEF-1 token=s3cr3t ok", "401 Bearer *** token=*** ok"),
    ("Authorization: Bearer a,b'c", "Authorization: Bearer ***'c"),
    ("FLOWGATE_TOKEN=xyz MY_API_KEY: k1 password=p", "FLOWGATE_TOKEN=*** MY_API_KEY: *** password=***"),
    ("Authorization: Basic zz", "Authorization: *** zz"),
])
def test_redaction_masks_with_stars_like_the_client_and_is_idempotent(raw, masked):
    # Same cases as chatHistory.v02test0024.spec.ts 'redacts credential-looking words'.
    assert chat_errors.redact(raw) == masked
    assert chat_errors.redact(masked) == masked
    assert "[REDACTED]" not in chat_errors.redact(raw)


@pytest.mark.parametrize("body", [
    {"id": "err-0001-badcode", "code": "settings_failed", "message": "x"},
    {"id": "short", "code": "send_failed"},
    {"id": "has space in it", "code": "send_failed"},
    {"code": "send_failed"},
    None,
])
def test_invalid_records_are_refused(env, body):
    res = _client({"user_id": USER}).post(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}/errors", json=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "conversation_invalid_request"


def test_only_a_readable_chat_takes_a_record(env, monkeypatch):
    owner = _client({"user_id": USER})
    res = _post(owner, {"id": "err-0001-nodoc1", "code": "send_failed"}, doc=f"{GROUP}.0042-CH")
    assert res.status_code == 404
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: False)
    res = _post(owner, {"id": "err-0001-denied", "code": "send_failed"})
    assert res.status_code == 403


def test_a_failing_error_read_never_fails_the_activity_read(env, monkeypatch):
    def boom(*_a):
        raise RuntimeError("store down")

    monkeypatch.setattr(chat_errors, "list_for", boom)
    body = _client({"user_id": USER}).get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}").json()
    assert body["ok"] is True and body["errors"] == []


def test_the_record_is_exempt_from_the_group_lease():
    assert policy.is_group_lease_exempt_path(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}/errors", "POST")
    assert policy.is_group_lease_exempt_path(f"/api/v1/chat-activity/{CHAT_DOC}/errors", "POST")
    assert not policy.is_group_lease_exempt_path(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}/errors", "DELETE")
    assert not policy.is_group_lease_exempt_path(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}", "POST")
    assert policy.classify_mutation_route("/flowgate/api/v1/chat-activity/{doc_id}/errors", {"POST"}) == (
        "personal", "chat_error_record")


def test_the_application_mounts_the_record_route_with_its_reason():
    from routers.main import app

    found = {path: (route.mutation_resource, route.mutation_reason)
             for route, path in policy.iter_mutation_routes(app) if path.endswith("/errors")}
    assert found.get("/flowgate/api/v1/chat-activity/{doc_id}/errors") == ("personal", "chat_error_record")


# ── D: the applied read/edit access of a chat run ────────────────────────────

def _token(token_id, *, action_scope="chat", source_access="read_write"):
    from modules.flow_gate.db import tokens

    tokens.create({"token_id": token_id, "hash": f"hash-{token_id}", "pepper_id": "test", "project": PROJECT,
                   "group_id": GROUP, "doc_ref": CHAT_DOC, "action_scope": action_scope, "issued_to": USER,
                   "source_access": source_access, "created_at": "2026-01-01T00:00:00+00:00",
                   "expires_at": "2099-01-01T00:00:00+00:00"})


def test_chat_source_access_reads_the_token_row(env):
    _token("tok_edit", source_access="read_write")
    _token("tok_read", source_access="read")
    _token("tok_review", action_scope="review", source_access=None)
    assert diagnostics.chat_source_access("tok_edit") == "read_write"
    assert diagnostics.chat_source_access("tok_read") == "read"
    assert diagnostics.chat_source_access("tok_review") is None
    assert diagnostics.chat_source_access("tok_missing") is None
    assert diagnostics.chat_source_access(None) is None


class _FakeSvc:
    def __init__(self, record):
        self.record = record

    def get_run_record(self, run_id):
        return self.record if self.record and self.record["run_id"] == run_id else None


def test_the_live_detail_answers_the_applied_access_and_the_pinned_provider(env, monkeypatch):
    # An edit_once whose claim was lost runs with a "read" token: that is what the detail says.
    _token("tok_lost_claim", source_access="read")
    record = {"run_id": "aiv_live", "token_id": "tok_lost_claim", "requested_provider_id": "aip_pick"}
    monkeypatch.setattr(diagnostics, "_svc", lambda: _FakeSvc(record))
    monkeypatch.setattr(diagnostics, "get_status", lambda run_id: {
        "ok": True, "run_id": run_id, "status": "running", "group_id": GROUP, "provider": None,
        "action_scope": "chat"})
    payload = diagnostics.get_run_detail("aiv_live")
    assert payload["source_access"] == "read"
    assert payload["requested_provider_id"] == "aip_pick"
    assert payload["persisted"] is False


def test_the_persisted_detail_answers_the_applied_access(env, monkeypatch):
    _token("tok_done", source_access="read_write")
    monkeypatch.setattr(diagnostics, "_svc", lambda: _FakeSvc(None))
    from modules.flow_gate.db import ai_invoke_runs

    monkeypatch.setattr(ai_invoke_runs, "get", lambda run_id: {"run_id": run_id, "token_id": "tok_done"})
    monkeypatch.setattr(diagnostics, "_run_detail_from_row", lambda row: {
        "ok": True, "run_id": row["run_id"], "status": "finished", "group_id": GROUP, "token_id": row["token_id"]})
    payload = diagnostics.get_run_detail("aiv_done")
    assert payload["source_access"] == "read_write"
    assert payload["persisted"] is True
