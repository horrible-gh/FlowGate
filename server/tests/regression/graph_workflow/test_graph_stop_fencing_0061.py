"""0061 T#7 regression cases: late-result fencing, the Stop gate and Stop; revision 2 adds the Q439 idle exceptions,
the run-end hand-off, the owned-end settle, the gate-held run-end approvals and the Q440 approval targets. TR compiles
only; TS/TSR executes them.

Real SQLite ledger (migrations 151/152, the T#4 fixture) with the run events, AI processes, token reclaim,
worktree removal and SSE mocked. No AI process, Git repository or production database is touched.
"""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from test_graph_attempt_runs_0061 import db, ai_run, request  # noqa: F401 — the shared T#4 fixture
from flowgate.graph_run import execution, execution_store as ledger, fencing, settle, stop, workspace
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import graph_source_hooks


@pytest.fixture
def fenced(db, monkeypatch):
    # The run columns a Stop writes (the T#4 fixture keeps only the ledger's).
    for column in ("stopped_at TEXT", "updated_at TEXT", "finished_at TEXT",
                   "last_event_seq INTEGER DEFAULT 0", "revision_no INTEGER DEFAULT 1"):
        db._execute("ALTER TABLE graph_runs ADD COLUMN " + column)
    recorded = []
    monkeypatch.setattr(fencing, "record", lambda row, operation, why, **extra:
                        recorded.append((operation, why, (row or {}).get("attempt_id"))))
    db.recorded = recorded
    return db


@pytest.fixture
def stopping(fenced, monkeypatch):
    def guard(_flow, body):
        with fenced.transaction():
            return body()
    monkeypatch.setattr(stop, "with_flow_guard", guard)
    monkeypatch.setattr(stop, "_context", lambda run, actor: SimpleNamespace(run=dict(run, last_event_seq=0)))
    from modules.flow_gate.graph_workflow.services import run_apply
    events = []
    monkeypatch.setattr(run_apply, "emit_run_status", lambda ctx, frm, to, cause, reason=None:
                        events.append(("run", frm, to, cause, reason)))
    monkeypatch.setattr(run_apply, "emit_node_state", lambda ctx, key, frm, to, cause, doc, *a, **k:
                        events.append(("node", key, frm, to, cause)))
    monkeypatch.setattr(stop, "_kill", Mock())
    monkeypatch.setattr(stop, "_revoke", lambda tokens: fenced.revoked.extend(tokens))
    monkeypatch.setattr(stop, "KILL_WAIT_SEC", 0)
    from modules.flow_gate.graph_workflow.services import run_service
    monkeypatch.setattr(run_service, "run_payload", lambda run_id, with_nodes: run_store.run_by_id(run_id))
    fenced.events, fenced.revoked, fenced.cleaned = events, [], []
    monkeypatch.setattr(workspace, "cleanup", lambda attempt_id: fenced.cleaned.append(
        (attempt_id, run_store.run_by_id("run")["status"])) or True)
    return fenced


def running(db, ai_id="ai"):
    eid = request(db)
    return eid, ledger.acquire(eid, ai_id)


def process_state(monkeypatch, ended):
    monkeypatch.setattr(stop, "_process_ended", lambda row: ended)


@pytest.mark.parametrize("status,allowed", [("running", True), ("paused", True), ("idle", False),
                                            ("stopped", True), ("stopping", False)])
def test_run_status_fence(fenced, status, allowed):
    fenced._execute("UPDATE graph_runs SET status=?", [status])
    assert (fencing.refusal(fenced.attempt, "source_mutation") is None) is allowed
    assert bool(fenced.recorded) is not allowed


def test_engine_opening_takes_idle_but_not_stopping(fenced):
    fenced._execute("UPDATE graph_runs SET status='idle'")
    fencing.check(fenced.attempt, "request_write", statuses=fencing.OPENING)
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        fencing.check(fenced.attempt, "request_write", statuses=fencing.OPENING)


def test_reporting_refuses_idle_and_invalidated_attempt(fenced):
    fenced._execute("UPDATE graph_runs SET status='idle'")
    assert fencing.refusal(fenced.attempt, "finalize", statuses=fencing.REPORTING) == "run_idle"
    fenced._execute("UPDATE graph_runs SET status='running'")
    fenced._execute("UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL")
    assert fencing.refusal(fenced.attempt, "inbox_new") == "attempt_invalidated"


