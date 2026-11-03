"""flowgate.v02test.0004 T#1 / T#2 / T#4 — a duplicate start of one Graph AI execution.

B flowgate.v02test.0004.0001-R / NR 0003-NR: ``request_write`` queues its own start job (``node_dispatch.start_ai``) and
the same transaction's settle refill (``scheduling.queue_requests``) queues a replay of the same, still unclaimed request.
Both start after the commit: the first claims the execution (``scheduling.admit``) and runs it, the second is refused
``run_in_progress``. That refusal was stored as an ``admission_refused`` role invocation record — shown as the work
failing — and became the next review's ``prev_inv_id``.

Now (T#1) a refused start of an execution another start of it already owns records nothing (the ledger tells it, never
the message); every other refusal — another execution's lease, no free slot, a Time Machine operation, another refusal
code — is recorded as before and its durable request is replayed by the refill as before. (T#2) a start refused at
admission is never the previous call of a later one; a launched record, failed or not, stays one; a record stored
before names its refusal still, and the card's invocation list shows the previous call it would name now.

The harness of ``test_fg_provider_failure_retry_0121`` (real SQLite DB, routes, Graph engine, execution ledger) and the
launch journal of ``test_fg_ai_roles_l3_records``. The AI invoke start is a stand-in that takes the admission's Graph
steps in their order (``role_invocations.admission`` -> ``graph_execution_hooks.admission`` -> ``admit``); nothing calls
an AI.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_fg_card_settings import FA, _sql, db_path, decided, env, legacy_cards  # noqa: F401 — fixtures used by name
from test_fg_question_pending_0111 import card_outputs_port, questions  # noqa: F401 — fixtures used by name
from test_fg_provider_failure_retry_0121 import _to_result_stage
from test_fg_ai_roles_l3_records import CLI, launch, origin, run

from flowgate.ai_roles import invocation_view as view, invocations as inv, record_store as store
from flowgate.graph_run import execution_store as ledger, scheduling

pytestmark = pytest.mark.usefixtures("legacy_cards")

WIN, DUP, REPLAY = "aiv_20261103_910001", "aiv_20261103_910002", "aiv_20261103_910003"
BUSY = {"code": "run_in_progress", "message": "An AI run is already in progress."}


# ── harness ──────────────────────────────────────────────────────────────────

class _Starter:
    """The AI invoke start (``ai_invoke_service.start_run``) as far as a Graph execution's admission: the role invocation
    journal's refusal wrapper, the Graph admission wrapper and the launcher's claim, in ``admission.start_run``'s
    order. Each start takes the next run id; ``admitted`` lists the starts that claimed their execution."""

    def __init__(self, monkeypatch, run_ids):
        from modules.flow_gate.graph_workflow.services import ai_node_starter, node_dispatch
        from modules.flow_gate.services import ai_invoke_service, graph_execution_hooks, mention_service

        self.run_ids, self.admitted, self.failures = list(run_ids), [], []
        starter = self

        @inv.admission
        @graph_execution_hooks.admission
        def start_run(**kw):
            run_id = starter.run_ids.pop(0)
            graph_execution_hooks.call("admit", kw["graph_exec_id"], run_id, kw["group_id"], kw.get("graph_attempt_id"))
            starter.admitted.append(run_id)
            return {"run_id": run_id, "status": "running"}

        monkeypatch.setattr(mention_service, "build_mention_from_token_rec", lambda **kw: "mention")
        monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
        monkeypatch.setattr(node_dispatch, "_ai_starter", ai_node_starter.start_ai_invoke)
        monkeypatch.setattr(node_dispatch, "record_ai_failure", lambda job: starter.failures.append(job))
        self.start_ai = node_dispatch.start_ai


def _refill(job):
    """The settle refill of the job's run (``queue_requests``): the replay jobs it queues."""
    ctx = SimpleNamespace(run={"status": "running"}, run_id=job["run_id"], flow_id=FA, jobs=[])
    scheduling.queue_requests(ctx)
    return ctx.jobs


def _launch_run(job, run_id):
    """The AI run the admitted start builds, as the launch journal reads it."""
    return {"run_id": run_id, "invocation_origin": dict(job["invocation_origin"]), "graph_exec_id": job["graph_exec_id"],
            "provider_id": "aip_assignee", "requested_provider_id": "aip_assignee", "fallback_history": [],
            "cancel_event": threading.Event(), "timed_out": False}


def _begin(r):
    """The launch begins: its record is stored once the CLI is prepared (``cli_prepared`` flushes it)."""
    inv.begin(r, CLI)
    inv.cli_prepared(r, CLI, "posix_sh")


