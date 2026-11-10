"""0159 T#1: restart recovery against migrated SQLite, real card reads and commands."""
import json
import threading
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
from flowgate.card_settings import legacy_adapter
from flowgate.graph_run import execution, execution_store as ledger, scheduling
from modules.flow_gate.db import ai_invoke_document_review_loops as loops
from modules.flow_gate.db import ai_invoke_runs as runs, ai_run_identity, group_ai_leases, connection
from modules.flow_gate.graph_workflow.services import node_dispatch
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import ai_invoke_service as service
from modules.flow_gate.services.ai_invoke import admission, remote_supervisor as remote, review

OLD = "2000-01-01T00:00:00+00:00"
BEFORE = "2026-11-10T09:00:00+09:00"
FUTURE = "2099-01-01T00:00:00Z"
RID = "air_restart_0159"


@pytest.fixture
def restart(env, monkeypatch):
    monkeypatch.setattr(service, "_runs", {})
    monkeypatch.setattr(admission, "now_iso", lambda: BEFORE)
    monkeypatch.setattr(admission, "_startup_recovery_before", None)
    monkeypatch.setattr(service, "startup_recover_handoffs", lambda: 0)
    from modules.flow_gate.services import q_service
    monkeypatch.setattr(q_service, "startup_recover_question_responders", lambda: 0)
    monkeypatch.setattr(remote, "held_run_ids", lambda: set())
    monkeypatch.setattr(remote, "remote_alive", lambda _id: False)
    monkeypatch.setattr(remote, "run_has_unsettled_job", lambda _id: False)
    return env


def active_loop(env, run_id=RID, stage="rework", doc=None):
    _loop_row(doc or f"{FA}.0001-R", run_id, stage)
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET started_at=?,updated_at=? WHERE run_id=?",
         (OLD, OLD, run_id))
    return loops.get(run_id)


def end_record(run_id, doc):
    runs.upsert({"run_id":run_id,"group_id":FA,"project_id":"flowgate","doc_ref":doc,
                 "mode":"single","outcome":"complete","end_reason":"exited","resumable":False,
                 "started_at":OLD,"finished_at":OLD,"created_at":OLD,"updated_at":OLD})


@pytest.mark.parametrize("stage", ["review", "rework"])
def test_issues_hop_restart_unlocks_card_and_step_starts_rework(
        restart, decided, starts, fork_join, completions, monkeypatch, stage):
    env = restart
    key, doc, assignment = _reviewed_ai_result(env, monkeypatch, starts)
    _review_row(doc, 0, "issues", RID)
    from modules.flow_gate.db import documents
    documents.update(doc, {"doc_review_status":"rejected", "rejection_reason":"fix the findings"})
    active_loop(env, stage=stage, doc=doc)
    _sql(env["db"], "UPDATE graph_attempt_runs SET status='ended',ai_run_id=?,ended_at=? WHERE exec_id=?",
         (RID, OLD, assignment["intent_exec_id"]))
    end_record(RID, doc)
    original_end = runs.get(RID)
    assert group_ai_leases.get_by_run_id(RID) is None
    assert legacy_adapter.node_running(FA, key) is True
    history = _sql(env["db"], "SELECT * FROM document_reviews")
    document_before = documents.get_by_id(doc)

    assert service.startup_recover_leases() == 0

    row = loops.get(RID)
    assert (row["current_stage"], row["stop_reason"]) == ("stopped", "restart_orphaned")
    assert row["stop_detail"] == "review loop orphaned by server restart (no live run, hold or lease)"
    assert runs.get(RID) == original_end
    assert legacy_adapter.node_running(FA, key) is False
    _read(env)
    assert _wire(env, key)["intervention"]["kind"] == "needs_intervention"
    assert _sql(env["db"], "SELECT * FROM document_reviews") == history
    assert documents.get_by_id(doc) == document_before
    hops = _first_hops(monkeypatch)
    command(env, "step")
    assert hops == ["rework"]
    assert one(env, key, "result_review", 2)["stage_state"] == "active"


