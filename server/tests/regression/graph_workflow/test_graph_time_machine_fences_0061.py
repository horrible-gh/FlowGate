"""0061 T#13 rework (rej_01M4W2J62CDJRF6G): while a Graph Time Machine operation of the Flow is unfinished no execution
is requested, admitted or replayed — not a person's explicit review (which would take the idle run back to running),
not the write of a node a person's approval opens, not a durable request at its admission or at the server start's
replay. TR Self-check only; formal PASS/gate belongs to TS/TSR.

The execution ledger is the real SQLite fixture of T#4 / T#8 (migrations 151/152/086d/153a, ``result_db``); the
unfinished operation (``operation.busy``), the fence's run event and the run settlement are stood in.
"""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from test_graph_attempt_runs_0061 import db  # noqa: F401 — shared SQLite execution fixture
from tests.regression.graph_attempt.test_graph_attempt_results_0061 import result_db  # noqa: F401 — T#8 result fixture (migration 153a)
from flowgate.graph_run import execution, execution_store as ledger, fencing, scheduling
from flowgate.graph_time_machine import operation
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import graph_execution_hooks, graph_source_hooks


@pytest.fixture
def fenced(result_db, monkeypatch):
    db = result_db
    unfinished = {"busy": True}
    monkeypatch.setattr(operation, "busy", lambda flow_id: unfinished["busy"])

    def guard(_flow, body):
        with db.transaction():
            return body()
    monkeypatch.setattr(fencing, "with_flow_guard", guard)
    monkeypatch.setattr(scheduling, "with_flow_guard", guard)
    recorded = Mock()
    monkeypatch.setattr(fencing, "record", recorded)
    monkeypatch.setattr(execution.db_groups, "get_by_id", lambda flow: {"project_id": "p"})
    settle = Mock()
    monkeypatch.setattr(run_apply, "load_context", Mock(return_value="ctx"))
    monkeypatch.setattr(run_apply, "settle_run", settle)
    db._execute("UPDATE graph_runs SET status = 'idle'")
    return SimpleNamespace(db=db, unfinished=unfinished, recorded=recorded, settle=settle)


def executions(db):
    return db._fetch_all("SELECT exec_id, status FROM graph_attempt_runs ORDER BY exec_id")


def refused_why(fenced):
    return fenced.recorded.call_args.args[2]


def test_an_explicit_review_does_not_wake_the_idle_run_under_an_unfinished_operation(fenced):
    with graph_execution_hooks.explicit_request():
        with pytest.raises(graph_source_hooks.GraphAttemptInactive):
            execution._request_document("flow", "doc", "review", {"type": "loop"})
    assert executions(fenced.db) == [] and not fenced.settle.called       # nothing requested, the run not settled
    assert run_store.run_by_id("run")["status"] == "idle"
    assert refused_why(fenced) == "time_machine_in_progress"
    fenced.unfinished["busy"] = False                                    # the operation finished: taken as before
    with graph_execution_hooks.explicit_request():
        exec_id = execution._request_document("flow", "doc", "review", {"type": "loop"})
    assert executions(fenced.db) == [{"exec_id": exec_id, "status": "requested"}] and fenced.settle.called


def test_an_opened_node_requests_no_execution_under_an_unfinished_operation(fenced):
    job = {"run_id": "run", "node_key": "node", "flow_id": "flow", "token": {"token_id": "t"}}
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        execution.request_write(dict(job))
    assert executions(fenced.db) == [] and not fenced.settle.called
    assert refused_why(fenced) == "time_machine_in_progress"
    fenced.unfinished["busy"] = False
    assert execution.request_write(dict(job)) and fenced.settle.called


def test_admission_keeps_the_request_under_an_unfinished_operation(fenced):
    fenced.db._execute("UPDATE graph_runs SET status = 'running'")
    exec_id = ledger.request(fenced.db.attempt, "run", "write", {"type": "write", "job": {}})
    with pytest.raises(HTTPException) as raised:
        scheduling.admit(exec_id, "ai-1", "flow")
    assert raised.value.detail["code"] == "run_in_progress"
    assert execution.start_failed(exec_id, raised.value) is True           # kept, not a node failure
    row = ledger.get(exec_id)
    assert row["status"] == "requested" and not row.get("ai_run_id")


def test_the_server_start_replays_nothing_of_an_unfinished_operation(fenced, monkeypatch):
    fenced.db._execute("UPDATE graph_runs SET status = 'running'")
    exec_id = ledger.request(fenced.db.attempt, "run", "write", {"type": "write", "job": {}})
    monkeypatch.setattr(execution.result_merge, "recover", Mock())
    monkeypatch.setattr(execution.stop, "recover", Mock())
    monkeypatch.setattr(execution.workspace, "cleanup_finished", Mock())
    monkeypatch.setattr(execution.fencing, "exclusive", lambda flow, **kwargs: nullcontext())
    replay = Mock()
    monkeypatch.setattr(execution, "replay", replay)
    execution.recover()
    assert not replay.called and ledger.get(exec_id)["status"] == "requested"
    fenced.unfinished["busy"] = False
    execution.recover()
    assert replay.call_count == 1 and replay.call_args.args[0]["exec_id"] == exec_id
