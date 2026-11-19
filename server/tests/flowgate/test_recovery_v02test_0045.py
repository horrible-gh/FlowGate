"""0045: explicit recovery on the real migrated SQLite Graph service.

Only Git facts and worker launches are controlled; mutations, guards, attempts,
report allocation, stop and run creation use the actual services.
"""
import copy
import json
import os
from unittest.mock import Mock

import pytest
from test_fg_card_settings import legacy_cards, FA, _sql, db_path, decided, env, node, put_draft
from test_chat_control_v02test_0023 import _group, _draft, _ai_card, _write_instruction, gate_policy
from flowgate.chat_control import service
from flowgate.graph_run import recovery, recovery_basis as basis, recovery_store as store, workspace
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store

pytestmark = pytest.mark.usefixtures("legacy_cards")
HEAD = "b" * 40
OLD = "a" * 40


@pytest.fixture(autouse=True)
def restore_agent_pepper():
    original = os.environ.get("FLOWGATE_AGENT_PEPPER_ID")
    yield
    if original is None:
        os.environ.pop("FLOWGATE_AGENT_PEPPER_ID", None)
    else:
        os.environ["FLOWGATE_AGENT_PEPPER_ID"] = original


@pytest.fixture
def running(env, decided, gate_policy, monkeypatch):
    put_draft(env, FA, 0, [node("tmp_r", "R")])
    key = service.place(_group(), "u_kim", {"cards": [{}]})["cards"][0]["node_key"]
    from flowgate.card_documents import repository
    instruction = next(r["doc_id"] for r in repository.rows(FA, [key]) if r["slot"] == "instruction")
    _write_instruction(instruction, "Implement the requested change.")
    _ai_card(key)
    result = service.start(_group(), "u_kim")
    rid = result["run"]["run_id"]
    aid = run_store.active_attempt_id(rid, key)
    assert aid
    head = {"available": True, "ref": "refs/heads/group", "commit": HEAD,
            "work_base_ref": "feature", "work_base_commit": OLD}
    monkeypatch.setattr(basis, "git_facts", lambda group: dict(head))
    monkeypatch.setattr(workspace.git, "_base_root_of", lambda project: "repo")
    monkeypatch.setattr(workspace.git, "_rev_parse", lambda root, ref: ref.split("^")[0])
    monkeypatch.setattr(workspace.git, "_is_ancestor", lambda *args: False)
    return {**env, "key": key, "run_id": rid, "attempt_id": aid, "head": head}


def body(state, node=True, key="request-1"):
    return {"request_id": key, "expected_context": basis.view(_group(), state["key"] if node else None)["context"]}


def hold_result(state):
    db, aid, rid, key = state["db"], state["attempt_id"], state["run_id"], state["key"]
    from flowgate.card_documents import repository
    doc = next(r["doc_id"] for r in repository.rows(FA, [key]) if r["slot"] == "report")
    _write_instruction(doc, "Previous result, held for review.")
    _sql(db, "UPDATE documents SET doc_review_status = 'rejected' WHERE doc_id = ?", [doc])
    _sql(db, "UPDATE graph_attempt_runs SET status = 'ended' WHERE attempt_id = ?", [aid])
    _sql(db, "UPDATE graph_node_attempts SET input_commit = ? WHERE attempt_id = ?", [OLD, aid])
    _sql(db, "UPDATE graph_node_runs SET status = 'review', result_doc_id = ? WHERE run_id = ? AND node_key = ?", [doc, rid, key])
    _sql(db, "UPDATE graph_runs SET status = 'idle', idle_reason = 'needs_intervention' WHERE run_id = ?", [rid])
    return doc


def test_read_separates_fixed_input_from_retry_and_does_not_mutate(running, monkeypatch):
    hold_result(running)
    forbid = Mock(side_effect=AssertionError("read must not provision"))
    monkeypatch.setattr(workspace, "prepare", forbid)
    monkeypatch.setattr(workspace.git, "ensure_worktree", forbid)
    before = _sql(running["db"], "SELECT * FROM graph_node_attempts")
    result = basis.view(_group(), running["key"])
    assert result["new_attempt"]["commit"] == HEAD
    assert result["existing_attempt"]["commit"] == OLD
    assert result["group_head"]["commit"] == HEAD
    assert before == _sql(running["db"], "SELECT * FROM graph_node_attempts")
    forbid.assert_not_called()


def test_git_failure_is_unknown_not_main(running, monkeypatch):
    monkeypatch.setattr(basis, "git_facts", lambda group: basis.unknown("git_disabled"))
    result = basis.view(_group(), running["key"])
    assert result["new_attempt"] == basis.unknown("git_disabled")
    with pytest.raises(GraphApiError, match="recovery_basis_unavailable"):
        recovery.retry(_group(), "u_kim", running["key"], body(running), lambda: None)


