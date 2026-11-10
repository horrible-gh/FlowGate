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


@pytest.mark.parametrize("status,old", [("requested", False), ("requested", True), ("running", False)])
def test_graph_cause_including_older_attempt(clock, decided, starts, fork_join, completions, monkeypatch, status, old):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    _sql(clock["db"], "UPDATE graph_attempt_runs SET status=?,lease_expires_at=? WHERE exec_id=?",
         (status, FUTURE, rr["intent_exec_id"]))
    if old: new_attempt(clock, key, rr)
    assert legacy.node_running_cause(FA, key) == "graph_exec:" + rr["intent_exec_id"]
    assert legacy.node_running(FA, key)


@pytest.mark.parametrize("with_execution", [False, True])
def test_group_lease_cause(clock, decided, starts, fork_join, completions, monkeypatch, with_execution):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    ended(clock, rr["intent_exec_id"], RID)
    token = _sql(clock["db"], "SELECT token_id FROM graph_run_token_map WHERE run_id=? AND node_key=? LIMIT 1", (rr["run_id"], key))[0]["token_id"]
    lease_run = RID if with_execution else "air_lease_only"
    _sql(clock["db"], "UPDATE tokens SET ai_run_id=? WHERE token_id=?", (lease_run, token))
    monkeypatch.setattr(legacy.db_leases, "get", lambda _: {"run_id": lease_run, "expires_at": FUTURE})
    cause = "graph_exec:" + rr["intent_exec_id"] if with_execution else "group_lease:" + lease_run
    assert legacy.node_running_cause(FA, key) == cause
    assert legacy.node_running(FA, key)