def _finish(r, code=0):
    inv.cli_outcome(r, stdout_full='{"type":"assistant.message","data":{"model":"x"}}', exit_code=code,
                    timed_out=False, process_exited_itself=True)
    inv.finish(r, "started_ok")
    inv.finalize(r)
    return r["invocations"][-1]


def _review_of(job, run_id):
    """The card's result review after its writer (an AI author)."""
    o = dict(job["invocation_origin"])
    o.update(purpose="result_review_round", author="ai",
             target={**o["target"], "stage": "result_review", "seq": 1})
    r = {**_launch_run(job, run_id), "invocation_origin": o}
    _begin(r)
    return _finish(r)


def _results(exec_id):
    return [(r["result"], r["result_detail"]) for r in store.by_exec(exec_id)]


def _attempt_requests(env, job):
    """The attempt's live requests (its instruction stage's execution was handed off, cancelled, before)."""
    return _sql(env["db"], "SELECT exec_id, status, ai_run_id FROM graph_attempt_runs WHERE attempt_id = ? "
                           "AND status IN ('requested', 'running')", (job["graph_attempt_id"],))


# ── T#1 / T#4-1 / T#4-2: the duplicate start ─────────────────────────────────

@pytest.mark.parametrize("order", ["refused_after_launch_began", "refused_before_launch_began"])
def test_a_duplicate_start_runs_the_execution_once_records_no_refusal_and_the_review_links_the_real_run(
        env, decided, monkeypatch, questions, order):
    _first, _second, job = _to_result_stage(env, monkeypatch)
    exec_id = job["graph_exec_id"]
    starter = _Starter(monkeypatch, [WIN, DUP])
    # The same transaction queued the request's own start and the refill's replay of the still unclaimed request.
    [replay] = _refill(job)

    starter.start_ai(dict(job))                                   # the request's own start: claims the execution
    winner = _launch_run(job, WIN)
    if order == "refused_after_launch_began":
        _begin(winner)                                            # the incident: refused 133 ms after the launch began
        replay()
    else:
        replay()
        _begin(winner)

    # one execution, run once by its owner; the duplicate neither took nor cancelled it, nor failed the card
    assert starter.admitted == [WIN]
    row = ledger.get(exec_id)
    assert (row["status"], row["ai_run_id"]) == ("requested", WIN)
    assert [(e["exec_id"], e["status"], e["ai_run_id"]) for e in _attempt_requests(env, job)] == [
        (exec_id, "requested", WIN)]
    assert starter.failures == []
    # no refusal record of the duplicate start; the refill replays nothing of an owned execution
    assert _results(exec_id) == [("running", None)]
    assert _refill(job) == []

    written = _finish(winner)
    assert _results(exec_id) == [("succeeded", None)]
    review = _review_of(job, "aiv_20261103_910009")
    assert (review["link_kind"], review["prev_inv_id"]) == ("review_of", written["inv_id"])


# ── T#1 / T#4-3 / T#4-4: real refusals stay refusals, and their request is replayed ─────────

@pytest.mark.parametrize("cause", ["another_execution_lease", "no_free_slot", "time_machine_operation"])
def test_a_real_refusal_is_recorded_and_its_durable_request_is_replayed_later(env, decided, monkeypatch, questions,
                                                                              cause):
    from flowgate.graph_time_machine import operation

    _first, _second, job = _to_result_stage(env, monkeypatch)
    exec_id = job["graph_exec_id"]
    starter = _Starter(monkeypatch, [DUP, REPLAY])
    blocked = {"on": True}
    if cause == "another_execution_lease":
        monkeypatch.setattr(scheduling.group_ai_leases, "get_active",
                            lambda _flow: {"run_id": "aiv_other_execution"} if blocked["on"] else None)
    elif cause == "no_free_slot":
        real = scheduling.available
        monkeypatch.setattr(scheduling, "available", lambda *a, **kw: 0 if blocked["on"] else real(*a, **kw))
    else:
        monkeypatch.setattr(operation, "busy", lambda _flow: blocked["on"])

    starter.start_ai(dict(job))

    assert starter.admitted == []
    assert _results(exec_id) == [("admission_refused", "run_in_progress")]      # recorded as before
    row = ledger.get(exec_id)
    assert (row["status"], row["ai_run_id"]) == ("requested", None)             # the durable request is kept
    assert starter.failures == []
    # once the cause is gone the refill replays the request, which now starts
    blocked["on"] = False
    [replay] = _refill(job)
    replay()
    assert starter.admitted == [REPLAY]
    assert (ledger.get(exec_id)["status"], ledger.get(exec_id)["ai_run_id"]) == ("requested", REPLAY)
    assert _results(exec_id) == [("admission_refused", "run_in_progress")]


