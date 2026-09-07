"""flowgate.default.0481 T0008 — the four new review-gate HTTP routes.

test_git_integration_0115.py's TestGitEndToEnd already drives the real state machine
(git_service.get_merge_review/approve_merge_review/reject_merge_review/send_review_message)
end-to-end against a real git repo. This file is the thin layer ABOVE that: does the route
parse the request body correctly, enforce the human-only/provider-pinned/UUID contracts
before ever calling the service, and turn a GitServiceError into the standard envelope?
git_service is monkeypatched here on purpose — the state machine itself is not this
file's concern.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.auth.middleware import get_current_user  # noqa: E402
from modules.flow_gate.api.v1 import git_routes  # noqa: E402
from modules.flow_gate.services.git_service import GitServiceError  # noqa: E402

GROUP_ID = "flowgate.default.0481"
MERGE_ID = 42


def _client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin"}
    monkeypatch.setattr(git_routes, "_has_permission", lambda *a, **k: True)
    return TestClient(app, raise_server_exceptions=False)


def test_get_review_returns_service_payload(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes.git_service, "get_merge_review",
        lambda group_id, merge_id: {"ok": True, "result": {
            "group_id": group_id, "merge_id": merge_id, "review_state": "resolved_pending_review",
        }},
    )
    resp = client.get(f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/review")
    assert resp.status_code == 200
    assert resp.json()["result"]["review_state"] == "resolved_pending_review"


def test_get_review_not_found_is_envelope_not_500(monkeypatch):
    client = _client(monkeypatch)

    def _raise(*_a, **_k):
        raise GitServiceError(404, "review_not_found", f"merge session {MERGE_ID} not found")

    monkeypatch.setattr(git_routes.git_service, "get_merge_review", _raise)
    resp = client.get(f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/review")
    assert resp.status_code == 404
    assert resp.json() == {"ok": False, "error": {"code": "review_not_found", "message": f"merge session {MERGE_ID} not found"}}


def test_approve_rejects_non_uuid_attempt_id_before_touching_the_service(monkeypatch):
    client = _client(monkeypatch)
    called = []
    monkeypatch.setattr(
        git_routes.git_service, "approve_merge_review",
        lambda *a, **k: called.append(1) or {"ok": True, "result": {"status": "merged"}},
    )
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/approve",
        json={"attempt_id": "not-a-uuid", "review_fingerprint": "f" * 64},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_attempt_id"
    assert not called


def test_approve_forbids_unknown_fields(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes.git_service, "approve_merge_review",
        lambda *a, **k: {"ok": True, "result": {"status": "merged"}},
    )
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/approve",
        json={
            "attempt_id": "12345678-1234-1234-1234-123456789012",
            "review_fingerprint": "f" * 64,
            "auto": True,
        },
    )
    assert resp.status_code == 422


def test_approve_passes_valid_uuid_through_to_the_service(monkeypatch):
    client = _client(monkeypatch)
    captured = {}

    def _approve(group_id, merge_id, *, attempt_id, review_fingerprint, authority):
        captured.update(
            group_id=group_id, merge_id=merge_id, attempt_id=attempt_id,
            review_fingerprint=review_fingerprint, authority=authority,
        )
        return {"ok": True, "result": {"status": "merged", "review_state": "completed"}}

    monkeypatch.setattr(git_routes.git_service, "approve_merge_review", _approve)
    attempt_id = "12345678-1234-1234-1234-123456789012"
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/approve",
        json={"attempt_id": attempt_id, "review_fingerprint": "f" * 64},
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "merged"
    assert captured == {
        "group_id": GROUP_ID, "merge_id": MERGE_ID, "attempt_id": attempt_id,
        "review_fingerprint": "f" * 64, "authority": "human",
    }


def test_reject_requires_a_non_blank_reason(monkeypatch):
    client = _client(monkeypatch)
    called = []
    monkeypatch.setattr(
        git_routes.git_service, "reject_merge_review",
        lambda *a, **k: called.append(1),
    )
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/reject",
        json={"reason": "   ", "provider_id": "prov_1", "provider_pinned": True},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_reason"
    assert not called


def test_reject_requires_provider_pinned_true(monkeypatch):
    client = _client(monkeypatch)
    called = []
    monkeypatch.setattr(
        git_routes.git_service, "reject_merge_review",
        lambda *a, **k: called.append(1),
    )
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/reject",
        json={"reason": "다시 시도해 주세요", "provider_id": "prov_1", "provider_pinned": False},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "provider_not_pinned"
    assert not called


def test_reject_success_calls_service_with_a_start_run_callable(monkeypatch):
    client = _client(monkeypatch)
    captured = {}

    def _reject(group_id, merge_id, *, reason, provider_id, provider_pinned, start_run):
        captured.update(reason=reason, provider_id=provider_id, provider_pinned=provider_pinned)
        run_id = start_run("first message")
        return {"ok": True, "result": {
            "status": "returned_to_resolver", "review_state": None,
            "instruction_generation": 1, "resolver_run_id": run_id,
        }}

    monkeypatch.setattr(git_routes.git_service, "reject_merge_review", _reject)
    monkeypatch.setattr(
        git_routes, "_start_resolve_conflict_run",
        lambda **kw: "aiv_fake_run",
    )
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/reject",
        json={"reason": "다시 시도해 주세요", "provider_id": "prov_1", "provider_pinned": True},
    )
    assert resp.status_code == 200
    body = resp.json()["result"]
    assert body["status"] == "returned_to_resolver"
    assert body["resolver_run_id"] == "aiv_fake_run"
    assert captured == {"reason": "다시 시도해 주세요", "provider_id": "prov_1", "provider_pinned": True}


def test_review_message_requires_provider_pinned_true(monkeypatch):
    client = _client(monkeypatch)
    called = []
    monkeypatch.setattr(
        git_routes.git_service, "send_review_message",
        lambda *a, **k: called.append(1),
    )
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/review-message",
        json={"message": "질문", "provider_id": "prov_1", "provider_pinned": False},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "provider_not_pinned"
    assert not called


def test_review_message_success(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes.git_service, "send_review_message",
        lambda group_id, merge_id, *, message, provider_id, provider_pinned, apply_requested, start_run, allow_test_edits=False: {
            "ok": True, "result": {
                "status": "accepted", "review_state": "resolved_pending_review",
                "run_id": start_run(), "review_fingerprint": "f" * 64, "instruction_generation": 0,
            },
        },
    )
    monkeypatch.setattr(git_routes, "_start_resolve_conflict_run", lambda **kw: "aiv_fake_run")
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/review-message",
        json={"message": "질문", "provider_id": "prov_1", "provider_pinned": True, "apply_requested": False},
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["run_id"] == "aiv_fake_run"


def test_resolve_token_route_forbids_the_auto_field(monkeypatch):
    """A worker token cannot self-grant auto — even as a stray field on /resolve-token."""
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes, "verify_bearer",
        lambda request: {
            "action_scope": "resolve_conflict", "group_id": GROUP_ID, "merge_id": MERGE_ID,
            "token_id": "tok_1", "project": "flowgate",
        },
    )
    called = []
    monkeypatch.setattr(git_routes.git_service, "resolve_conflicts", lambda *a, **k: called.append(1))
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/resolve-token",
        json={"files": [], "complete": True, "auto": True},
    )
    assert resp.status_code == 422   # pydantic extra="forbid" on ResolveBody
    assert not called


_WRITE_PLAN_BODY = {
    "schema_version": "flowgate.write-plan.v1",
    "base_fingerprint": "f" * 64,
    "operations": [{
        "operation_id": "op1", "kind": "create_file", "path": "server/x.py",
        "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
    }],
    "held_test_operations": [],
}


def test_write_plan_token_route_rejects_a_human_jwt(monkeypatch):
    # 0481 TR0009 rev2 / T0008 item 1: this is the ONLY channel by which a
    # review-message write turn's AI run can change the source tree — it must
    # be exactly as worker-token-only as /resolve-token, never reachable with a
    # human's own session.
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "user_id": "usr_admin"},
    )
    called = []
    monkeypatch.setattr(git_routes.git_service, "submit_review_write_plan", lambda *a, **k: called.append(1))
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/write-plan-token",
        json=_WRITE_PLAN_BODY,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "conflict_token_required"
    assert not called


def test_write_plan_token_route_rejects_a_token_bound_to_a_different_merge(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes, "verify_bearer",
        lambda request: {
            "action_scope": "resolve_conflict", "group_id": GROUP_ID, "merge_id": MERGE_ID + 1,
            "token_id": "tok_1", "project": "flowgate",
        },
    )
    called = []
    monkeypatch.setattr(git_routes.git_service, "submit_review_write_plan", lambda *a, **k: called.append(1))
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/write-plan-token",
        json=_WRITE_PLAN_BODY,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "conflict_token_required"
    assert not called


def test_write_plan_token_route_success(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes, "verify_bearer",
        lambda request: {
            "action_scope": "resolve_conflict", "group_id": GROUP_ID, "merge_id": MERGE_ID,
            "token_id": "tok_1", "project": "flowgate", "ai_run_id": "aiv_fake",
        },
    )
    captured = {}

    def _submit(group_id, merge_id, *, plan, ai_run_id=None):
        captured["group_id"] = group_id
        captured["merge_id"] = merge_id
        captured["plan"] = plan
        captured["ai_run_id"] = ai_run_id
        return {"ok": True, "result": {"status": "accepted", "run_id": "aiv_fake"}}

    monkeypatch.setattr(git_routes.git_service, "submit_review_write_plan", _submit)
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/write-plan-token",
        json=_WRITE_PLAN_BODY,
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "accepted"
    assert captured["group_id"] == GROUP_ID
    assert captured["merge_id"] == MERGE_ID
    assert captured["plan"]["schema_version"] == "flowgate.write-plan.v1"
    assert captured["plan"]["operations"][0]["operation_id"] == "op1"
    # 0009-TR rev3 (AI review finding 1): the route must forward the verified
    # token's OWN ai_run_id claim to the service — never trust a value from the
    # request body (WritePlanBody has no such field at all) — so the service
    # can bind the plan to the pending write turn's run.
    assert captured["ai_run_id"] == "aiv_fake"


def test_write_plan_token_route_forwards_a_missing_ai_run_id_as_none(monkeypatch):
    # A resolve_conflict token that somehow carries no ai_run_id claim must not
    # be silently treated as matching — the service is what rejects it (see
    # test_review_gate_write_plan_submission_rejects_a_mismatched_run_id in
    # test_git_integration_0115.py); this route-level test only proves the
    # route does not invent or default a value on the token's behalf.
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes, "verify_bearer",
        lambda request: {
            "action_scope": "resolve_conflict", "group_id": GROUP_ID, "merge_id": MERGE_ID,
            "token_id": "tok_1", "project": "flowgate",
        },
    )
    captured = {}

    def _submit(group_id, merge_id, *, plan, ai_run_id=None):
        captured["ai_run_id"] = ai_run_id
        return {"ok": True, "result": {"status": "accepted", "run_id": "aiv_fake"}}

    monkeypatch.setattr(git_routes.git_service, "submit_review_write_plan", _submit)
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/write-plan-token",
        json=_WRITE_PLAN_BODY,
    )
    assert resp.status_code == 200
    assert captured["ai_run_id"] is None


def test_write_plan_token_route_forbids_unknown_fields(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        git_routes, "verify_bearer",
        lambda request: {
            "action_scope": "resolve_conflict", "group_id": GROUP_ID, "merge_id": MERGE_ID,
            "token_id": "tok_1", "project": "flowgate",
        },
    )
    called = []
    monkeypatch.setattr(git_routes.git_service, "submit_review_write_plan", lambda *a, **k: called.append(1))
    resp = client.post(
        f"/api/v1/groups/{GROUP_ID}/git/merge/{MERGE_ID}/write-plan-token",
        json={**_WRITE_PLAN_BODY, "auto_apply": True},
    )
    assert resp.status_code == 422
    assert not called
