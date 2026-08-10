"""Durable lease, central mutation policy, and route inventory contracts (0378)."""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import group_ai_leases as leases  # noqa: E402
from modules.flow_gate.services import ai_invoke_service  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402

GROUP = "flowgate.default.0378"
OTHER_GROUP = "flowgate.default.9999"
LEASE = {
    "group_id": GROUP,
    "project_id": "flowgate",
    "run_id": "aiv_owner",
    "chain_id": "aiv_chain",
    "token_id": "tok_owner",
    "action_scope": "new",
    "worker_identity": "usr_ai",
    "state": "active",
    "generation": 2,
    "expires_at": "2999-01-01T00:00:00+00:00",
}
OWNER = policy.MutationPrincipal(
    kind="worker", token_id="tok_owner", group_id=GROUP,
    run_id="aiv_owner", action_scope="new",
)


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres"])
def test_group_ai_lease_migration_exists_for_every_supported_dialect(dialect):
    path = _SERVER_DIR / "sql" / "migrations" / dialect / "077_group_ai_leases.sql"
    sql = path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS group_ai_leases" in sql
    for column in ("group_id", "project_id", "run_id", "state", "generation", "expires_at"):
        assert column in sql
    assert "idx_group_ai_leases_run" in sql
    assert "idx_group_ai_leases_project" in sql
    assert "idx_group_ai_leases_expiry" in sql


def test_human_mutation_is_structured_423(monkeypatch):
    monkeypatch.setattr(policy.db_leases, "get_active", lambda gid: dict(LEASE) if gid == GROUP else None)
    with pytest.raises(policy.MutationPolicyError) as exc_info:
        policy.assert_group_mutation_allowed(GROUP, policy.human_principal({"user_id": "usr_pm"}), "edit")
    exc = exc_info.value
    assert exc.status_code == 423
    assert exc.body() == {"error": {
        "code": "GROUP_AI_RUN_LOCKED",
        "message": "Modification not allowed while an AI run owns this group.",
        "group_id": GROUP,
        "run_id": "aiv_owner",
        # 0401 NR0003 §3 / T0004 작업 4: the lease's OWN run is not in this process's
        # registry (never seeded here), so is_run_live reports False -- an orphaned lease.
        "run_live": False,
    }}


def test_exact_owner_worker_is_allowed_and_heartbeats(monkeypatch):
    calls = []
    monkeypatch.setattr(policy.db_leases, "get_active", lambda _gid: dict(LEASE))
    monkeypatch.setattr(policy.db_leases, "heartbeat", lambda gid, rid: calls.append((gid, rid)) or True)
    result = policy.assert_group_mutation_allowed(GROUP, OWNER, "advance")
    assert result["run_id"] == "aiv_owner"
    assert calls == [(GROUP, "aiv_owner")]


@pytest.mark.parametrize("field,value", [
    ("token_id", "tok_other"),
    ("run_id", "aiv_other"),
    ("action_scope", "review"),
    ("group_id", OTHER_GROUP),
])
def test_non_owner_worker_is_403(monkeypatch, field, value):
    monkeypatch.setattr(policy.db_leases, "get_active", lambda _gid: dict(LEASE))
    principal = policy.MutationPrincipal(**{**OWNER.__dict__, field: value})
    with pytest.raises(policy.MutationPolicyError) as exc_info:
        policy.assert_group_mutation_allowed(GROUP, principal, "write")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error["code"] == "GROUP_AI_RUN_OWNER_MISMATCH"


def test_other_group_remains_parallel(monkeypatch):
    monkeypatch.setattr(policy.db_leases, "get_active", lambda gid: dict(LEASE) if gid == GROUP else None)
    assert policy.assert_group_mutation_allowed(OTHER_GROUP, policy.human_principal(), "edit") is None


def _guarded_client(monkeypatch, principal):
    app = FastAPI()
    side_effects = []

    @app.post("/api/v1/groups/{group_id}/mutate")
    def mutate(group_id: str):
        side_effects.append(group_id)
        return {"ok": True}

    monkeypatch.setattr(policy, "principal_from_request", lambda _request: principal)
    monkeypatch.setattr(policy.db_leases, "get_active", lambda gid: dict(LEASE) if gid == GROUP else None)
    monkeypatch.setattr(policy.db_leases, "heartbeat", lambda *_args: True)
    app.add_middleware(policy.GroupMutationPolicyMiddleware)
    return TestClient(app), side_effects


def test_middleware_rejects_before_side_effect(monkeypatch):
    client, side_effects = _guarded_client(monkeypatch, policy.human_principal())
    response = client.post(f"/api/v1/groups/{GROUP}/mutate")
    assert response.status_code == 423
    assert response.json()["error"]["code"] == "GROUP_AI_RUN_LOCKED"
    assert side_effects == []


def test_middleware_allows_owner_worker(monkeypatch):
    client, side_effects = _guarded_client(monkeypatch, OWNER)
    response = client.post(f"/api/v1/groups/{GROUP}/mutate")
    assert response.status_code == 200
    assert side_effects == [GROUP]


def test_ai_start_acquires_lease_before_issuing_any_token():
    source = inspect.getsource(ai_invoke_service.start_run)
    acquire = source.index("db_group_ai_leases.acquire")
    assert acquire < source.index("_call_issue_builder")
    assert acquire < source.index("token_service.issue")
    assert source.index("db_group_ai_leases.activate") < source.index("thread.start()")


def test_hop_handoff_marks_releasing_before_successor_spawn():
    finalize_source = inspect.getsource(ai_invoke_service._finalize_run)
    spawn_source = inspect.getsource(ai_invoke_service._maybe_auto_resume_hop)
    assert "db_group_ai_leases.begin_handoff" in finalize_source
    assert "db_group_ai_leases.release" in finalize_source
    assert "_spawn_auto_resume" in spawn_source


def test_expiry_rule_recovers_restart_stale_rows():
    assert leases._expired({"expires_at": "2000-01-01T00:00:00+00:00"}) is True
    assert leases._expired({"expires_at": "2999-01-01T00:00:00+00:00"}) is False


def test_required_missing_surfaces_classify_as_group():
    paths = [
        "/api/v1/groups/{group_id}",
        "/api/v1/groups/{gid}/transitions/{action}",
        "/api/v1/documents/{doc_id}/transitions/{action}",
        "/api/v1/workflow/{doc_id}/decide",
        "/api/v1/workflow/{doc_id}/advance",
        "/api/v1/groups/{group_id}/git/finalize",
        "/api/v1/projects/{project_id}/files/upload",
        "/api/v1/documents/{doc_id}/conversation/turns",
        "/api/v1/documents/{doc_id}/test-run",
        "/flowgate/api/v1/inbox",
        "/flowgate/api/v1/remote/write",
    ]
    for path in paths:
        resource, reason = policy.classify_mutation_route(path, {"POST"})
        assert resource == "group", (path, resource, reason)


def test_application_has_no_unclassified_mutation_route():
    from routers.main import app

    mutation_routes = [route for route, _path in policy.iter_mutation_routes(app)]
    assert mutation_routes
    missing = [route.path for route in mutation_routes if not getattr(route, "mutation_resource", None)]
    assert missing == []
    for route in mutation_routes:
        assert route.mutation_resource in {"group", "project_substrate", "personal", "system"}
        assert getattr(route, "mutation_reason", None)
        if route.mutation_resource == "group":
            assert route.group_resolver == "path_query_body_doc_or_worker"