def test_restart_orphan_reentry_is_explicit_only():
    from flowgate.card_intervention import reasons
    reason = "review_not_passed:restart_orphaned"
    assert reasons.command_reentry_due(reason) is True
    assert reasons.retry_due([reason]) is False


def test_missing_end_is_written_once_and_identity_conflict_keeps_loop(restart):
    env = restart
    active_loop(env)
    service.startup_recover_leases()
    end = runs.get(RID)
    assert (end["end_reason"],end["outcome"],bool(end["resumable"])) == ("orphaned_by_restart","none",False)
    assert end["doc_ref"] == f"{FA}.0001-R" and end["started_at"] == OLD
    first = loops.get(RID)
    service.startup_recover_leases()
    assert loops.get(RID) == first and runs.get(RID) == end
    assert loops.stop_for_restart_orphan(RID) is False


@pytest.mark.parametrize("evidence", ["live_run","held","remote_alive","remote_error","group_lease",
                                     "graph_running","graph_requested","recent","foreign"])
def test_owner_evidence_is_read_only_and_sweep_preserves_owner(restart, monkeypatch, evidence):
    env = restart
    run_id = "aiv_20261109_000001" if evidence == "foreign" else RID
    row = active_loop(env, run_id)
    held = set()
    expected = {"held":"remote_hold","remote_alive":"remote_hold","remote_error":"remote_hold",
                "graph_running":"graph_lease","graph_requested":"graph_lease"}.get(evidence,evidence)
    if evidence == "live_run":
        service._runs[run_id] = {"run_id":run_id,"status":"running"}
    if evidence == "held": held.add(run_id)
    if evidence == "remote_alive": monkeypatch.setattr(remote,"remote_alive",lambda _:True)
    if evidence == "remote_error": monkeypatch.setattr(remote,"remote_alive",Mock(side_effect=RuntimeError("unreadable")))
    if evidence == "group_lease":
        monkeypatch.setattr(group_ai_leases,"get_by_run_id",lambda _: {"run_id":run_id,"expires_at":FUTURE})
    if evidence.startswith("graph_"):
        store = connection.get_store()
        real = store._fetch_all
        monkeypatch.setattr(store,"_fetch_all",lambda sql,params=():
            [{"status":"requested" if evidence == "graph_requested" else "running","lease_expires_at":FUTURE}]
            if sql.startswith("SELECT status, lease_expires_at") else real(sql,params))
    if evidence == "recent":
        # Same instant, different offsets and precision; lexical comparison would get this wrong.
        _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET updated_at=? WHERE run_id=?",
             ("2026-11-10T00:00:00.000Z",run_id))
        row = loops.get(run_id)
    assert service.review_loop_owner_evidence(row,held=held,before=BEFORE) == expected
    admission._startup_recover_orphan_review_loops(held,BEFORE)
    assert loops.get(run_id) == row and runs.get(run_id) is None
    if evidence == "recent":
        assert service.review_loop_owner_evidence(row,held=held,before=None) is None


def test_unknown_holds_skip_entire_sweep_and_hold_release_closes_lease_free_loop(restart, monkeypatch):
    env = restart
    original = active_loop(env)
    monkeypatch.setattr(remote,"held_run_ids",Mock(side_effect=RuntimeError("hold unavailable")))
    service.startup_recover_leases()
    assert loops.get(RID) == original and runs.get(RID) is None
    monkeypatch.setattr(remote,"held_run_ids",lambda:{RID})
    # The durable hold is not marked resumed until this call returns.
    monkeypatch.setattr(remote,"remote_alive",lambda _:True)
    assert admission.resume_remote_held_recovery(RID) == []
    assert loops.get(RID)["stop_reason"] == "restart_orphaned"


@pytest.mark.parametrize("stage,reason",[("stopped","review_verdict_hold"),("stopped","review_passed"),("stopped","retry_exhausted")])
def test_terminal_or_human_reason_never_changes(restart, stage, reason):
    env = restart
    active_loop(env)
    _sql(env["db"],"UPDATE ai_invoke_document_review_loops SET current_stage=?,stop_reason=?,stop_detail=? WHERE run_id=?",
         (stage,reason,None if reason == "review_passed" else "terminal outcome",RID))
    original = loops.get(RID)
    service.startup_recover_leases()
    assert loops.get(RID) == original