def test_token_refusal_is_409_graph_attempt_inactive(fenced, monkeypatch):
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    monkeypatch.setattr(graph_source_hooks, "binding", lambda token: fenced.attempt)
    with pytest.raises(graph_source_hooks.GraphAttemptInactive) as refused:
        fencing.require_token({"token_id": "tok"}, "inbox_new")
    assert refused.value.status_code == 409 and refused.value.code == "graph_attempt_inactive"
    assert isinstance(refused.value, graph_source_hooks.GraphSourceError)
    assert isinstance(refused.value, ledger.ExecutionLeaseLost)
    assert fenced.recorded == [("inbox_new", "run_stopping", fenced.attempt)]


def test_unmapped_v01_token_is_never_fenced(fenced, monkeypatch):
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    monkeypatch.setattr(graph_source_hooks, "binding", lambda token: None)
    fencing.require_token({"token_id": "v01"}, "inbox_new")
    assert fenced.recorded == []


def test_review_verdict_needs_running_execution_with_current_lease(fenced, monkeypatch):
    eid, token = running(fenced, "reviewer")
    live = {"graph_exec_id": eid, "graph_lease_token": token}
    from modules.flow_gate.services import ai_invoke_service
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: live)
    monkeypatch.setattr(graph_source_hooks, "binding", lambda tok: fenced.attempt)
    fencing.require_token({"token_id": "t", "ai_run_id": "reviewer"}, "inbox_review", report=True)
    live["graph_lease_token"] = "stale"
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        fencing.require_token({"token_id": "t", "ai_run_id": "reviewer"}, "inbox_review", report=True)


@pytest.mark.parametrize("live,why", [(None, "execution_not_live"), ({"status": "running"}, "execution_missing")])
def test_review_verdict_without_a_live_execution_is_refused(fenced, monkeypatch, live, why):
    from modules.flow_gate.services import ai_invoke_service
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: live)
    monkeypatch.setattr(graph_source_hooks, "binding", lambda tok: fenced.attempt)
    with pytest.raises(graph_source_hooks.GraphAttemptInactive) as refused:
        fencing.require_token({"token_id": "t", "ai_run_id": "gone"}, "inbox_review", report=True)
    assert refused.value.details["reason"] == why
    assert fenced.recorded[-1][:2] == ("inbox_review", why)


def test_gate_orders_side_effects_against_the_stopping_commit(fenced):
    order, release, entered = [], threading.Event(), threading.Event()

    def stopper():
        with fencing.exclusive("flow"):
            order.append("stopping_committed")
            release.wait(2)

    def late_writer():
        with fencing.shared("flow"):
            entered.set()
            order.append("late_write")

    with fencing.shared("flow"):
        stop_thread = threading.Thread(target=stopper)
        stop_thread.start()
        stop_thread.join(0.2)
        assert order == []                               # the Stop waits for the side effect in flight
        order.append("in_flight_write")
    late = threading.Thread(target=late_writer)
    for _ in range(100):
        if order[-1] == "stopping_committed":
            break
        threading.Event().wait(0.02)
    late.start()
    late.join(0.2)
    assert not entered.is_set()                          # nothing new starts while the Stop holds the gate
    release.set()
    stop_thread.join(2)
    late.join(2)
    assert order == ["in_flight_write", "stopping_committed", "late_write"]


def test_hold_judges_under_the_gate_and_records_after_release(fenced, monkeypatch):
    from contextlib import contextmanager
    released = []

    @contextmanager
    def git_lock():
        yield
        released.append("git")

    monkeypatch.setattr(fencing, "record", lambda row, operation, why, **extra:
                        fenced.recorded.append((operation, why, list(released))))
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        with fencing.hold(fenced.attempt, "source_mutation", before=git_lock):
            pytest.fail("a stopping run's write must not run")
    assert fenced.recorded == [("source_mutation", "run_stopping", ["git"])]
    assert fencing._gate("flow").readers == 0


