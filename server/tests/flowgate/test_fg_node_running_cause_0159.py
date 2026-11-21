"""0159 T#3: migrated SQLite, read-only R4/R5 and isolated recovery to completion.

External AI is a recorder; clocks are injected. No sleeps, restarts or deployed DB.
"""
import time
from datetime import timedelta
from unittest.mock import Mock

import pytest

from test_fg_ai_roles_l2_assignment import (
    B, FA, X, _sql, command, db_path, decided, env, one, starts, wired, node_run, roles, stored, flow,
)
from test_fg_card_intervention_assigned_0119 import (
    _loop_row, _read, _reviewed_ai_result, _wire, fork_join,
)
from test_fg_command_review_0125 import completions, _review_row, _pass
from test_fg_command_rerequest_0124 import _first_hops
from test_fg_card_settings import _doc, _h, node, edge, put_draft
from test_fg_restart_orphan_sweep_0159 import active_loop, end_record, OLD, BEFORE, FUTURE, RID
from test_fg_review_loop_sweeper_0159 import sweep, NOW
from flowgate.card_settings import legacy_adapter as legacy, review_stage
from flowgate.graph_run import execution_store as ledger
from modules.flow_gate.db import ai_invoke_document_review_loops as loops
from modules.flow_gate.db import documents, ai_invoke_runs as runs, group_ai_leases, connection
from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import ai_invoke_service as service
from modules.flow_gate.services.ai_invoke import admission, remote_supervisor as remote


@pytest.fixture
def clock(sweep, monkeypatch):
    monkeypatch.setattr(admission, "now_iso", lambda: NOW.isoformat())
    actual_owner = service.review_loop_owner
    def owner_at(row, *, now=None):
        return actual_owner(row, now=NOW if now is None else now)
    monkeypatch.setattr(service, "review_loop_owner", owner_at)
    return sweep


@pytest.fixture
def gate_policy(fork_join, monkeypatch):
    """The production human-confirm policy, with the shared harness's decided slots."""
    from flowgate.graph_policies import gate
    from modules.flow_gate.graph_workflow.domain import policies, run_policies
    old = {}
    for key, value, version, impl in gate.SLOTS:
        run_policies.register(key, value, version, impl)
        old[key] = policies.set_system_default(key, value, version)
        monkeypatch.setitem(policies._registered, key, impl)
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


def ended(env, exec_id, rid):
    _sql(env["db"], "UPDATE graph_attempt_runs SET status='ended',ai_run_id=?,ended_at=? WHERE exec_id=?",
         (rid, OLD, exec_id))


def new_attempt(env, key, rr):
    run_store.end_attempt(rr["run_id"], key)
    return run_store.open_attempt(rr["run_id"], key, 1)


def exec_for(env, attempt, run_id, rid, status="ended"):
    eid = ledger.request(attempt, run_id, "review", {})
    _sql(env["db"], "UPDATE graph_attempt_runs SET status=?,ai_run_id=?,lease_expires_at=? WHERE exec_id=?",
         (status, rid, FUTURE if status == "running" else None, eid))
    return eid


def old_loop(env, monkeypatch, starts, old_document=False):
    key, doc, rr = _reviewed_ai_result(env, monkeypatch, starts)
    ended(env, rr["intent_exec_id"], RID)
    active_loop(env, doc=doc)
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET deadline_at=? WHERE run_id=?", (FUTURE, RID))
    a2 = new_attempt(env, key, rr)
    current_doc = doc
    if old_document:
        current_doc = _doc(FA, 80, "TR")
        _sql(env["db"], "UPDATE graph_node_runs SET result_doc_id=? WHERE node_key=?", (current_doc, key))
    _loop_row(current_doc, "air_current_0159", "review")
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage='stopped',stop_reason='retry_exhausted',"
         "stop_detail='finished',started_at=?,updated_at=? WHERE run_id='air_current_0159'", (BEFORE, BEFORE))
    exec_for(env, a2, rr["run_id"], "air_current_0159")
    return key, doc, rr, a2


@pytest.mark.parametrize("old_document", [False, True])
@pytest.mark.parametrize("current_ended", [False, True])
def test_verification_5_current_attempt_ended_ignores_unowned_old_loop(
        clock, decided, starts, fork_join, completions, monkeypatch, old_document, current_ended):
    key, doc, rr, a2 = old_loop(clock, monkeypatch, starts, old_document)
    if current_ended:
        run_store.end_attempt(rr["run_id"], key)
    assert a2 != rr["attempt_id"]
    assert any(r["doc_id"] == doc for r in _sql(clock["db"],
        "SELECT doc_id FROM graph_node_documents WHERE group_id=? AND node_key=?", (FA, key)))
    before = loops.get(RID)
    assert before["current_stage"] == "rework" and before["stop_reason"] is None
    assert not legacy.node_running(FA, key)
    assert legacy.node_running_cause(FA, key) is None
    assert not ports.ai_working(FA, key, doc)
    wire = _wire(clock, key)
    assert wire["attempt_id"] == a2
    assert (wire["node_running"], wire["node_running_cause"]) == (False, None)
    assert loops.get(RID) == before


