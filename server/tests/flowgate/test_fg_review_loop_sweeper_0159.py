"""0159 T#2: runtime orphan recovery, migrated SQLite and real card/Graph paths.

No sleeps, server restart or external AI. Clock advances through explicit timestamps.
"""
import json
import time
from datetime import timedelta
from unittest.mock import Mock

import pytest

from test_fg_ai_roles_l2_assignment import (
    B, FA, X, _sql, command, db_path, decided, env, one, starts, wired,
)
from test_fg_card_intervention_assigned_0119 import (
    _loop_row, _read, _reviewed_ai_result, _wire, fork_join,
)
from test_fg_command_review_0125 import completions, _review_row
from test_fg_command_rerequest_0124 import _first_hops
from test_fg_execution_persistence_0156 import scene
from test_fg_card_settings import legacy_cards
from test_fg_restart_orphan_sweep_0159 import active_loop, end_record, OLD, BEFORE, FUTURE, RID
from flowgate.card_settings import legacy_adapter, review_stage
from flowgate.graph_run import execution, execution_store as ledger
from modules.flow_gate.db import ai_invoke_document_review_loops as loops
from modules.flow_gate.db import ai_invoke_runs as runs, ai_run_identity, group_ai_leases, connection
from modules.flow_gate.graph_workflow.services import node_dispatch
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import ai_invoke_service as service
from modules.flow_gate.services.ai_invoke import admission, remote_supervisor as remote, review, chain

NOW = admission._utc_instant(BEFORE)


@pytest.fixture
def sweep(env, monkeypatch):
    # Keep a process registry alive throughout recovery; never invoke startup recovery.
    monkeypatch.setattr(service, "_runs", {})
    monkeypatch.setattr(admission, "_review_loop_sweep_candidates", {})
    monkeypatch.setattr(remote, "_unowned_request_candidates", {})
    monkeypatch.setattr(remote, "_unowned_sweep_ticks", 0)
    monkeypatch.setattr(remote, "held_run_ids", lambda: set())
    monkeypatch.setattr(remote, "remote_alive", lambda _: False)
    monkeypatch.setattr(service, "startup_recover_leases", Mock(side_effect=AssertionError("restart forbidden")))
    monkeypatch.setattr(review_stage, "_handoffs", {})
    monkeypatch.setattr(ledger, "_handoffs", {})
    return env


def aged_loop(env, *, stage="rework", age=3600, deadline=FUTURE, run_id=RID):
    active_loop(env, run_id=run_id, stage=stage)
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET updated_at=?,deadline_at=? WHERE run_id=?",
         ((NOW - timedelta(seconds=age)).isoformat(), deadline, run_id))
    return loops.get(run_id)


def twice():
    return [service.sweep_unowned_review_loops(now=NOW) for _ in range(2)]


@pytest.mark.parametrize("stage,deadline", [("rework", False), ("review", False), ("review", True)])
def test_no_restart_unlocks_card_and_explicit_step_resumes_rework(
        sweep, decided, starts, fork_join, completions, monkeypatch, stage, deadline):
    env = sweep
    key, doc, assignment = _reviewed_ai_result(env, monkeypatch, starts)
    _review_row(doc, 0, "issues", RID)
    from modules.flow_gate.db import documents
    documents.update(doc, {"doc_review_status": "rejected", "rejection_reason": "fix the findings"})
    active_loop(env, stage=stage, doc=doc)
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET deadline_at=?,updated_at=? WHERE run_id=?",
         ((NOW - timedelta(seconds=1)).isoformat() if deadline else FUTURE,
          (NOW - timedelta(seconds=1000 if deadline else 3600)).isoformat(), RID))
    _sql(env["db"], "UPDATE graph_attempt_runs SET status='ended',ai_run_id=?,ended_at=? WHERE exec_id=?",
         (RID, OLD, assignment["intent_exec_id"]))
    end_record(RID, doc)
    registry = service._runs
    registry[RID] = {"run_id": RID, "status": "finished", "end_reason": "exited"}
    original_end, original_loop = runs.get(RID), loops.get(RID)
    history = _sql(env["db"], "SELECT * FROM document_reviews")
    original_doc = documents.get_by_id(doc)
    assert group_ai_leases.get_by_run_id(RID) is None
    assert legacy_adapter.node_running(FA, key)
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    assert loops.get(RID) == original_loop
    assert service.sweep_unowned_review_loops(now=NOW) == 1
    reason = "total_timeout" if deadline else "retry_exhausted"
    row = loops.get(RID)
    assert (row["current_stage"], row["stop_reason"]) == ("stopped", reason)
    assert row["stop_detail"] == (review.UNOWNED_LOOP_DEADLINE_STOP_DETAIL if deadline else review.UNOWNED_LOOP_STOP_DETAIL)
    assert runs.get(RID) == original_end and service._runs is registry
    assert not legacy_adapter.node_running(FA, key)
    # No run read/settle hook is needed before the card read and explicit command.
    _read(env)
    assert _wire(env, key)["intervention"] == {"kind": "needs_intervention", "reason": "review_not_passed:" + reason}
    assert _sql(env["db"], "SELECT * FROM document_reviews") == history
    assert documents.get_by_id(doc) == original_doc
    hops = _first_hops(monkeypatch)
    command(env, "step")
    assert hops == ["rework"]
    assert one(env, key, "result_review", 2)["stage_state"] == "active"
    service.startup_recover_leases.assert_not_called()