def test_a_failed_owner_releases_its_claim_and_the_refill_replays_the_request(env, decided, monkeypatch, questions):
    _first, _second, job = _to_result_stage(env, monkeypatch)
    exec_id = job["graph_exec_id"]

    def owner_fails():
        scheduling.admit(exec_id, WIN, FA)
        raise RuntimeError("token issue failed")

    with pytest.raises(RuntimeError):
        scheduling.admission(owner_fails)
    assert ledger.get(exec_id)["ai_run_id"] is None
    assert scheduling.duplicate_start(exec_id) is False                         # nobody owns it any more
    assert len(_refill(job)) == 1


def test_another_refusal_code_of_an_owned_execution_is_still_recorded(env, decided, monkeypatch, questions):
    _first, _second, job = _to_result_stage(env, monkeypatch)
    exec_id = job["graph_exec_id"]
    scheduling.admit(exec_id, WIN, FA)

    @inv.admission
    def start(**_kw):
        raise HTTPException(409, {"code": "graph_worktree_unavailable", "message": "busy", "reason": "git_busy"})

    with pytest.raises(HTTPException):
        start(invocation_origin=dict(job["invocation_origin"]), graph_exec_id=exec_id)
    assert _results(exec_id) == [("admission_refused", "graph_worktree_unavailable")]


def test_duplicate_start_is_read_from_the_ledger_state_only(env, decided, monkeypatch, questions):
    _first, _second, job = _to_result_stage(env, monkeypatch)
    exec_id = job["graph_exec_id"]

    def state(status, owner):
        _sql(env["db"], "UPDATE graph_attempt_runs SET status = ?, ai_run_id = ? WHERE exec_id = ?",
             (status, owner, exec_id))
        return scheduling.duplicate_start(exec_id)

    assert state("requested", None) is False                    # unclaimed: a refusal of it is a real one
    assert state("requested", WIN) is True                      # claimed by another start
    assert scheduling.duplicate_start(exec_id, WIN) is False    # its own claim is no duplicate of itself
    assert state("running", WIN) is True
    assert state("ended", WIN) is True
    assert state("cancelled", WIN) is False                     # a stopped execution's refusal stays recorded
    assert state("lost", WIN) is False
    assert scheduling.duplicate_start("exe_missing") is False and scheduling.duplicate_start(None) is False

    # the journal asks the Graph port, and only for a run_in_progress refusal of a Graph execution
    state("running", WIN)
    busy = HTTPException(409, BUSY)
    assert inv.duplicate_start(busy, exec_id) is True
    assert inv.duplicate_start(busy, None) is False
    assert inv.duplicate_start(HTTPException(409, {**BUSY, "code": "inactive"}), exec_id) is False
    assert inv.duplicate_start(RuntimeError("no detail"), exec_id) is False
    from modules.flow_gate.services import graph_execution_hooks
    monkeypatch.setattr(graph_execution_hooks, "_handler", None)                # no Graph port: recorded as before
    assert inv.duplicate_start(busy, exec_id) is False


# ── T#2: the previous call is a launched record ─────────────────────────────

def _refused_row(o, exec_id="e"):
    inv.refused(o, HTTPException(409, BUSY), exec_id)
    rows = [r for r in store.for_node(FA, "removed", 50) if r["result"] == "admission_refused"]
    return rows[0]


def test_a_refusal_is_never_the_previous_call_and_a_failed_launch_still_is(env):
    w = run("w", origin("result_write"))
    w1 = launch(w)
    inv.finalize(w)
    refusal = _refused_row(origin("result_write_retry"))          # a refusal stored after the writer's success

    o = origin("result_review_round", "result_review", 1)
    o["author"] = "ai"
    rv = run("rv", o)
    r1 = launch(rv)
    inv.finalize(rv)
    assert (r1["link_kind"], r1["prev_inv_id"]) == ("review_of", w1["inv_id"])

    retry = run("w2", origin("result_write_retry"))
    w2 = launch(retry, code=1)                                    # a launched retry that failed
    inv.finalize(retry)
    assert w2["result"] == "call_failed"
    assert (w2["link_kind"], w2["prev_inv_id"]) == ("stage_retry", w1["inv_id"])
    _refused_row(origin("result_write_retry"))
    again = run("w3", origin("result_write_retry"))
    w3 = launch(again)
    inv.finalize(again)
    assert (w3["link_kind"], w3["prev_inv_id"]) == ("stage_retry", w2["inv_id"])   # the failed launch stays its link
    assert store.latest(w1["unit_key_hash"], w3["create_seq"])["result"] == "admission_refused"   # stored as it was
    assert refusal["prev_inv_id"] == w1["inv_id"]