@pytest.mark.parametrize("owner", ["recent", "live", "stage_handoff", "graph_handoff", "chain_handoff",
    "held", "remote_alive", "unknown_holds", "group_lease", "requested", "running", "foreign", "exception"])
def test_old_loop_owner_evidence_fails_closed(clock, decided, starts, fork_join, completions, monkeypatch, owner):
    key, doc, rr, a2 = old_loop(clock, monkeypatch, starts)
    rid = RID
    if owner == "recent":
        _sql(clock["db"], "UPDATE ai_invoke_document_review_loops SET updated_at=? WHERE run_id=?", (NOW.isoformat(), rid))
    if owner == "live": service._runs[rid] = {"run_id": rid, "status": "running"}
    # Handoff belongs to the loop, not this card's direct AI branch.
    if owner == "stage_handoff": review_stage._handoffs[(FA, "other")] = (rid, time.monotonic() + 600)
    if owner == "graph_handoff": monkeypatch.setattr(ledger, "handing_off", lambda r: r == rr["run_id"])
    if owner == "chain_handoff":
        admission.db_paused.upsert(group_id=FA, doc_ref=doc, paused_by="u_kim", paused_at=BEFORE,
            continuation_target_seq=None, docs_target=None, docs_reached=0, stop_kind="system",
            stop_code="hop_handoff", stop_run_id=rid)
    if owner == "held": monkeypatch.setattr(remote, "held_run_ids", lambda: {rid})
    if owner == "remote_alive": monkeypatch.setattr(remote, "remote_alive", lambda r: r == rid)
    if owner == "unknown_holds": monkeypatch.setattr(admission, "_restart_held_run_ids", lambda: None)
    if owner == "group_lease":
        monkeypatch.setattr(group_ai_leases, "get_by_run_id", lambda r: {"run_id": rid, "expires_at": FUTURE} if r == rid else None)
    if owner in ("requested", "running"):
        # A real execution of another node owns this loop. It must not mask the
        # target card's loop cause through node_ai_run_active's direct branch.
        other = _sql(clock["db"], "SELECT node_key FROM graph_node_runs WHERE run_id=? AND node_key<>? LIMIT 1",
                     (rr["run_id"], key))[0]["node_key"]
        run_store.end_attempt(rr["run_id"], other)
        other_attempt = run_store.open_attempt(rr["run_id"], other, 1)
        exec_for(clock, other_attempt, rr["run_id"], rid, owner)
    if owner == "foreign":
        monkeypatch.setattr(admission.ai_run_identity, "foreign_run_id", lambda r: r == rid)
    if owner == "exception": monkeypatch.setattr(service, "review_loop_owner", Mock(side_effect=RuntimeError("owner unreadable")))
    before = loops.get(rid)
    assert legacy.node_running_cause(FA, key) == "review_loop:" + rid
    assert legacy.node_running(FA, key) and ports.ai_working(FA, key, doc)
    assert loops.get(rid) == before


@pytest.mark.parametrize("stage", ["review", "rework"])
@pytest.mark.parametrize("unknown", [None, "active_run", "attempt", "loop_attempt", "ended_attempt"])
def test_latest_current_or_unknown_attempt_keeps_running(clock, decided, starts, fork_join, completions, monkeypatch, stage, unknown):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    ended(clock, rr["intent_exec_id"], RID)
    active_loop(clock, doc=doc, stage=stage)
    if unknown == "active_run": monkeypatch.setattr(run_store, "active_run", lambda _: None)
    if unknown == "attempt":
        monkeypatch.setattr(run_store, "active_attempt_id", lambda *a: None)
        monkeypatch.setattr(run_store, "current_attempts", lambda *a: {})
    if unknown == "loop_attempt":
        _sql(clock["db"], "UPDATE graph_attempt_runs SET ai_run_id=NULL WHERE exec_id=?", (rr["intent_exec_id"],))
    if unknown == "ended_attempt": run_store.end_attempt(rr["run_id"], key)
    assert legacy.node_running(FA, key)
    assert legacy.node_running_cause(FA, key) == "review_loop:" + RID
    assert ports.ai_working(FA, key, doc)