def test_row_failure_does_not_block_next_loop(restart,monkeypatch,caplog):
    active_loop(restart,"air_bad")
    active_loop(restart,"air_good")
    real=admission.review_loop_owner_evidence
    def evidence(row,**kw):
        if row["run_id"] == "air_bad": raise RuntimeError("row failure")
        return real(row,**kw)
    monkeypatch.setattr(admission,"review_loop_owner_evidence",evidence)
    service.startup_recover_leases()
    assert loops.get("air_bad")["stop_reason"] is None
    assert loops.get("air_good")["stop_reason"] == "restart_orphaned"
    assert "air_bad" in caplog.text


@pytest.fixture
def request_scene(scene,monkeypatch):
    s=scene
    monkeypatch.setattr(remote,"held_run_ids",lambda:set())
    monkeypatch.setattr(remote,"remote_alive",lambda _:False)
    monkeypatch.setattr(group_ai_leases,"get_active",lambda _:None)
    monkeypatch.setattr(execution.result_merge,"recover",lambda:None)
    monkeypatch.setattr(execution.stop,"recover",lambda:None)
    monkeypatch.setattr(service,"_runs",{})
    monkeypatch.setattr(service,"startup_recover_handoffs",lambda:0)
    from modules.flow_gate.services import q_service
    monkeypatch.setattr(q_service,"startup_recover_question_responders",lambda:0)
    # Real failure recorder needs the open row's actual dispatch token and attempt.
    token = run_store.token_ids(s.rid,s.key)[-1]
    job={"run_id":s.rid,"flow_id":FA,"node_key":s.key,"stage":"result","token":{"token_id":token},
         "graph_attempt_id":s.aid,"project_id":"flowgate"}
    _sql(s.env["db"],"UPDATE graph_attempt_runs SET created_at=?,request_json=? WHERE exec_id=?",
         (OLD,json.dumps({"type":"write","job":job}),s.eid))
    s.failures=[]
    original=node_dispatch.record_ai_failure
    def failure(job):
        s.failures.append(job)
        return original(job)
    monkeypatch.setattr(node_dispatch,"record_ai_failure",failure)
    service.startup_recover_leases()
    return s


@pytest.mark.parametrize("mode",["no_admission","run_in_progress","guard_failure"])
def test_replayed_unowned_request_is_cancelled_and_node_records_start_failed(request_scene,monkeypatch,mode):
    s=request_scene
    if mode == "run_in_progress":
        from fastapi import HTTPException
        monkeypatch.setattr(execution,"replay",Mock(side_effect=HTTPException(409,detail={"code":"run_in_progress"})))
    else:
        monkeypatch.setattr(execution,"replay",Mock())
    if mode == "guard_failure":
        real=execution.with_flow_guard
        first=[True]
        def guard(group,body):
            if first.pop() if first else False: raise RuntimeError("one failed transaction")
            return real(group,body)
        monkeypatch.setattr(execution,"with_flow_guard",guard)
    execution.recover()
    assert ledger.get(s.eid)["status"] == "cancelled"
    assert not run_store._all("SELECT * FROM graph_attempt_runs WHERE status='requested' AND ai_run_id IS NULL",[])
    [failure]=s.failures
    assert failure["start_failure"] == "start_failed"
    assert failure["start_failure_info"]["reason"] == "restart_unowned_request"
    node=next(n for n in run_store.node_runs(s.rid) if n["node_key"] == s.key)
    assert node["dispatch_error_code"] == "ai_invoke_failed"


