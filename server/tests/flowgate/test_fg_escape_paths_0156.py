"""flowgate.v02.0156 T#4 (0011-T): D1 / D6 / D7 / D8 escape paths of an idle or stopping run.

Light checks only: the real SQLite DB and Graph routes of ``test_fg_card_settings`` with a stubbed AI starter (no provider,
no Git, no wait). D8's GUI half is ``client_gui/tests/features/designer/stopRetry.0156.spec.ts``."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from test_fg_card_documents_memos_0056 import _ai_card
from test_fg_card_settings import (  # noqa: F401
    legacy_cards, pre_feature_attempts, AI, FA, _h, _sql, db_path, decided, edge, env, node, put_draft, _start,
)
from test_fg_card_settings_review import _admin_and_reviewer

pytestmark = pytest.mark.usefixtures("legacy_cards")


def _fail_start(env, monkeypatch, exc):
    from modules.flow_gate.graph_workflow.services import node_dispatch
    calls = []

    def starter(job):
        calls.append(job)
        raise exc
    monkeypatch.setattr(node_dispatch, "_ai_starter", starter)
    _admin_and_reviewer(env)
    key = _ai_card(env, (("instruction", "omit"),))
    _start(env)
    return key, calls


def _cmd(env, mode):
    return env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=_h())


def _run(env):
    return _sql(env["db"], "SELECT run_id, status, idle_reason, revision_no, last_event_seq FROM graph_runs WHERE group_id = ?", (FA,))[0]


def _nodes(env):
    return [(r["node_key"], r["status"], r["dispatch_json"] if "dispatch_json" in r else None) for r in _sql(env["db"], "SELECT * FROM graph_node_runs")]


def _attempts(env):
    return len(_sql(env["db"], "SELECT 1 FROM graph_node_attempts"))


# ── D6: an AI start that failed is started again by the next explicit command ──────────────────────────────────────────

@pytest.mark.parametrize("kind", ["reject", "exc"])
def test_d6_pre_feature_card_whose_ai_start_failed_is_dispatched_again(kind, pre_feature_attempts, env, decided, monkeypatch):
    exc = (HTTPException(409, detail={"code": "scratch_path_conflict", "message": "m", "retryable": True})
           if kind == "reject" else RuntimeError("boom"))
    key, calls = _fail_start(env, monkeypatch, exc)
    assert (_run(env)["status"], _run(env)["idle_reason"]) == ("idle", "error")
    node_row = _sql(env["db"], "SELECT dispatch_error_code FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert node_row["dispatch_error_code"] == "ai_invoke_failed"
    before, attempts = len(calls), _attempts(env)
    assert _cmd(env, "step").status_code == 201
    assert len(calls) > before                                  # the AI start was asked for again
    assert _attempts(env) == attempts                            # on the same attempt: no duplicate attempt


def test_d6_a_held_card_or_another_failure_code_is_not_dispatched_again(pre_feature_attempts, env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import node_dispatch
    key, calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    before = len(calls)
    real = node_dispatch.graph_execution_hooks.call
    monkeypatch.setattr(node_dispatch.graph_execution_hooks, "call",
                        lambda name, *a, **k: (key,) if name == "held_nodes" else real(name, *a, **k))
    assert _cmd(env, "step").status_code == 201
    assert len(calls) == before                                  # hold keeps it
    monkeypatch.setattr(node_dispatch.graph_execution_hooks, "call", real)
    _sql(env["db"], "UPDATE graph_node_runs SET dispatch_error_code = 'token_issue_failed' WHERE node_key = ?", (key,))
    assert _cmd(env, "step").status_code == 201
    assert len(calls) == before                                  # only the AI start failure code is retried here


def _executions(env):
    return _sql(env["db"], "SELECT exec_id, attempt_id, status FROM graph_attempt_runs ORDER BY exec_id")


@pytest.mark.parametrize("activity", ["requested", "running"])
def test_d6_a_live_execution_on_the_attempt_is_never_started_again(activity, pre_feature_attempts, env, decided, monkeypatch):
    key, calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    attempt = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'", (key,))[0]
    run = _run(env)
    _sql(env["db"], "INSERT INTO graph_attempt_runs (exec_id, attempt_id, run_id, kind, status, created_at, request_json) "
                    "VALUES ('live_exec', ?, ?, 'write', ?, 'now', '{}')", (attempt["attempt_id"], run["run_id"], activity))
    from flowgate.graph_run import execution
    replayed = []                                                            # the durable redelivery of a requested row (not a dispatch)
    monkeypatch.setattr(execution, "replay", lambda row: replayed.append(row["exec_id"]))
    before_calls, before_exec = len(calls), _executions(env)
    assert [e["status"] for e in before_exec if e["exec_id"] == "live_exec"] == [activity]
    assert _cmd(env, "step").status_code == 201
    assert len(calls) == before_calls                                        # start_ai is not asked again
    assert _executions(env) == before_exec                                    # no graph_attempt_runs row made; the live one stays
    assert set(replayed) <= {"live_exec"}                                    # nothing but the existing execution is redelivered


def test_d6_a_card_waiting_for_its_answers_is_not_dispatched_again(pre_feature_attempts, env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import node_dispatch
    key, calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    before_calls, before_exec = len(calls), _executions(env)
    real = node_dispatch.graph_execution_hooks.call
    monkeypatch.setattr(node_dispatch.graph_execution_hooks, "call",
                        lambda name, *a, **k: node_dispatch.QUESTION_PENDING if name == "write_failure" else real(name, *a, **k))
    assert _cmd(env, "step").status_code == 201
    assert len(calls) == before_calls                                        # question_pending: its answers retry it once
    assert _executions(env) == before_exec


def test_d6_assigned_attempt_is_still_started_again(env, decided, monkeypatch):
    key, calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    before = len(calls)
    assert _cmd(env, "step").status_code == 201
    assert len(calls) > before


# ── D1: a new revision after an open card (v02test.0018: adopted; the open card keeps its attempt) ─────────────────────

def _confirm_new_card_after(env, key):
    """A new card after ``key`` in a confirmed revision 2 (the run still runs revision 1)."""
    g = env["client"].get(f"/api/v1/flows/{FA}/graph", headers=_h()).json()["latest_revision"]
    nodes = [{"node_key": n["node_key"], "client_key": None, "node_type": n["node_type"], "doc_type": n["doc_type"],
              "palette_item_id": None, "label": n["label"], "assignee": n.get("assignee")} for n in g["nodes"]]
    edges = [{"edge_id": e["edge_id"], "client_key": None, "source": e["source"], "target": e["target"], "condition": None}
             for e in g["edges"]]
    nodes.append(node("tmp_new", "T"))
    edges.append(edge("tmp_e2", key, "tmp_new"))
    assert env["client"].put(f"/api/v1/flows/{FA}/graph/draft", json={"draft_version": 0, "nodes": nodes, "edges": edges},
                             headers=_h()).status_code == 200
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1, "base_revision_no": 1},
                              headers=_h()).status_code == 201


def test_d1_open_card_does_not_block_the_new_revision_and_keeps_its_attempt(env, decided, monkeypatch):
    """v02test.0018: the open card (its AI start failed) no longer refuses revision 2 as a whole. Its definition and input
    edges are unchanged, so the command adopts revision 2; the card stays open on its revision-1 attempt (or a reopened
    one) and the card added behind it waits as pending."""
    from modules.flow_gate.graph_workflow.api import graph_messages
    key, _calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    run_id = _run(env)["run_id"]
    rows_before = {r["node_key"] for r in _sql(env["db"], "SELECT node_key FROM graph_node_runs")}
    _confirm_new_card_after(env, key)
    res = _cmd(env, "step")
    assert res.status_code == 201, res.text
    assert _run(env)["revision_no"] == 2
    after = {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}
    added = set(after) - rows_before
    assert len(added) == 1 and after[added.pop()] == "pending"               # behind the open card: not opened yet
    assert after[key] == "open"
    # the refusal message (still used by the remaining blockers) tells the way out
    for locale in ("ko", "en", "ja"):
        assert "Time Machine" in graph_messages.RUN_EDIT_MESSAGES["graph_revision_migration_blocked"][locale]


def _mount_time_machine(env):
    """This isolated app mounts the Graph routes only; the Time Machine router is the production one."""
    from flowgate.graph_time_machine import routes as time_machine_routes
    env["client"].app.include_router(time_machine_routes.router)


def _graph_edges(env):
    g = env["client"].get(f"/api/v1/flows/{FA}/graph", headers=_h()).json()["latest_revision"]
    return g, sorted((e["source"], e["target"]) for e in g["edges"])


def test_d1_the_done_upstream_card_s_time_machine_frees_the_blocked_run_and_the_new_revision_is_adopted(
        env, decided, monkeypatch):
    """The Time Machine run for real (SQLite, the stubbed AI start, no Git: the plan has no commit to revert) with
    revision 2 confirmed after the open card (R -> T, the AI start failed): the Time Machine of the completed card before
    it (the live root R, the menu's ``done`` card) puts R's range back — T becomes pending, its attempt invalidated — and
    the next command adopts revision 2 and the new card and every edge stay. (v02test.0018: the open card alone no
    longer blocks the adoption; see the test above.)"""
    key, calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    run_id = _run(env)["run_id"]
    _confirm_new_card_after(env, key)
    graph, edges_before = _graph_edges(env)
    root = next(n["node_key"] for n in graph["nodes"] if n["doc_type"] == "R")
    states = {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}
    assert states[root] == "done" and states[key] == "open"                  # the menu's `done` card; T is open
    # GET-free check of the pre-state the GUI menu needs: the run is idle (the menu item is enabled only then)
    assert (_run(env)["status"], _run(env)["idle_reason"]) == ("idle", "error")

    _mount_time_machine(env)
    res = env["client"].post(f"/api/v1/runs/{run_id}/nodes/{root}/time-machine", json={}, headers=_h())
    assert res.status_code == 200, res.text
    assert key in res.json()["time_machine"]["impact"]["node_keys"]          # the range includes the open card
    after = {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}
    assert after[key] == "pending"                                           # the open card is back to pending
    attempts = _sql(env["db"], "SELECT status FROM graph_node_attempts WHERE node_key = ?", (key,))
    assert attempts and all(a["status"] == "invalidated" for a in attempts)  # no active attempt remains

    before_calls = len(calls)
    rows_before = {r["node_key"] for r in _sql(env["db"], "SELECT node_key FROM graph_node_runs")}   # revision 1's cards
    adopted = _cmd(env, "step")
    assert adopted.status_code == 201, adopted.text
    assert _run(env)["revision_no"] == 2                                     # revision 2 adopted
    graph2, edges_after = _graph_edges(env)
    assert edges_after == edges_before                                       # edges kept (R -> T, T -> new)
    rows_after = {r["node_key"] for r in _sql(env["db"], "SELECT node_key FROM graph_node_runs")}
    (new_key,) = rows_after - rows_before                                    # the new card became a run row on adoption
    assert new_key in {n["node_key"] for n in graph2["nodes"]} and (key, new_key) in edges_after
    assert rows_before <= rows_after                                         # no existing card or run row was dropped
    assert len(calls) > before_calls                                         # the opened card is dispatched again on a new attempt


def test_d1_an_open_card_has_no_time_machine_of_its_own(env, decided, monkeypatch):
    """The open (blocking) card itself is no Time Machine target: the server refuses it (``node_not_done``), so the message
    must not send the person to it — it names the completed card before it."""
    key, _calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    run_id = _run(env)["run_id"]
    _mount_time_machine(env)
    res = env["client"].post(f"/api/v1/runs/{run_id}/nodes/{key}/time-machine", json={}, headers=_h())
    assert res.status_code == 409
    reasons = res.json()["error"]["params"]["reasons"]
    assert any(r.get("code") == "node_not_done" for r in reasons), reasons
    assert _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = ?", (key,))[0]["status"] == "open"


# ── D7: a completed run takes a command with nothing to do and changes nothing ─────────────────────────────────────────

def test_d7_completed_run_takes_a_command_and_changes_nothing(env, decided, monkeypatch):
    key, calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    run_id = _run(env)["run_id"]
    _sql(env["db"], "UPDATE graph_node_runs SET status = 'done' WHERE node_key = ?", (key,))
    _sql(env["db"], "UPDATE graph_runs SET status = 'idle', idle_reason = 'completed' WHERE run_id = ?", (run_id,))
    before, executions = len(calls), len(_sql(env["db"], "SELECT 1 FROM graph_attempt_runs"))
    res = _cmd(env, "step")
    assert res.status_code == 201 and res.json()["run"]["idle_reason"] == "completed"
    assert (_run(env)["status"], _run(env)["idle_reason"]) == ("idle", "completed")
    assert len(calls) == before and len(_sql(env["db"], "SELECT 1 FROM graph_attempt_runs")) == executions


# ── D8: a repeated Stop goes on with a stopping run (idempotent) ───────────────────────────────────────────────────────

def test_d8_a_repeated_stop_finishes_a_stopping_run(env, decided, monkeypatch):
    _key, _calls = _fail_start(env, monkeypatch, RuntimeError("boom"))
    run_id = _run(env)["run_id"]
    _sql(env["db"], "UPDATE graph_runs SET status = 'stopping' WHERE run_id = ?", (run_id,))
    res = env["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h())
    assert res.status_code == 200, res.text
    assert (_run(env)["status"], _run(env)["idle_reason"]) == ("idle", "stopped")