@pytest.mark.parametrize("available", [False, True])
def test_existing_tick_sweeps_without_remote_schema(sweep, monkeypatch, available):
    aged_loop(sweep)
    monkeypatch.setattr(remote, "available", lambda: available)
    jobs = Mock()
    monkeypatch.setattr(remote, "_jobs", lambda: jobs)
    monkeypatch.setattr(remote, "_settle_in_process", Mock())
    monkeypatch.setattr(remote, "_hold_rows", lambda: [])
    monkeypatch.setattr(remote, "_recover_graph", Mock())
    jobs.unfenced_expired_ai_jobs.return_value = []
    actual = service.sweep_unowned_review_loops
    monkeypatch.setattr(service, "sweep_unowned_review_loops", lambda: actual(now=NOW))
    for _ in range(remote.REVIEW_LOOP_SWEEP_EVERY_TICKS): remote.tick()
    assert loops.get(RID)["stop_reason"] is None
    for _ in range(remote.REVIEW_LOOP_SWEEP_EVERY_TICKS): remote.tick()
    assert loops.get(RID)["stop_reason"] == "retry_exhausted"
    assert jobs.advance_ai_jobs.call_count == (remote.REVIEW_LOOP_SWEEP_EVERY_TICKS * 2 if available else 0)


@pytest.mark.parametrize("owner", ["live", "stage_handoff", "graph_handoff", "chain_handoff", "handoff_error",
    "held", "remote_alive", "remote_error", "unknown_holds", "lease", "lease_handoff", "graph_running",
    "graph_requested", "foreign", "grace", "ttl", "human_hold", "stopped", "identity"])
def test_active_owners_and_exclusions_remain_unchanged(sweep, monkeypatch, owner):
    run_id = "aiv_20261109_000001" if owner == "foreign" else RID
    row = aged_loop(sweep, run_id=run_id,
        age=100 if owner == "grace" else 1000 if owner == "ttl" else 3600,
        deadline=OLD if owner == "grace" else FUTURE)
    if owner == "live": service._runs[run_id] = {"run_id": run_id, "status": "running"}
    if owner == "stage_handoff": review_stage._handoffs[(FA, "node")] = (run_id, time.monotonic() + 600)
    if owner == "graph_handoff":
        store = connection.get_store(); real = store._fetch_all
        monkeypatch.setattr(store, "_fetch_all", lambda sql, params=():
            [{"run_id": "graph-owner"}] if sql.startswith("SELECT DISTINCT run_id FROM graph_attempt_runs") else real(sql, params))
        monkeypatch.setattr(ledger, "handing_off", lambda rid: rid == "graph-owner")
    if owner == "chain_handoff":
        admission.db_paused.upsert(group_id=FA, doc_ref=row["doc_ref"], paused_by="u_kim",
            paused_at=connection.now_iso(), continuation_target_seq=None, docs_target=None, docs_reached=0,
            stop_kind="system", stop_code="hop_handoff", stop_run_id=run_id)
    if owner == "handoff_error": monkeypatch.setattr(review_stage, "handoff_owned_by", Mock(side_effect=RuntimeError("read error")))
    if owner == "held": monkeypatch.setattr(remote, "held_run_ids", lambda: {run_id})
    if owner == "remote_alive": monkeypatch.setattr(remote, "remote_alive", lambda _: True)
    if owner == "remote_error": monkeypatch.setattr(remote, "remote_alive", Mock(side_effect=RuntimeError("remote unreadable")))
    if owner == "unknown_holds": monkeypatch.setattr(admission, "_restart_held_run_ids", lambda: None)
    if owner in ("lease", "lease_handoff"):
        monkeypatch.setattr(group_ai_leases, "get_by_run_id", lambda _: {"expires_at": FUTURE,
                            "state": "handoff" if owner == "lease_handoff" else "active"})
    if owner in ("graph_running", "graph_requested"):
        store = connection.get_store(); real = store._fetch_all
        monkeypatch.setattr(store, "_fetch_all", lambda sql, params=():
            [{"status": "running" if owner == "graph_running" else "requested", "lease_expires_at": FUTURE}]
            if sql.startswith("SELECT status, lease_expires_at") else real(sql, params))
    if owner in ("human_hold", "stopped"):
        _sql(sweep["db"], "UPDATE ai_invoke_document_review_loops SET current_stage='stopped',stop_reason=?,stop_detail='terminal outcome' WHERE run_id=?",
             ("review_verdict_hold" if owner == "human_hold" else "review_stalled", run_id))
        row = loops.get(run_id)
    if owner == "identity":
        store = ai_run_identity.get_store(); real = store._fetch_one
        monkeypatch.setattr(store, "_fetch_one", lambda sql, params=():
            {"project_id": "other", "group_id": FA} if sql.startswith("SELECT * FROM ai_run_owners WHERE") else real(sql, params))
    if owner in ("stage_handoff", "graph_handoff", "chain_handoff", "handoff_error"):
        assert service.review_loop_owner_evidence(row, held=set(), before=None, now=NOW) == "handoff"
    before_chain = admission.db_paused.get_by_group(FA)
    for _ in range(3): assert service.sweep_unowned_review_loops(now=NOW) == 0
    assert loops.get(run_id) == row and runs.get(run_id) is None
    assert admission.db_paused.get_by_group(FA) == before_chain