def test_kept_hold_lasts_for_the_write_session(fenced, monkeypatch):
    monkeypatch.setattr(graph_source_hooks, "binding", lambda tok: fenced.attempt)
    monkeypatch.setattr(graph_source_hooks, "_fence_handler", fencing.require_token)
    with graph_source_hooks.write_session():
        graph_source_hooks.require_writable({"token_id": "t"}, "inbox_new")
        assert fencing._gate("flow").readers == 1
    assert fencing._gate("flow").readers == 0


def test_finalize_report_fence(fenced):
    eid, token = running(fenced)
    run = {"graph_exec_id": eid, "graph_lease_token": token, "run_id": "ai"}
    assert fencing.report_refused(run) is None
    assert fencing.report_refused({**run, "graph_lease_token": "old"}) == "lease_token_stale"
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    assert fencing.report_refused(run) == "run_stopping"
    fenced._execute("UPDATE graph_attempt_runs SET status='cancelled'")
    assert fencing.report_refused(run) == "execution_cancelled"


def test_stopping_commit_cancels_requests_and_refuses_acquire(stopping, monkeypatch):
    eid, token = running(stopping)
    other = run_store.open_attempt("run", "second", 1)
    queued = ledger.request(other, "run", "write", {"type": "write", "job": {}})
    process_state(monkeypatch, False)
    answer = stop.stop_run({"group_id": "flow"}, "run", "user", "u1")
    assert answer["run"]["status"] == "stopping" and answer["unconfirmed_exec_ids"] == [eid]
    assert ledger.get(queued)["status"] == "cancelled"
    assert ledger.get(eid)["status"] == "running" and ledger.get(eid)["cancel_requested_at"]
    with pytest.raises(ledger.LeaseLost):
        ledger.renew(eid, token)
    retry = run_store._one("SELECT 1 AS x FROM graph_attempt_runs WHERE exec_id=?", [queued])
    assert retry and not ledger.eligible(ledger.get(queued))
    # Unconfirmed: nothing of (c)~(e) happened.
    assert run_store.active_attempt_id("run", "node") == stopping.attempt
    assert stopping.cleaned == [] and stopping.revoked == []
    assert ("run", "stopping", "stopping", stop.CAUSE_PENDING, stop.PENDING_REASON) in stopping.events


def test_stop_completes_only_after_processes_end(stopping, monkeypatch):
    eid, _token = running(stopping)
    stopping._execute("INSERT INTO graph_run_token_map(token_id,run_id,node_key,stage,mapped_at,attempt_id) "
                      "VALUES('tok','run','node',NULL,'0',?)", [stopping.attempt])
    stopping._execute("INSERT INTO graph_node_runs(run_id,node_key,status) VALUES('run','done_node','done')")
    process_state(monkeypatch, False)
    stop.stop_run({"group_id": "flow"}, "run", None, "u1")
    process_state(monkeypatch, True)
    assert stop.advance("flow", "run") == ([], [])
    assert ledger.get(eid)["status"] == "cancelled"
    attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id=?", [stopping.attempt])
    assert attempt["status"] == "invalidated" and attempt["invalidated_reason"] == "user_stop"
    assert stopping.revoked == ["tok"]
    nodes = {r["node_key"]: r for r in run_store.node_runs("run")}
    assert nodes["node"]["status"] == "pending" and nodes["node"]["result_doc_id"] is None
    assert nodes["done_node"]["status"] == "done"
    # The worktree goes while the run is still stopping; only then idle (stopped).
    assert stopping.cleaned == [(stopping.attempt, "stopping")]
    run = run_store.run_by_id("run")
    assert run["status"] == "idle" and run["idle_reason"] == "stopped"
    assert stopping.events[-1][:4] == ("run", "stopping", "idle", stop.CAUSE_STOP)
    assert run_store.token_ids("run", "node") == []


def test_failed_worktree_removal_keeps_the_run_stopping_until_a_retry(stopping, monkeypatch):
    process_state(monkeypatch, True)
    monkeypatch.setattr(workspace, "cleanup", lambda attempt_id: False)
    answer = stop.stop_run({"group_id": "flow"}, "run", None, "u1")
    assert answer["run"]["status"] == "stopping"
    assert answer["cleanup_pending_attempt_ids"] == [stopping.attempt]
    assert ("run", "stopping", "stopping", stop.CAUSE_PENDING, stop.CLEANUP_PENDING_REASON) in stopping.events
    assert run_store._one("SELECT status FROM graph_node_attempts WHERE attempt_id=?",
                          [stopping.attempt])["status"] == "invalidated"
    monkeypatch.setattr(workspace, "cleanup", lambda attempt_id: True)
    stop.recover()                                       # the start-up recovery retries the removal
    run = run_store.run_by_id("run")
    assert run["status"] == "idle" and run["idle_reason"] == "stopped"