@pytest.mark.parametrize("owner",["running","admitted","group_lease","remote_hold","remote_error","live_run","time_machine","stopping","recent"])
def test_requested_owner_or_exclusion_is_preserved(request_scene,monkeypatch,owner):
    s=request_scene
    monkeypatch.setattr(execution,"replay",Mock())
    if owner in ("running","admitted","remote_hold"):
        run_store._affected("INSERT INTO graph_node_runs(run_id,node_key,status,updated_at) VALUES (?,'sibling','open',?)",[s.rid,ledger.now()])
        aid=run_store.open_attempt(s.rid,'sibling',1)
        eid=ledger.request(aid,s.rid,'write',{"type":"write","job":{}})
        if owner == "running": ledger.acquire(eid,"air_owner")
        else: run_store._affected("UPDATE graph_attempt_runs SET ai_run_id='air_owner' WHERE exec_id=?",[eid])
        if owner == "remote_hold":
            run_store._affected("UPDATE graph_attempt_runs SET status='ended' WHERE exec_id=?",[eid])
            monkeypatch.setattr(remote,"held_run_ids",lambda:{"air_owner"})
    if owner == "group_lease": monkeypatch.setattr(group_ai_leases,"get_active",lambda _: {"run_id":"air_owner"})
    if owner == "remote_error": monkeypatch.setattr(remote,"held_run_ids",Mock(side_effect=RuntimeError("holds unreadable")))
    if owner == "live_run":
        service._runs[RID]={"run_id":RID,"status":"running","graph_exec_id":s.eid}
    if owner == "time_machine": monkeypatch.setattr(execution._time_machine(),"busy",lambda _:True)
    if owner == "stopping": _sql(s.env["db"],"UPDATE graph_runs SET status='stopping' WHERE run_id=?",(s.rid,))
    if owner == "recent": _sql(s.env["db"],"UPDATE graph_attempt_runs SET created_at=? WHERE exec_id=?",(FUTURE,s.eid))
    # Test the post-replay scan: earlier recovery is free to admit requests or finish Stop.
    before=ledger.get(s.eid)
    execution._close_unowned_requests(started_before=service.startup_recovery_started_at())
    assert ledger.get(s.eid) == before and not s.failures
    if owner == "running":
        ledger.end(eid,ledger.get(eid)["lease_token"])
        # The real settle/refill dispatches the waiting request after its owner ends.
        execution.settle(FA)
        assert any(c.args[0]["exec_id"] == s.eid for c in execution.replay.call_args_list)


def test_identity_mismatch_does_not_close_loop(restart,monkeypatch,caplog):
    original=active_loop(restart)
    store=ai_run_identity.get_store()
    real=store._fetch_one
    monkeypatch.setattr(store,"_fetch_one",lambda sql,params=():
        {"project_id":"other","group_id":FA} if sql.startswith("SELECT * FROM ai_run_owners WHERE") else real(sql,params))
    service.startup_recover_leases()
    assert loops.get(RID) == original and runs.get(RID) is None
    assert "ai_run_identity_conflict" in caplog.text


def test_restart_scan_does_not_bind_loop_reads_to_a_temporary_connection_store():
    """A remote fixture may patch connection.get_store before the first restart scan.

    A fresh interpreter preserves that first-import order; the following card must
    read the active database after the temporary remote store has been closed.
    """
    import subprocess
    import sys
    from pathlib import Path
    from textwrap import dedent

    script = dedent('''
        import sqlite3
        from modules.flow_gate.services.ai_invoke import admission
        from modules.flow_gate.db import connection

        class Store:
            def __init__(self):
                self.db = sqlite3.connect(":memory:")
                self.db.row_factory = sqlite3.Row

            def table_exists(self, name):
                return self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    [name]).fetchone() is not None

            def _fetch_one(self, sql, params):
                row = self.db.execute(sql, params).fetchone()
                return dict(row) if row else None

        temporary = Store()
        original_get_store = connection.get_store
        connection.get_store = lambda: temporary
        admission._startup_recover_orphan_review_loops(set(), None)
        connection.get_store = original_get_store
        temporary.db.close()

        active = Store()
        active.db.execute("CREATE TABLE ai_invoke_document_review_loops (run_id TEXT)")
        active.db.execute("INSERT INTO ai_invoke_document_review_loops VALUES ('active-store')")
        connection.STORE = active
        try:
            from modules.flow_gate.db import ai_invoke_document_review_loops as loops
            assert loops.get("active-store") == {"run_id": "active-store"}
        finally:
            active.db.close()
    ''')
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", script],
        cwd=Path(__file__).resolve().parents[2], capture_output=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