def test_retry_preserves_held_report_and_creates_one_attempt(running):
    doc = hold_result(running)
    payload = body(running)
    first = recovery.retry(_group(), "u_kim", running["key"], payload, lambda: None)
    assert first["created"] and first["new_attempt_id"] != running["attempt_id"]
    assert first["selected_basis"]["commit"] == HEAD
    old = workspace.attempt(running["attempt_id"])
    assert old["status"] == "invalidated" and old["input_commit"] == OLD
    assert any(doc in r["payload_json"] for r in _sql(running["db"], "SELECT payload_json FROM graph_run_events WHERE cause = 'explicit_retry'"))
    assert _sql(running["db"], "SELECT doc_review_status FROM documents WHERE doc_id = ?", [doc])[0]["doc_review_status"] == "rejected"
    from flowgate.card_documents import repository
    fresh = next(r["doc_id"] for r in repository.rows(FA, [running["key"]]) if r["slot"] == "report")
    assert fresh != doc
    assert not any(r["result_doc_id"] == doc for r in run_store.node_runs(first["run_id"]))
    again = recovery.retry(_group(), "u_kim", running["key"], payload, lambda: None)
    assert again["replayed"] and again["new_attempt_id"] == first["new_attempt_id"]
    assert len(_sql(running["db"], "SELECT * FROM graph_node_attempts WHERE node_key = ?", [running["key"]])) == 2
    with pytest.raises(GraphApiError) as caught:
        recovery.retry(_group(), "different-user", running["key"], payload, lambda: None)
    assert caught.value.code == "recovery_request_conflict"


def test_retry_refuses_a_live_worker_and_changed_source(running):
    with pytest.raises(GraphApiError) as caught:
        recovery.retry(_group(), "u_kim", running["key"], body(running), lambda: None)
    assert caught.value.params["reason"] == "card_running"
    hold_result(running)
    payload = body(running)
    running["head"]["commit"] = "c" * 40
    with pytest.raises(GraphApiError) as caught:
        recovery.retry(_group(), "u_kim", running["key"], payload, lambda: None)
    assert caught.value.code == "recovery_context_changed"
    assert workspace.attempt(running["attempt_id"])["status"] == "active"


def test_recheck_refusal_rolls_back_before_invalidation(running):
    hold_result(running)
    def deny():
        raise GraphApiError(403, "flow_access_denied")
    with pytest.raises(GraphApiError):
        recovery.retry(_group(), "u_kim", running["key"], body(running), deny)
    assert workspace.attempt(running["attempt_id"])["status"] == "active"


def test_restart_uses_a_new_run_and_preserves_the_old_one(running, monkeypatch):
    hold_result(running)
    # No Git workspace was materialized by the mock worker.
    monkeypatch.setattr(workspace, "cleanup_for_stop", lambda aid: True)
    request = body(running, node=False)
    result = recovery.restart(_group(), "u_kim", request, lambda: None)
    assert result["stop_confirmed"] and not result["pending"]
    assert result["new_run_id"] != running["run_id"]
    old = run_store.run_by_id(running["run_id"])
    assert old["status"] == "idle" and old["idle_reason"] == "stopped"
    assert old["active_group_id"] is None
    assert workspace.attempt(running["attempt_id"]) is not None
    repeated = recovery.restart(_group(), "u_kim", request, lambda: None)
    assert repeated["new_run_id"] == result["new_run_id"] and repeated["replayed"]
    assert len(_sql(running["db"], "SELECT * FROM graph_runs")) == 2


def test_restart_pending_does_not_create_a_run(running, monkeypatch):
    hold_result(running)
    monkeypatch.setattr(workspace, "cleanup_for_stop", lambda aid: False)
    request = body(running, node=False)
    pending = recovery.restart(_group(), "u_kim", request, lambda: None)
    assert pending["pending"] and pending["new_run_id"] is None
    assert run_store.run_by_id(running["run_id"])["status"] == "stopping"
    assert len(_sql(running["db"], "SELECT * FROM graph_runs")) == 1
    monkeypatch.setattr(workspace, "cleanup_for_stop", lambda aid: True)
    result = recovery.restart(_group(), "u_kim", request, lambda: None)
    assert result["new_run_id"] and not result["pending"]


def test_restart_source_change_after_stop_blocks_start(running, monkeypatch):
    hold_result(running)
    def cleanup(aid):
        running["head"]["commit"] = "c" * 40
        return True
    monkeypatch.setattr(workspace, "cleanup_for_stop", cleanup)
    with pytest.raises(GraphApiError) as caught:
        recovery.restart(_group(), "u_kim", body(running, node=False), lambda: None)
    assert caught.value.code == "recovery_context_changed"
    assert run_store.current_run(FA)["run_id"] == running["run_id"]
    assert len(_sql(running["db"], "SELECT * FROM graph_runs")) == 1