def test_repeated_stop_and_non_running_refusal(stopping, monkeypatch):
    process_state(monkeypatch, True)
    stop.stop_run({"group_id": "flow"}, "run", None, "u1")
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    with pytest.raises(GraphApiError) as refused:
        stop.stop_run({"group_id": "flow"}, "run", None, "u1")
    assert refused.value.status == 409 and refused.value.code == "run_not_running"


def test_late_end_after_stop_settles_nothing(stopping, monkeypatch):
    eid, token = running(stopping)
    process_state(monkeypatch, True)
    stop.stop_run({"group_id": "flow"}, "run", None, "u1")
    monkeypatch.setattr("modules.flow_gate.services.ai_invoke.terminal.provider_stopped", lambda run: True)
    settle_mock = Mock()
    monkeypatch.setattr(execution, "settle", settle_mock)
    run = {**ai_run(eid), "graph_lease_token": token}
    run["cancel_event"].set()
    execution.finish(run)
    assert run["graph_execution_finished"] and run["graph_report_late"] and run["graph_settle_skipped"]
    settle_mock.assert_not_called()


def test_an_old_attempts_callback_moves_no_node_opened_again(fenced):
    old = fenced.attempt
    fenced._execute("UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL")
    new = run_store.open_attempt("run", "node", 2)
    assert fencing.node_refused("run", "node", "result_stage", old)
    assert fenced.recorded[-1][:3] == ("result_stage", "attempt_invalidated", old)
    assert not fencing.node_refused("run", "node", "result_stage", new)


def test_invalidated_attempt_tokens_are_told_apart(fenced):
    fenced._execute("INSERT INTO graph_run_token_map(token_id,run_id,node_key,stage,mapped_at,attempt_id) "
                    "VALUES('old','run','node',NULL,'0',?)", [fenced.attempt])
    fenced._execute("UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL")
    assert run_store.invalidated_token_ids("run", "node") == ["old"]
    assert run_store.token_ids("run", "node") == []


def test_workspace_record_is_a_run_status_event(fenced, monkeypatch):
    written = []
    monkeypatch.setattr(run_store, "append_event", lambda run_id, seq, event_type, payload, **kw:
                        written.append((event_type, kw["node_key"], kw["cause"], payload["mode"])))
    row = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id=?", [fenced.attempt])
    workspace._event(row, "document_only")
    assert written == [("graph_run_status_changed", None, workspace.CAUSE, "document_only")]


def test_last_process_end_finishes_a_waiting_stop(stopping, monkeypatch):
    eid, token = running(stopping)
    process_state(monkeypatch, False)
    stop.stop_run({"group_id": "flow"}, "run", None, "u1")
    assert run_store.run_by_id("run")["status"] == "stopping"
    monkeypatch.setattr("modules.flow_gate.services.ai_invoke.terminal.provider_stopped", lambda run: True)
    monkeypatch.setattr(stop, "_process_ended", lambda row: row["exec_id"] != eid or
                        ledger.get(eid)["status"] != "running")
    run = {**ai_run(eid), "graph_lease_token": token}
    run["cancel_event"].set()
    execution.finish(run)
    assert ledger.get(eid)["status"] == "cancelled"
    assert run_store.run_by_id("run")["status"] == "idle"


def test_stopped_idle_reason_is_kept_until_the_next_command(fenced):
    fenced._execute("UPDATE graph_runs SET status='idle', idle_reason='stopped'")
    ctx = SimpleNamespace(run=run_store.run_by_id("run"), run_id="run", flow_id="flow")
    assert settle.settle_plan(ctx) is None
    assert "stopped" in settle.IDLE_REASONS


def test_stopping_run_settles_nothing(fenced):
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    ctx = SimpleNamespace(run=run_store.run_by_id("run"), run_id="run", flow_id="flow")
    assert settle.settle_plan(ctx) is None


