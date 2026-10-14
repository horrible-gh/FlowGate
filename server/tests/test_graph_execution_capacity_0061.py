"""0061 T#6 regression cases. TR compiles only; TS/TSR executes behavior.

Real SQLite execution ledger and migration 153, mocked process/workspace/provider
and Flow guard. No AI process or production database is touched by these tests.
"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
from fastapi import HTTPException

from test_graph_attempt_runs_0061 import db, MIGRATIONS
from flowgate.graph_run import scheduling, execution, execution_store as ledger, settle
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import graph_execution_hooks
from modules.flow_gate.services.ai_invoke import provider_cli
from modules.flow_gate.graph_workflow.domain.frontier import RunState, NodeRun


@pytest.fixture
def capacity(db, monkeypatch):
    db.db.execute("CREATE TABLE groups(group_id TEXT PRIMARY KEY)")
    db.db.execute("INSERT INTO groups VALUES('flow')")
    db.db.executescript((MIGRATIONS / "sqlite" / "153_graph_execution_limit.sql").read_text())
    monkeypatch.setattr(scheduling, "get_store", lambda: db)
    monkeypatch.setattr(scheduling.groups, "get_by_id", lambda key:
                        db._fetch_one("SELECT * FROM groups WHERE group_id=?", [key]))
    monkeypatch.setattr(scheduling.group_ai_leases, "get_active", lambda _: None)
    monkeypatch.setattr(scheduling, "with_flow_guard", execution.with_flow_guard)
    monkeypatch.setattr(execution.workspace, "verify_execution", lambda _: None)
    monkeypatch.setattr(execution, "settle", Mock())
    return db


def requests(db, number):
    result = []
    for i in range(number):
        attempt = run_store.open_attempt("run", f"n{i}", 1)
        eid = ledger.request(attempt, "run", ("write", "review", "rework")[i % 3], {})
        db._execute("UPDATE graph_attempt_runs SET created_at=? WHERE exec_id=?", [f"{i:04}", eid])
        result.append(eid)
    return result


def test_four_running_fifth_requested_then_refilled(capacity):
    ids = requests(capacity, 5)
    tokens = [scheduling.acquire(eid, f"ai{i}") for i, eid in enumerate(ids[:4])]
    assert all(tokens)
    assert scheduling.acquire(ids[4], "fifth") is None
    assert ledger.get(ids[4])["status"] == "requested"
    assert scheduling.counts("run") == {"running_execution_count": 4, "requested_execution_count": 1}
    ledger.end(ids[0], tokens[0])
    assert scheduling.acquire(ids[4], "fifth")
    assert scheduling.counts("run")["running_execution_count"] == 4


def test_parallel_acquire_never_exceeds_configured_limit(capacity):
    capacity._execute("UPDATE groups SET graph_execution_limit=2")
    ids = requests(capacity, 8)
    with ThreadPoolExecutor(max_workers=8) as workers:
        tokens = list(workers.map(lambda eid: scheduling.acquire(eid, eid), ids))
    assert sum(bool(t) for t in tokens) == 2
    assert scheduling.counts("run")["running_execution_count"] == 2
    assert scheduling.counts("run")["requested_execution_count"] == 6


def test_oldest_request_precedes_newer_request(capacity):
    capacity._execute("UPDATE groups SET graph_execution_limit=1")
    old, new = requests(capacity, 2)
    assert scheduling.acquire(new, "new") is None
    owner = scheduling.acquire(old, "old")
    ledger.end(old, owner)
    assert scheduling.acquire(new, "new")


def test_approval_wait_and_ended_attempt_do_not_occupy_slot(capacity):
    eid = requests(capacity, 1)[0]
    owner = scheduling.acquire(eid, "ai")
    ledger.end(eid, owner)
    assert run_store.active_attempt_id("run", "n0")
    assert scheduling.available("run", "flow") == 4
    assert scheduling.available("run", "flow", reserve=True) == 4


@pytest.mark.parametrize("status", ["idle", "stopping"])
def test_idle_stopping_cannot_acquire_or_queue(capacity, status):
    eid = requests(capacity, 1)[0]
    capacity._execute("UPDATE graph_runs SET status=?", [status])
    with pytest.raises(ledger.LeaseLost):
        scheduling.acquire(eid, "ai")
    ctx = SimpleNamespace(run={"status": status}, run_id="run", flow_id="flow", jobs=[])
    scheduling.queue_requests(ctx)
    assert ctx.jobs == [] and not scheduling.can_open(ctx)


def test_paused_starts_existing_requests_without_new_nodes(capacity):
    eid = requests(capacity, 1)[0]
    capacity._execute("UPDATE graph_runs SET status='paused'")
    ctx = SimpleNamespace(run={"status": "paused"}, run_id="run", flow_id="flow", jobs=[])
    scheduling.queue_requests(ctx)
    assert len(ctx.jobs) == 1
    assert not scheduling.can_open(ctx)
    assert scheduling.acquire(eid, "ai")


def test_v01_conflicts_both_directions_and_no_graph_group_lease(capacity, monkeypatch):
    eid = requests(capacity, 1)[0]
    legacy = Mock(return_value={"run_id": "v01"})
    with pytest.raises(HTTPException) as refused:
        scheduling.legacy_acquire(legacy, group_id="flow")
    assert refused.value.status_code == 409 and refused.value.detail["code"] == "run_in_progress"
    legacy.assert_not_called()
    monkeypatch.setattr(scheduling.group_ai_leases, "get_active", lambda _: {"run_id": "v01"})
    with pytest.raises(HTTPException):
        scheduling.admit(eid, "ai", "flow")
    assert scheduling.acquire(eid, "ai") is None
    monkeypatch.setattr(scheduling.group_ai_leases, "get_active", lambda _: None)
    forbidden = Mock(side_effect=AssertionError("Graph must not acquire group lease"))
    monkeypatch.setattr(scheduling.group_ai_leases, "acquire", forbidden)
    attempt = scheduling.admit(eid, "ai", "flow")
    assert attempt == ledger.get(eid)["attempt_id"]
    assert scheduling.acquire(eid, "ai")
    forbidden.assert_not_called()


def test_failed_admission_releases_only_its_unacquired_claim(capacity):
    eid = requests(capacity, 1)[0]
    def fail():
        scheduling.admit(eid, "ai", "flow")
        raise RuntimeError("token issue failed")
    with pytest.raises(RuntimeError):
        scheduling.admission(fail)
    assert ledger.get(eid)["ai_run_id"] is None
    assert ledger.get(eid)["status"] == "requested"
    scheduling.admit(eid, "winner", "flow")
    with pytest.raises(HTTPException):
        scheduling.admission(lambda: scheduling.admit(eid, "loser", "flow"))
    assert ledger.get(eid)["ai_run_id"] == "winner"


def test_node_order_and_step_refill_intersection(capacity, monkeypatch):
    # z precedes a: lexical sorting alone would incorrectly put a first.
    graph = SimpleNamespace(node_keys=lambda: ["z", "a", "b", "next"],
                            live_edges=lambda: [SimpleNamespace(source="z", target="a"),
                                                SimpleNamespace(source="a", target="next")])
    assert scheduling.ordered(graph, ["a", "b", "z"]) == ["b", "z", "a"]
    from modules.flow_gate.graph_workflow.domain import frontier
    from modules.flow_gate.graph_workflow.services import run_apply
    state = RunState("running", {k: NodeRun(k, "pending") for k in ("a", "b", "next")},
                     mode="step", step_targets=frozenset({"a", "b"}))
    monkeypatch.setattr(frontier, "run_candidates", lambda *_: ["a", "b", "next"])
    monkeypatch.setattr(run_apply, "read_run_state", lambda _: state)
    ctx = SimpleNamespace(run={"status": "running"}, revision=graph)
    assert scheduling.opening(ctx, ["a", "b", "next"]) == ["b", "a"]
    # Recompute after a target finishes: its new descendant remains outside the command.
    monkeypatch.setattr(frontier, "run_candidates", lambda *_: ["b", "next"])
    assert scheduling.opening(ctx, ["b", "next"]) == ["b"]


def test_requested_reserves_new_node_room_and_wire_is_per_attempt(capacity):
    ids = requests(capacity, 4)
    assert scheduling.available("run", "flow") == 4
    assert scheduling.available("run", "flow", reserve=True) == 0
    attempt = ledger.get(ids[0])["attempt_id"]
    assert scheduling.wire("run", "flow", attempt) == {
        "running_execution_count": 0, "requested_execution_count": 1, "execution_limit": 4}


def test_local_boundary_acquires_before_process_and_preserves_probe(monkeypatch):
    log = []
    run = {"run_id": "ai", "graph_exec_id": "exec", "cancel_event": threading.Event()}
    process = Mock()
    process.start.side_effect = lambda *_: log.append("spawn") or True
    process.communicate.return_value = (b"out", b"", False, None)
    monkeypatch.setattr(provider_cli, "_CliProcess", Mock(return_value=process))
    monkeypatch.setattr(provider_cli, "_cli_popen_kwargs", lambda *_: {})
    monkeypatch.setattr(provider_cli, "_audit_cli_launch", lambda *_: None)
    monkeypatch.setattr(provider_cli, "_absolute_remaining_sec", lambda _: 10)
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *_: log.append("acquire") or True)
    handle, result = provider_cli._execute_local("exec", "cwd", "prompt", {}, command="cli", run=run,
                                                 decision={}, started=lambda _: None, stopped=lambda: None)
    assert log == ["acquire", "spawn"]
    assert handle is process and result == (b"out", b"", False, None)
    process.close.assert_called_once()
    assert run["proc"] is None
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *_: False)
    provider_cli._CliProcess.reset_mock()
    assert provider_cli._execute_local("exec", "cwd", "prompt", {}, command="cli", run=run,
                                       decision={}, started=lambda _: None, stopped=lambda: None) == (None, None)
    provider_cli._CliProcess.assert_not_called()


def test_non_graph_admission_wrapper_is_transparent():
    start = Mock(return_value="legacy")
    wrapped = graph_execution_hooks.admission(start)
    assert wrapped(project_id="project") == "legacy"
    start.assert_called_once_with(project_id="project")


def test_opening_checks_capacity_after_each_dispatch(capacity, monkeypatch):
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.domain.frontier import Decision
    capacity._execute("UPDATE groups SET graph_execution_limit=2")
    opened = []
    monkeypatch.setattr(scheduling, "opening", lambda _ctx, keys: list(keys))
    # The registered handler captures the real function; patch that one port here.
    monkeypatch.setitem(execution._ACTIONS, "opening", scheduling.opening)
    def dispatch(ctx, key):
        ledger.request(run_store.active_attempt_id(ctx.run_id, key), ctx.run_id, "write", {})
        opened.append(key)
        return {"token_issued": True}
    monkeypatch.setattr(run_apply.node_dispatch, "dispatch", dispatch)
    monkeypatch.setattr(run_store, "open_node_run", lambda *_: 1)
    monkeypatch.setattr(run_apply, "emit_node_state", lambda *_: None)
    ctx = SimpleNamespace(run={"status": "running", "revision_no": 1}, run_id="run", flow_id="flow")
    decision = Decision(open=["one", "two", "three", "four"])
    run_apply.apply_decision(ctx, decision, settle=False)
    assert opened == ["one", "two"]
    assert decision.open == ["one", "two", "three", "four"]  # saved step targets are not truncated
    oldest = scheduling.requests("run")[0]
    token = scheduling.acquire(oldest["exec_id"], "ai")
    ledger.end(oldest["exec_id"], token)
    run_apply.apply_decision(ctx, Decision(open=["three", "four"]), settle=False)
    assert opened == ["one", "two", "three"]


def test_refill_queues_existing_requests_before_new_node_jobs(capacity, monkeypatch):
    eid = requests(capacity, 1)[0]
    order = []
    monkeypatch.setattr(execution, "replay", lambda row: order.append(row["exec_id"]))
    monkeypatch.setattr(settle, "settle_plan", lambda _: ("refill", None))
    monkeypatch.setattr(settle.engine, "read_run_state", lambda _: None)
    monkeypatch.setattr(settle, "_candidates", lambda *_: ["new"])
    monkeypatch.setattr(settle.engine, "apply_decision",
                        lambda ctx, _d, **_kw: ctx.jobs.append(lambda: order.append("new")))
    ctx = SimpleNamespace(run={"status": "running"}, run_id="run", flow_id="flow", jobs=[])
    settle.settle_run(ctx)
    for job in ctx.jobs:
        job()
    assert order == [eid, "new"]