def test_cause_priority_and_patch_compatibility(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    active_loop(clock, doc=doc)
    assert legacy.node_running_cause(FA, key) == "graph_exec:" + rr["intent_exec_id"]
    monkeypatch.setattr(review_stage, "_handoff_pending", lambda *a: True)
    assert legacy.node_running_cause(FA, key) == "handoff"
    monkeypatch.setattr(review_stage, "_handoff_pending", lambda *a: False)
    ended(clock, rr["intent_exec_id"], RID)
    assert legacy.node_running_cause(FA, key) == "review_loop:" + RID
    monkeypatch.setattr(legacy, "review_loops", lambda _: [])
    monkeypatch.setattr(legacy, "review_loop_active", lambda _: True)
    assert legacy.node_running_cause(FA, key) == "review_loop:unknown"
    monkeypatch.setattr(legacy, "node_ai_run_active", lambda *a: True)
    assert legacy.node_running_cause(FA, key) == "graph_exec:unknown"
    assert legacy.node_running(FA, key)


def test_active_attempt_fallback_cause(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    store = connection.get_store(); real = store._fetch_one
    monkeypatch.setattr(store, "_fetch_one", lambda sql, params=():
        None if sql.startswith("SELECT x.exec_id FROM graph_attempt_runs x JOIN graph_runs") else real(sql, params))
    assert legacy.node_running_cause(FA, key) == "graph_exec:"+rr["intent_exec_id"]
    assert legacy.node_running(FA, key)


def test_execution_cause_order_is_created_at_then_exec_id(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    a2 = new_attempt(clock, key, rr)
    eid = exec_for(clock, a2, rr["run_id"], "air_new_graph", status="requested")
    _sql(clock["db"], "UPDATE graph_attempt_runs SET created_at=? WHERE exec_id=?", (OLD, rr["intent_exec_id"]))
    assert legacy.node_running_cause(FA, key) == "graph_exec:"+eid
    _sql(clock["db"], "UPDATE graph_attempt_runs SET created_at=? WHERE exec_id=?", (OLD, eid))
    assert legacy.node_running_cause(FA, key) == "graph_exec:"+max(eid, rr["intent_exec_id"])


def test_document_cause_order_is_result_instruction_then_sorted_links(clock, monkeypatch):
    monkeypatch.setattr(review_stage, "ai_working", lambda *a: False)
    monkeypatch.setattr(run_store, "active_run", lambda _: {"run_id": "run_order"})
    monkeypatch.setattr(run_store, "node_runs", lambda _: [{"node_key": "key", "result_doc_id": "z_result", "instruction_doc_id": "z_instruction"}])
    store = connection.get_store(); real = store._fetch_all
    monkeypatch.setattr(store, "_fetch_all", lambda sql, params=():
        [{"doc_id": "b_link"}, {"doc_id": "a_link"}] if sql.startswith("SELECT doc_id FROM graph_node_documents") else real(sql, params))
    for winner, expected in [("z_result", ["z_result"]), ("z_instruction", ["z_result", "z_instruction"]),
                             ("b_link", ["z_result", "z_instruction", "a_link", "b_link"])]:
        seen = []
        def running(flow, key, doc):
            seen.append(doc)
            return {"run_id": doc} if doc == winner else None
        monkeypatch.setattr(legacy, "running_review_loop", running)
        assert legacy.node_running_cause(FA, "key") == "review_loop:"+winner
        assert seen == expected


@pytest.mark.parametrize("case", ["none", "exception", "unregistered", "idle_open", "idle_review", "done"])
def test_actual_run_read_cause_defaults(clock, decided, starts, fork_join, completions, monkeypatch, case):
    if case == "idle_open":
        key = flow(clock, extra_settings=(("instruction", "omit"),))
        roles(key, instruction="not_used", execution="not_used", review="not_used")
        command(clock, "step")
        wire = _wire(clock, key)
        assert wire["status"] == "open"
        assert (wire["node_running"], wire["node_running_cause"]) == (False, None)
        return
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    ended(clock, rr["intent_exec_id"], RID)
    if case in ("none", "exception"):
        monkeypatch.setattr(ports, "node_running", lambda *a: True)
        ports.register(ports.NODE_RUNNING_CAUSE, Mock(side_effect=RuntimeError("read failed")) if case == "exception" else lambda *a: None)
    elif case == "unregistered":
        ports.unregister(ports.NODE_RUNNING); ports.unregister(ports.NODE_RUNNING_CAUSE)
    else:
        status = {"idle_open": "open", "idle_review": "review", "done": "done"}[case]
        _sql(clock["db"], "UPDATE graph_node_runs SET status=? WHERE node_key=?", (status, key))
    wire = _wire(clock, key)
    if case in ("idle_review", "done"):
        assert wire["status"] == status
    assert "node_running_cause" in wire
    assert (wire["node_running"], wire["node_running_cause"]) == ((True, "unknown") if case in ("none", "exception") else (False, None))


def test_wrapper_delegates_grace_without_writes(clock, monkeypatch):
    row = active_loop(clock)
    before = loops.get(RID)
    held = {"some-owner"}
    monkeypatch.setattr(admission, "_restart_held_run_ids", lambda: held)
    evidence = Mock(return_value="recent")
    monkeypatch.setattr(admission, "review_loop_owner_evidence", evidence)
    assert service.review_loop_owner(row, now=NOW) == "recent"
    evidence.assert_called_once_with(row, held=held, before=NOW-timedelta(seconds=900), now=NOW)
    monkeypatch.setattr(admission, "_restart_held_run_ids", lambda: None)
    assert service.review_loop_owner(row, now=NOW) == "remote_hold"
    assert evidence.call_count == 1 and loops.get(RID) == before
    monkeypatch.setattr(admission, "_restart_held_run_ids", Mock(side_effect=RuntimeError("unreadable")))
    with pytest.raises(RuntimeError): service.review_loop_owner(row, now=NOW)


@pytest.mark.parametrize("recovery", ["sweep", "restart"])
def test_issues_stuck_recovered_rework_revision_rereview_followup_no_no_targets(
        clock, decided, starts, gate_policy, completions, monkeypatch, recovery):
    graph = put_draft(clock, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T"), node("tmp_gate", node_type="gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_a", "tmp_gate")])
    key, follow = graph["key_map"]["tmp_a"], graph["key_map"]["tmp_gate"]
    stored(key, "instruction", "omit"); stored(key, "review_count", 1)
    res = clock["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    roles(key, execution=X, review=B)
    command(clock, "full")
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts, key)
    assert doc.endswith("-TR")
    _review_row(doc, 0, "issues", RID)
    documents.update(doc, {"doc_review_status": "rejected", "rejection_reason": "fix findings"})
    active_loop(clock, doc=doc)
    _sql(clock["db"], "UPDATE ai_invoke_document_review_loops SET deadline_at=? WHERE run_id=?", (FUTURE, RID))
    ended(clock, rr["intent_exec_id"], RID)
    end_record(RID, doc)
    original_end = runs.get(RID)
    assert group_ai_leases.get_by_run_id(RID) is None
    assert (_wire(clock, key)["node_running"], _wire(clock, key)["node_running_cause"]) == (True, "review_loop:"+RID)
    if recovery == "sweep":
        assert service.sweep_unowned_review_loops(now=NOW) == 0
        assert service.sweep_unowned_review_loops(now=NOW) == 1
        reason = "retry_exhausted"
        service.startup_recover_leases.assert_not_called()
    else:
        monkeypatch.setattr(admission, "_startup_recovery_before", None)
        monkeypatch.setattr(service, "startup_recover_handoffs", lambda: 0)
        from modules.flow_gate.services import q_service
        monkeypatch.setattr(q_service, "startup_recover_question_responders", lambda: 0)
        assert admission.startup_recover_leases() == 0
        reason = "restart_orphaned"
    wire = _wire(clock, key)
    assert (wire["node_running"], wire["node_running_cause"]) == (False, None)
    assert wire["intervention"] == {"kind": "needs_intervention", "reason": "review_not_passed:"+reason}
    assert runs.get(RID) == original_end
    assert documents.get_by_id(doc)["revision_no"] == 0
    hops = _first_hops(monkeypatch)
    resumed = command(clock, "full")
    assert resumed.get("idle_reason") != "no_targets" and hops == ["rework"]
    rr2 = one(clock, key, "result_review", 2)
    assert rr2["stage_state"] == "active" and rr2["seq"] == rr["seq"]+1
    # The recorded AI performs its rework; retain the original issues review and write a new revision.
    documents.update(doc, {"revision_no": 1, "doc_review_status": "revised"})
    assert documents.get_by_id(doc)["revision_no"] == 1
    _pass(clock, key, 2, doc, "air_followup_pass_0159")
    state = _read(clock)
    assert node_run(clock, key)["status"] == "done"
    assert node_run(clock, follow)["status"] == "open"
    assert legacy.node_of_key(FA, follow)["node_type"] == "gate"
    assert state.get("idle_reason") != "no_targets"
    assert _sql(clock["db"], "SELECT verdict,revision_no FROM document_reviews WHERE doc_id=? ORDER BY id", (doc,)) == [
        {"verdict": "issues", "revision_no": 0}, {"verdict": "pass", "revision_no": 1}]
    for _ in range(3):
        assert command(clock, "full").get("idle_reason") != "no_targets"
        assert node_run(clock, follow)["status"] == "open"


def test_old_loop_guard_window_until_sweeper_closes(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr, a2 = old_loop(clock, monkeypatch, starts, old_document=True)
    assert not legacy.node_running(FA, key) and legacy.review_loop_active(doc)
    # Exercise the unmodified broad start guard, even while R4 excludes the loop.
    run = run_store.active_run(FA)
    start_loop = Mock()
    monkeypatch.setattr(legacy, "start_review_loop", start_loop)
    before = len(starts)
    assert review_stage.review_running(FA, key, doc) is False
    review_stage._start_person_review(FA, run, key, documents.get_by_id(doc), {"review_ai": B}, "u_kim", a2)
    start_loop.assert_not_called()
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    assert legacy.review_loop_active(doc)
    assert service.sweep_unowned_review_loops(now=NOW) == 1
    assert not legacy.review_loop_active(doc)
    assert len(starts) == before
    review_stage._start_person_review(FA, run, key, documents.get_by_id(doc), {"review_ai": B}, "u_kim", a2)
    start_loop.assert_called_once()