def test_result_binder_contact_is_refused_while_stopping(fenced):
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    run = run_store.run_by_id("run")
    assert execution.result_refused(run, "review_decided", {"doc_id": "doc"})
    fenced._execute("UPDATE graph_runs SET status='running'")
    fenced._execute("INSERT INTO graph_run_token_map(token_id,run_id,node_key,stage,mapped_at,attempt_id) "
                    "VALUES('old','run','node',NULL,'0',?)", [fenced.attempt])
    fenced._execute("UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL")
    assert execution.result_refused(run_store.run_by_id("run"), "inbox_registered", {"token_id": "old"})


def test_source_mutation_refused_before_the_git_lock(fenced, monkeypatch):
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    lock = Mock()
    monkeypatch.setattr(workspace.locks, "acquire", lock)
    monkeypatch.setattr(workspace, "_project", lambda row, *a: "proj")
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        with workspace.mutation(fenced.attempt, project_id="proj", group_id="flow"):
            pass
    lock.assert_not_called()


def test_review_transition_refused_while_stopping(fenced):
    eid, token = running(fenced)
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    run = {**ai_run(eid), "graph_lease_token": token}
    with pytest.raises(ledger.ExecutionLeaseLost):
        execution.transition(run, {"current_stage": "rework", "last_hop_outcome": "ok"})
    assert ledger.get(eid)["status"] == "running"


def test_hop_request_refused_while_stopping(fenced, monkeypatch):
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    monkeypatch.setattr(execution, "_attempt_for_document", lambda flow, doc: {
        "attempt_id": fenced.attempt, "run_id": "run", "status": "active"})
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        execution.request_hop("flow", {}, {"slot": {"doc_id": "doc"}}, "review")
    assert run_store._one("SELECT COUNT(*) AS n FROM graph_attempt_runs", [])["n"] == 0


def test_pause_route_keeps_t2_behaviour_and_stop_route_is_stop():
    from modules.flow_gate.graph_workflow.api import run_routes
    paths = {route.path: route.endpoint.__name__ for route in run_routes.router.routes}
    assert paths["/api/v1/runs/{run_id}/pause"] == "post_run_pause"
    assert paths["/api/v1/runs/{run_id}/stop"] == "post_run_stop"
    from modules.flow_gate.graph_workflow.services import run_service
    assert run_service.pause_run.__doc__ and "Pause" in run_service.pause_run.__doc__
    assert threading.Event  # the run fixtures' cancel events


# ── 0017-TR revision 2: Q439 idle exceptions, the run-end hand-off, owned ends, gate-held approvals, Q440 ──────────

def _guard_in(db, monkeypatch, *modules):
    def guard(_flow, body):
        with db.transaction():
            return body()
    for module in modules:
        monkeypatch.setattr(module, "with_flow_guard", guard)


def test_idle_takes_a_persons_own_token_registration_only(fenced, monkeypatch):
    fenced._execute("UPDATE graph_runs SET status='idle', idle_reason='error'")
    monkeypatch.setattr(graph_source_hooks, "binding", lambda token: fenced.attempt)
    fencing.require_token({"token_id": "dispatch"}, "inbox_new")             # L0009 E-23: the node's own token
    fencing.require_token({"token_id": "dispatch", "ai_run_id": ""}, "inbox_edit")
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        fencing.require_token({"token_id": "ai", "ai_run_id": "air_1"}, "inbox_new")
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        fencing.require_token({"token_id": "dispatch"}, "source_write")      # no other idle write
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        fencing.require_token({"token_id": "dispatch"}, "inbox_new")
    assert fencing.human_registration({"token_id": "d"}, "result_inbox_registered")
    assert not fencing.human_registration({"token_id": "d", "ai_run_id": "air"}, "result_inbox_registered")