def test_a_record_stored_naming_a_refusal_is_shown_with_its_real_previous_call(env):
    w = run("w", origin("result_write"))
    w1 = launch(w)
    inv.finalize(w)
    refusal = _refused_row(origin("result_write_retry"))
    o = origin("result_review_round", "result_review", 1)
    o["author"] = "ai"
    rv = run("rv", o)
    r1 = launch(rv)
    inv.finalize(rv)
    # a review stored before T#2 (#421 of the incident) named the refusal as its previous call
    _sql(env["db"], "UPDATE ai_role_invocations SET prev_inv_id = ? WHERE inv_id = ?", (refusal["inv_id"], r1["inv_id"]))
    stored = [r for r in store.for_node(FA, "removed", 50) if r["inv_id"] == r1["inv_id"]][0]
    assert stored["prev_inv_id"] == refusal["inv_id"]                          # the stored row is never rewritten
    assert view.item(stored)["link"] == {"kind": "review_of", "prev_inv_id": w1["inv_id"],
                                         "recorded_prev_inv_id": refusal["inv_id"]}
    # a record naming a launched record is shown as stored
    fresh = [r for r in store.for_node(FA, "removed", 50) if r["inv_id"] == refusal["inv_id"]][0]
    assert view.item(fresh)["link"] == {"kind": fresh["link_kind"], "prev_inv_id": w1["inv_id"],
                                        "recorded_prev_inv_id": w1["inv_id"]}


def test_a_review_stored_naming_a_refused_rework_is_shown_with_its_ai_writer(env, monkeypatch):
    """A review stored before T#2 named a refused rework of its own unit, and no rework of that unit launched: it is
    shown with the AI writer's launch — the previous call ``_links`` picks on the same records (rework first, else the
    AI writer's write, ``invocations.writer_unit_hash``). The stored row and ``recorded_prev_inv_id`` keep the refusal."""
    from flowgate.ai_roles import assignment_store
    w = run("w", origin("result_write"))
    w1 = launch(w)
    inv.finalize(w)
    refused_rework = _refused_row(origin("result_rework", "result_review", 1))      # same unit as the review
    o = origin("result_review_round", "result_review", 1)
    o["author"] = "ai"
    rv = run("rv", o)
    r1 = launch(rv)
    inv.finalize(rv)
    assert refused_rework["unit_key_hash"] == r1["unit_key_hash"] != w1["unit_key_hash"]
    assert (r1["link_kind"], r1["prev_inv_id"]) == ("review_of", w1["inv_id"])       # _links on the same records
    _sql(env["db"], "UPDATE ai_role_invocations SET prev_inv_id = ? WHERE inv_id = ?",
         (refused_rework["inv_id"], r1["inv_id"]))
    stored = [r for r in store.for_node(FA, "removed", 50) if r["inv_id"] == r1["inv_id"]][0]
    assert stored["prev_inv_id"] == refused_rework["inv_id"]
    assert store.latest(r1["unit_key_hash"], r1["create_seq"], view.REWORK_PURPOSES, launched_only=True) is None

    keys = []
    monkeypatch.setattr(assignment_store, "read", lambda *key: keys.append(key) or {"author": "ai"})
    assert view.item(stored)["link"] == {"kind": "review_of", "prev_inv_id": w1["inv_id"],
                                         "recorded_prev_inv_id": refused_rework["inv_id"]}
    assert keys == [("old-attempt", "result_review", 1)]                              # the review's own assignment
    [row] = _sql(env["db"], "SELECT prev_inv_id, result FROM ai_role_invocations WHERE inv_id = ?", (r1["inv_id"],))
    assert row["prev_inv_id"] == refused_rework["inv_id"]                             # never rewritten

    # the writer is no AI: _links names no writer, so the stored value is shown as before
    monkeypatch.setattr(assignment_store, "read", lambda *key: {"author": "person"})
    assert view.item(stored)["link"]["prev_inv_id"] == refused_rework["inv_id"]
    # a launched rework of the unit precedes the review: it is the previous call, as _links picks it first
    monkeypatch.setattr(assignment_store, "read", lambda *key: {"author": "ai"})
    rw = run("rw", origin("result_rework", "result_review", 1))
    rw1 = launch(rw)
    inv.finalize(rw)
    later = run("rv2", o)
    r2 = launch(later)
    inv.finalize(later)
    assert (r2["link_kind"], r2["prev_inv_id"]) == ("review_of", rw1["inv_id"])
    _sql(env["db"], "UPDATE ai_role_invocations SET prev_inv_id = ? WHERE inv_id = ?",
         (refused_rework["inv_id"], r2["inv_id"]))
    stored2 = [r for r in store.for_node(FA, "removed", 50) if r["inv_id"] == r2["inv_id"]][0]
    assert view.item(stored2)["link"] == {"kind": "review_of", "prev_inv_id": rw1["inv_id"],
                                          "recorded_prev_inv_id": refused_rework["inv_id"]}