def test_predecessor_and_join_reads_do_not_merge(running, monkeypatch):
    monkeypatch.setattr(workspace, "_inputs", lambda row: [("a", "first", OLD)])
    result = basis.view(_group(), running["key"])
    assert result["new_attempt"]["commit"] == OLD
    assert result["new_attempt"]["input_attempt_ids"] == ["first"]
    monkeypatch.setattr(workspace, "_inputs", lambda row: [("a", "first", OLD), ("b", "second", HEAD)])
    assert basis.view(_group(), running["key"])["new_attempt"]["reason"] == "join_requires_merge"


def test_workspace_uses_pinned_retry_and_refuses_changed_predecessor(running, monkeypatch):
    selected = basis.view(_group(), running["key"])["new_attempt"]
    store.pin(running["attempt_id"], FA, selected)
    row = workspace.attempt(running["attempt_id"])
    proposed = {"input_commit": OLD, "input_source": "run_base", "input_attempt_ids": [], "base_since": 1}
    assert store.workspace_input(row, "repo", proposed)["input_commit"] == HEAD
    monkeypatch.setattr(workspace, "_inputs", lambda row: [("upstream", "other", OLD)])
    with pytest.raises(Exception) as caught:
        store.workspace_input(row, "repo", proposed)
    assert caught.value.details["reason"] == "recovery_context_changed"
    with pytest.raises(Exception) as caught:
        store.workspace_input(row, "repo", {**proposed, "join_inputs": [HEAD]})
    assert caught.value.details["reason"] == "recovery_join_requires_merge"


def test_new_tools_are_registered_and_both_transports_authenticate(monkeypatch):
    from flowgate.chat_control import recovery_tools, authz, routes, tools
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    names = {d["name"] for d in tools.descriptors()}
    assert {"read_flow_recovery_basis", "retry_flow_card", "restart_flow_run"} <= names
    seen = []
    def deny(*args, **kwargs):
        seen.append(kwargs["write"])
        raise GraphApiError(403, "flow_access_denied")
    monkeypatch.setattr(authz, "caller_from_run", deny)
    monkeypatch.setattr(authz, "caller_from_bearer", deny)
    for fn, value in [(recovery_tools.basis_call, {}), (recovery_tools.retry_call, {"node_key": "x"}),
                      (recovery_tools.restart_call, {})]:
        status, result = fn({"group_id": FA}, value)
        assert status == 403 and result["error"]["code"] == "flow_access_denied"
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.get(f"/api/v2/chat/flows/{FA}/recovery-basis").status_code == 403
        assert client.get(f"/api/v2/chat/flows/{FA}/nodes/x/basis").status_code == 403
        assert client.post(f"/api/v2/chat/flows/{FA}/nodes/x/retry", content=b"not json").status_code == 403
        assert client.post(f"/api/v2/chat/flows/{FA}/restart", content=b"not json").status_code == 403
    assert seen == [False, True, True, False, False, True, True]



def test_pending_restart_blocks_other_commands(running, monkeypatch):
    hold_result(running)
    monkeypatch.setattr(workspace, "cleanup_for_stop", lambda aid: False)
    request = body(running, node=False)
    assert recovery.restart(_group(), "u_kim", request, lambda: None)["pending"]
    from modules.flow_gate.graph_workflow.services import run_service
    with pytest.raises(GraphApiError) as caught:
        run_service.start_run(_group(), "u_kim", None, "full")
    assert caught.value.code == "recovery_in_progress"
    with pytest.raises(GraphApiError) as caught:
        recovery.restart(_group(), "u_kim", body(running, node=False, key="other"), lambda: None)
    assert caught.value.code == "recovery_in_progress"


def test_concurrent_retry_replays_one_committed_attempt(running):
    from concurrent.futures import ThreadPoolExecutor
    hold_result(running)
    request = body(running)
    group = _group()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(recovery.retry, group, "u_kim", running["key"], request, lambda: None)
                   for _ in range(2)]
        results = [f.result(timeout=30) for f in futures]
    assert results[0]["new_attempt_id"] == results[1]["new_attempt_id"]
    assert sum(bool(r["replayed"]) for r in results) == 1


def test_start_with_stale_context_does_not_resume_old_attempt(running):
    hold_result(running)
    expected = basis.view(_group(), running["key"])["context"]
    running["head"]["commit"] = "c" * 40
    with pytest.raises(GraphApiError) as caught:
        service.start(_group(), "u_kim", {"expected_context": expected})
    assert caught.value.code == "recovery_context_changed"
    assert run_store.current_run(FA)["status"] == "idle"