def test_explicit_review_hop_on_an_idle_run_and_automatic_refused(fenced, monkeypatch):
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.services import graph_execution_hooks
    fenced._execute("UPDATE graph_runs SET status='idle', idle_reason='waiting_approval'")
    _guard_in(fenced, monkeypatch, fencing)
    monkeypatch.setattr(execution, "_attempt_for_document", lambda flow, doc: {
        "attempt_id": fenced.attempt, "run_id": "run", "status": "active"})
    monkeypatch.setattr(run_apply, "load_context", lambda run, project, actor: SimpleNamespace(run=run))
    monkeypatch.setattr(execution.db_groups, "get_by_id", lambda flow: {"project_id": "proj"})
    promoted = Mock()
    monkeypatch.setattr(run_apply, "settle_run", promoted)
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        execution.request_hop("flow", {}, {"slot": {"doc_id": "doc"}}, "review")       # an automatic hop
    with graph_execution_hooks.explicit_request():
        assert execution.request_hop("flow", {}, {"slot": {"doc_id": "doc"}}, "review")
    promoted.assert_called_once()                                                      # idle -> running
    assert not graph_execution_hooks.explicit()


def test_refusal_inside_a_guard_is_recorded_after_its_rollback(fenced, monkeypatch):
    fenced._execute("UPDATE graph_runs SET status='stopping'")
    _guard_in(fenced, monkeypatch, fencing)

    def body():
        fencing.check(fenced.attempt, "hop_review")
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        fencing.guarded("flow", body)
    assert fenced.recorded == [("hop_review", "run_stopping", fenced.attempt)]


def test_run_end_handoff_keeps_a_running_run_working_only(fenced):
    ctx = SimpleNamespace(run=run_store.run_by_id("run"), run_id="run", flow_id="flow")
    assert not settle.working(ctx, [])
    ledger.begin_handoff("exe_x", "run")
    try:
        assert settle.working(ctx, [])
        idle = SimpleNamespace(run={**ctx.run, "status": "idle"}, run_id="run", flow_id="flow")
        assert not settle.working(idle, [])                                       # never wakes an idle run
    finally:
        assert ledger.end_handoff("exe_x") == "run"
    assert not settle.working(ctx, [])


def test_owned_end_handed_on_settles_after_the_stage(fenced, monkeypatch):
    eid, token = running(fenced)
    monkeypatch.setattr("modules.flow_gate.services.ai_invoke.terminal.provider_stopped", lambda run: True)
    settle_mock = Mock()
    monkeypatch.setattr(execution, "settle", settle_mock)
    run = {**ai_run(eid), "graph_lease_token": token, "graph_run_end_pending": True}
    execution.finish(run)
    assert run["graph_end_owned"] and ledger.get(eid)["status"] == "ended"
    settle_mock.assert_not_called()
    assert ledger.handing_off("run")
    execution.handoff_end(run)
    assert not ledger.handing_off("run")


def test_lost_launcher_end_is_not_owned(fenced, monkeypatch):
    eid = request(fenced)
    fenced._execute("UPDATE graph_attempt_runs SET status='cancelled' WHERE exec_id=?", [eid])
    settle_mock = Mock()
    monkeypatch.setattr(execution, "settle", settle_mock)
    run = {**ai_run(eid), "graph_lease_lost": True}
    execution.finish(run)
    assert not run.get("graph_execution_finished") and not run.get("graph_end_owned")
    settle_mock.assert_not_called()


def test_node_hold_judges_the_executions_attempt_under_the_gate(fenced):
    with fencing.node_hold("run", "node", fenced.attempt, "instruction_approval") as owned:
        assert owned and fencing._gate("flow").readers == 1
    old = fenced.attempt
    fenced._execute("UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL")
    run_store.open_attempt("run", "node", 2)
    with fencing.node_hold("run", "node", old, "instruction_approval") as owned:
        assert not owned and fencing._gate("flow").readers == 0                  # recorded after the release
    assert fenced.recorded[-1][:3] == ("instruction_approval", "attempt_invalidated", old)


def test_runtime_invalidation_tolerates_its_own_shared_hold():
    with fencing.shared("flow_own"):
        with fencing.exclusive("flow_own", own_ok=True):
            pass
        with pytest.raises(RuntimeError):
            with fencing.exclusive("flow_own"):
                pass


def test_an_ended_attempts_line_document_is_no_approval_target():
    from flowgate.card_approval import approval
    stale = {"slot": "instruction", "doc_id": "old_t", "doc_review_status": "pending_review", "stale": True}
    assert not approval.is_target(stale)
    assert approval.is_target({**stale, "stale": False})