def test_confirmation_resets_after_owner_and_progress_and_reason_change(sweep, monkeypatch):
    row = aged_loop(sweep)
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    service._runs[RID] = {"run_id": RID, "status": "running"}
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    service._runs.clear()
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    _sql(sweep["db"], "UPDATE ai_invoke_document_review_loops SET updated_at=? WHERE run_id=?",
         ((NOW - timedelta(seconds=3500)).isoformat(), RID))
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    _sql(sweep["db"], "UPDATE ai_invoke_document_review_loops SET deadline_at=? WHERE run_id=?", (OLD, RID))
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    assert service.sweep_unowned_review_loops(now=NOW) == 1
    assert loops.get(RID)["stop_reason"] == "total_timeout"


def test_stop_unowned_cas_preserves_history_and_rejects_other_reasons(sweep, monkeypatch):
    row = aged_loop(sweep)
    with pytest.raises(ValueError):
        loops.stop_unowned(RID, stop_reason="review_stalled", detail="bad", expected_updated_at=row["updated_at"])
    original = loops.stop_unowned
    def racing(run_id, **kw):
        _sql(sweep["db"], "UPDATE ai_invoke_document_review_loops SET updated_at=? WHERE run_id=?", (BEFORE, RID))
        return original(run_id, **kw)
    monkeypatch.setattr(loops, "stop_unowned", racing)
    assert twice() == [0, 0]
    latest = loops.get(RID)
    assert latest["stop_reason"] is None and latest["updated_at"] == BEFORE
    assert original(RID, stop_reason="retry_exhausted", detail="closed", expected_updated_at=BEFORE, at=FUTURE)
    assert not original(RID, stop_reason="retry_exhausted", detail="closed", expected_updated_at=BEFORE)
    result = loops.get(RID)
    assert {k: v for k, v in result.items() if k not in {"current_stage", "stop_reason", "stop_detail", "updated_at"}} == \
           {k: v for k, v in row.items() if k not in {"current_stage", "stop_reason", "stop_detail", "updated_at"}}


def test_bad_row_does_not_block_other_rows_and_missing_end_is_not_synthesized(sweep, monkeypatch):
    aged_loop(sweep, run_id="air_bad"); aged_loop(sweep, run_id="air_good")
    real = admission.review_loop_owner_evidence
    def evidence(row, **kw):
        if row["run_id"] == "air_bad": raise RuntimeError("bad row")
        return real(row, **kw)
    monkeypatch.setattr(admission, "review_loop_owner_evidence", evidence)
    assert twice() == [0, 1]
    assert loops.get("air_bad")["stop_reason"] is None
    assert loops.get("air_good")["stop_reason"] == "retry_exhausted"
    assert runs.get("air_good") is None


@pytest.fixture
def graph_sweep(scene, monkeypatch):
    s = scene
    monkeypatch.setattr(remote, "held_run_ids", lambda: set())
    monkeypatch.setattr(remote, "remote_alive", lambda _: False)
    monkeypatch.setattr(remote, "_unowned_request_candidates", {})
    monkeypatch.setattr(remote, "_unowned_sweep_ticks", 0)
    monkeypatch.setattr(remote, "REVIEW_LOOP_SWEEP_EVERY_TICKS", 1)
    monkeypatch.setattr(service, "sweep_unowned_review_loops", lambda: 0)
    monkeypatch.setattr(service, "_runs", {})
    monkeypatch.setattr(group_ai_leases, "get_active", lambda _: None)
    token = run_store.token_ids(s.rid, s.key)[-1]
    job = {"run_id": s.rid, "flow_id": FA, "node_key": s.key, "stage": "result", "token": {"token_id": token},
           "graph_attempt_id": s.aid, "project_id": "flowgate"}
    _sql(s.env["db"], "UPDATE graph_attempt_runs SET created_at=?,request_json=? WHERE exec_id=?",
         (OLD, json.dumps({"type": "write", "job": job}), s.eid))
    s.failures = []
    original = node_dispatch.record_ai_failure
    def failure(job):
        s.failures.append(job); return original(job)
    monkeypatch.setattr(node_dispatch, "record_ai_failure", failure)
    return s