def test_retry_context_includes_card_settings_version(running):
    hold_result(running)
    request = body(running)
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    with_flow_guard(FA, lambda: repository.bump_version(FA, running["key"]))
    with pytest.raises(GraphApiError) as caught:
        recovery.retry(_group(), "u_kim", running["key"], request, lambda: None)
    assert caught.value.code == "recovery_context_changed"


def test_restart_failure_keeps_holder_and_can_be_replaced_by_fresh_request(running, monkeypatch):
    hold_result(running)
    monkeypatch.setattr(workspace, "cleanup_for_stop", lambda aid: True)
    real = recovery.run_service.policy_set_for_run
    def unavailable(flow_id):
        raise GraphApiError(409, "run_policy_undecided")
    monkeypatch.setattr(recovery.run_service, "policy_set_for_run", unavailable)
    request = body(running, node=False)
    with pytest.raises(GraphApiError):
        recovery.restart(_group(), "u_kim", request, lambda: None)
    assert run_store.current_run(FA)["run_id"] == running["run_id"]
    blocked = recovery.restart(_group(), "u_kim", request, lambda: None)
    assert blocked["blocked"] and not blocked["pending"]
    monkeypatch.setattr(recovery.run_service, "policy_set_for_run", real)
    result = recovery.restart(_group(), "u_kim", body(running, node=False, key="fresh"), lambda: None)
    assert result["new_run_id"] != running["run_id"]



def test_first_start_accepts_inspected_draft_basis(env, decided, gate_policy, monkeypatch):
    put_draft(env, FA, 0, [node("tmp_r", "R")])
    card = service.place(_group(), "u_kim", {"cards": [{}]})["cards"][0]
    _ai_card(card["node_key"])
    head = {"available": True, "ref": "refs/heads/group", "commit": HEAD}
    monkeypatch.setattr(basis, "git_facts", lambda group: dict(head))
    expected = basis.view(_group(), card["node_key"])["context"]
    _write_instruction(card["instruction"]["doc_id"], "Written after inspecting the input.")
    result = service.start(_group(), "u_kim", {"expected_context": expected})
    assert result["run"]["run_id"]
    assert store.input_of(result["run"]["run_id"])["commit"] == HEAD


def test_concurrent_restart_has_only_one_new_run(running, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    hold_result(running)
    monkeypatch.setattr(workspace, "cleanup_for_stop", lambda aid: True)
    request = body(running, node=False)
    group = _group()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(recovery.restart, group, "u_kim", request, lambda: None) for _ in range(2)]
        results = [f.result(timeout=45) for f in futures]
    ids = {r["new_run_id"] for r in results if r.get("new_run_id")}
    assert len(ids) == 1
    assert len(_sql(running["db"], "SELECT * FROM graph_runs")) == 2


def test_chat_adapter_keeps_recheck_tickets_until_exit(monkeypatch):
    from types import SimpleNamespace
    from flowgate.chat_control import recovery as chat, authz
    from flowgate.card_document_edit import chat_authz, chat_gate
    caller = SimpleNamespace(scope=SimpleNamespace(user_id="user", project_id="project"),
                             group={"group_id": FA}, token={}, run={})
    tickets, released = [], []
    monkeypatch.setattr(authz, "write_caller", lambda *a: caller)
    monkeypatch.setattr(authz, "has_permission", lambda *a: True)
    def check(run, token, holder):
        holder.ticket = object()
        tickets.append(holder.ticket)
        if len(tickets) == 2:
            raise GraphApiError(401, "unauthorized")
    monkeypatch.setattr(chat_authz, "chat_recheck", check)
    monkeypatch.setattr(chat_gate, "release", released.append)
    def restart(group, user, body, recheck):
        recheck()
        assert not released
        recheck()
    monkeypatch.setattr(chat.shared, "restart", restart)
    with pytest.raises(GraphApiError) as caught:
        chat.change(caller, {})
    assert caught.value.status == 401 and tickets == released and len(tickets) == 2


def test_foreign_flow_rejected_before_target_lookup(monkeypatch):
    from flowgate.chat_control import authz
    from flowgate.graph_read import authz as graph
    monkeypatch.setattr(graph.chat_authz, "token_live", lambda token: graph.chat_authz.LIVE)
    monkeypatch.setattr(graph.chat_authz, "check_run_token_binding", lambda *a: None)
    monkeypatch.setattr(graph, "_live", lambda *a: None)
    forbidden = Mock(side_effect=AssertionError("foreign target must not be read"))
    monkeypatch.setattr(graph.canvas_service, "load_flow", forbidden)
    with pytest.raises(GraphApiError) as caught:
        authz.read_caller({"action_scope": "chat", "group_id": FA}, "foreign.flow", {})
    assert caught.value.status == 403 and caught.value.code == "flow_access_denied"
    forbidden.assert_not_called()
