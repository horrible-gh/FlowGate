"""Group AI-run lease exemption of [new chat] and command decisions (flowgate.v02.0032 T#1).

D0006 §3.10.3: ``GroupMutationPolicyMiddleware`` must not answer 423 to
``POST /api/v1/conversations`` or ``POST /api/v1/chat-commands/{id}/decision`` while an AI
run holds the group lease (with or without the /flowgate prefix); every other group
write keeps its 423, the CLI ``POST /chat-commands`` is not exempt, and the route
inventory carries the three explicit reasons. Completion conditions 18 and 25.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    ADMIN, CHAT_DOC, GROUP, PROJECT, RUN_ID, USER, FakeExecutor, bind_worktree, clean_commands_state,  # noqa: F401
    clean_runs, fg_store, init_worktree, make_run, seed, set_policy, use_executor,  # noqa: F401
)

from flowgate.conversation import commands  # noqa: E402
from flowgate.conversation import contracts as c  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import routes  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402

LEASE = {"group_id": GROUP, "run_id": RUN_ID, "token_id": f"tok_{RUN_ID}", "action_scope": "chat"}


# ── path predicates and inventory ────────────────────────────────────────────

@pytest.mark.parametrize("path,method,exempt", [
    ("/flowgate/api/v1/conversations", "POST", True),
    ("/api/v1/conversations", "POST", True),
    ("/flowgate/api/v1/chat-commands/ccr_1/decision", "POST", True),
    ("/api/v1/chat-commands/ccr_1/decision", "POST", True),
    ("/flowgate/api/v1/chat-commands", "POST", False),
    ("/flowgate/api/v1/conversations", "DELETE", False),
    ("/flowgate/api/v1/documents/next-empty", "POST", False),
    ("/flowgate/api/v1/conversations/extra", "POST", False),
    # flowgate.v02.0047: the one AI title of a new chat.
    ("/flowgate/api/v1/conversations/fgp.v02.0001.0012-CH/auto-title", "POST", True),
    ("/api/v1/conversations/fgp.v02.0001.0012-CH/auto-title", "POST", True),
    ("/flowgate/api/v1/conversations/fgp.v02.0001.0012-CH/auto-title", "PATCH", False),
    ("/flowgate/api/v1/conversations/a/b/auto-title", "POST", False),
    ("/flowgate/api/v1/documents/x/conversation/turn", "POST", False),
])
def test_exempt_paths(path, method, exempt):
    assert policy.is_group_lease_exempt_path(path, method) is exempt


def test_policy_control_meaning_is_unchanged():
    for path in ("/flowgate/api/v1/conversations", "/flowgate/api/v1/chat-commands/x/decision",
                 "/flowgate/api/v1/chat-commands"):
        assert policy.is_policy_control_path(path) is False
    assert policy.is_policy_control_path("/flowgate/api/v1/ai-invoke/start") is True


def test_inventory_reasons():
    assert policy.classify_mutation_route("/flowgate/api/v1/conversations", {"POST"}) == (
        "group", "conversation_create_lease_exempt")
    assert policy.classify_mutation_route("/flowgate/api/v1/conversations/{doc_id}/auto-title", {"POST"}) == (
        "group", "conversation_auto_title_lease_exempt")
    assert policy.classify_mutation_route("/flowgate/api/v1/chat-commands/{request_id}/decision", {"POST"}) == (
        "personal", "chat_command_decision")
    assert policy.classify_mutation_route("/flowgate/api/v1/chat-commands", {"POST"}) == (
        "system", "chat_command_worker")


def test_the_application_routes_carry_those_reasons():
    from routers.main import app

    found = {path: (route.mutation_resource, route.mutation_reason)
             for route, path in policy.iter_mutation_routes(app)
             if "chat-commands" in path or "/conversations" in path}
    assert found == {
        "/flowgate/api/v1/conversations": ("group", "conversation_create_lease_exempt"),
        "/flowgate/api/v1/conversations/{doc_id}/auto-title": ("group", "conversation_auto_title_lease_exempt"),
        "/flowgate/api/v1/chat-commands/{request_id}/decision": ("personal", "chat_command_decision"),
        "/flowgate/api/v1/chat-commands": ("system", "chat_command_worker"),
    }


# ── the real middleware with a held lease ────────────────────────────────────

@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch, clean_commands_state, clean_runs):
    seed(fg_store)
    root = init_worktree(tmp_path / "wt")
    bind_worktree(monkeypatch, root)
    use_executor(monkeypatch, FakeExecutor())
    set_policy(monkeypatch, c.POLICY_USER_APPROVAL)
    monkeypatch.setattr(legacy_adapter, "document_file_path",
                        lambda project_id, group_id, doc_code, module: tmp_path / "docs" / doc_code / "document.md")
    monkeypatch.setattr(policy.db_leases, "get_active", lambda group_id: dict(LEASE) if group_id == GROUP else None)
    monkeypatch.setattr(policy.db_leases, "heartbeat", lambda group_id, run_id: None)
    run = make_run(source_root=root)

    app = FastAPI()
    app.add_middleware(policy.GroupMutationPolicyMiddleware)
    app.include_router(routes.router, prefix="/flowgate/api/v1")

    @app.post("/flowgate/api/v1/documents/{doc_id}/rename")
    def ordinary_group_write(doc_id: str):  # stands in for every other group mutation
        return {"ok": True}

    app.dependency_overrides[routes.get_current_user] = lambda: {"user_id": USER, "is_admin": True}
    return {"client": TestClient(app), "run": run}


def test_ordinary_group_writes_are_still_locked(env):
    res = env["client"].post(f"/flowgate/api/v1/documents/{CHAT_DOC}/rename", json={})
    assert res.status_code == 423 and res.json()["error"]["code"] == "GROUP_AI_RUN_LOCKED"


def test_new_chat_is_not_locked_by_the_lease(env):
    res = env["client"].post("/flowgate/api/v1/conversations",
                             json={"project_id": PROJECT, "group_id": GROUP, "title": "during run"})
    assert res.status_code == 201, res.text


def test_without_the_exemption_it_would_have_been_423(env, monkeypatch):
    monkeypatch.setattr(policy, "is_group_lease_exempt_path", lambda path, method="POST": False)
    res = env["client"].post("/flowgate/api/v1/conversations",
                             json={"project_id": PROJECT, "group_id": GROUP, "title": "during run"})
    assert res.status_code == 423


def test_command_decision_is_not_locked_by_the_lease(env):
    row = commands.create_request(env["run"], {"program": "git", "args": ["status"]})
    res = env["client"].post(f"/flowgate/api/v1/chat-commands/{row['request_id']}/decision",
                             json={"decision": "reject"})
    assert res.status_code == 200, res.text
    assert res.json()["request"]["status"] == "rejected"


def test_cli_create_is_not_exempt_and_needs_the_lease_owner(env, monkeypatch):
    from modules.flow_gate.services import token_service

    foreign = {"token_id": "tok_other", "group_id": GROUP, "ai_run_id": "aiv_other", "action_scope": "chat",
               "project": PROJECT}
    monkeypatch.setattr(token_service, "inspect_for_replay", lambda raw: dict(foreign))
    res = env["client"].post("/flowgate/api/v1/chat-commands", json={"program": "git"},
                             headers={"Authorization": "Bearer raw-other"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "GROUP_AI_RUN_OWNER_MISMATCH"

    owner = {"token_id": LEASE["token_id"], "group_id": GROUP, "ai_run_id": RUN_ID, "action_scope": "chat",
             "project": PROJECT}
    monkeypatch.setattr(token_service, "inspect_for_replay", lambda raw: dict(owner))
    monkeypatch.setattr(legacy_adapter, "verify_token", lambda raw: dict(owner))
    res = env["client"].post("/flowgate/api/v1/chat-commands",
                             json={"program": "git", "args": ["status"], "wait_seconds": 0},
                             headers={"Authorization": "Bearer raw-owner"})
    assert res.status_code == 200, res.text
    assert res.json()["request"]["status"] == "pending_approval"