@pytest.mark.parametrize("kind", ["write", "hop", "loop"])
def test_graph_ttl_closes_only_after_two_sweeps(graph_sweep, monkeypatch, kind):
    s = graph_sweep
    if kind != "write":
        payload = {"type": kind, "flow_id": FA}
        _sql(s.env["db"], "UPDATE graph_attempt_runs SET request_json=? WHERE exec_id=?", (json.dumps(payload), s.eid))
        settle = Mock(); monkeypatch.setattr(execution, "settle", settle)
    row = ledger.get(s.eid)
    remote._sweep_unowned()
    assert ledger.get(s.eid) == row
    remote._sweep_unowned()
    assert ledger.get(s.eid)["status"] == "cancelled"
    assert not legacy_adapter.node_ai_run_active(FA, s.key)
    if kind == "write":
        assert s.failures[0]["start_failure_info"]["reason"] == "stalled_unowned_request"
        assert s.failures[0]["start_failure"] == "start_failed"
    else: settle.assert_called_once_with(FA)


@pytest.mark.parametrize("owner", ["running", "admitted", "paused", "stopping", "time_machine", "lease", "merge", "handoff", "recent", "live"])
def test_graph_owners_preserve_request_and_queued_request_refills(graph_sweep, monkeypatch, owner):
    s = graph_sweep
    if owner in ("running", "admitted"):
        run_store._affected("INSERT INTO graph_node_runs(run_id,node_key,status,updated_at) VALUES (?,'sibling','open',?)", [s.rid, ledger.now()])
        aid = run_store.open_attempt(s.rid, "sibling", 1)
        eid = ledger.request(aid, s.rid, "write", {"type": "write", "job": {}})
        if owner == "running": ledger.acquire(eid, "air_owner")
        else: run_store._affected("UPDATE graph_attempt_runs SET ai_run_id='air_owner' WHERE exec_id=?", [eid])
    if owner in ("paused", "stopping"):
        _sql(s.env["db"], "UPDATE graph_runs SET status=? WHERE run_id=?", (owner, s.rid))
    if owner == "time_machine": monkeypatch.setattr(execution._time_machine(), "busy", lambda _: True)
    if owner == "lease": monkeypatch.setattr(group_ai_leases, "get_active", lambda _: {"run_id": "air_owner"})
    if owner == "merge": monkeypatch.setattr(execution.result_merge, "holding", lambda _: True)
    if owner == "handoff": monkeypatch.setattr(ledger, "handing_off", lambda _: True)
    if owner == "recent": _sql(s.env["db"], "UPDATE graph_attempt_runs SET created_at=? WHERE exec_id=?", (FUTURE, s.eid))
    if owner == "live": service._runs[RID] = {"run_id": RID, "status": "running", "graph_exec_id": s.eid}
    row = ledger.get(s.eid)
    for _ in range(3): remote._sweep_unowned()
    assert ledger.get(s.eid) == row and not s.failures
    if owner == "running":
        ledger.end(eid, ledger.get(eid)["lease_token"])
        monkeypatch.setattr(execution, "replay", Mock())
        execution.settle(FA)
        assert any(call.args[0]["exec_id"] == s.eid for call in execution.replay.call_args_list)


def test_supervisor_db_less_and_independent_failures(monkeypatch):
    monkeypatch.setattr(remote, "REVIEW_LOOP_SWEEP_EVERY_TICKS", 1)
    monkeypatch.setattr(remote, "_unowned_sweep_ticks", 0)
    monkeypatch.setattr(remote, "available", lambda: False)
    monkeypatch.setattr(remote, "_store", Mock(side_effect=RuntimeError("DB-less")))
    remote.tick()
    store = Mock(); store.table_exists.return_value = True
    monkeypatch.setattr(remote, "_store", lambda: store)
    monkeypatch.setattr(service, "sweep_unowned_review_loops", Mock(side_effect=RuntimeError("loop failure")))
    close = Mock(); monkeypatch.setattr(execution, "_close_unowned_requests", close)
    remote.tick()
    close.assert_called_once()
