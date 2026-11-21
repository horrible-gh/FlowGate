"""Independently selectable scenarios; shared fixtures remain in test_fg_command_rerequest_0124.py."""
from __future__ import annotations

from test_fg_command_rerequest_0124 import (
    A,
    B,
    EXCLUDED,
    FA,
    LIMIT,
    MEMO,
    REVIEW_RUN,
    SECOND_LOOP,
    SPENT,
    THIRD_LOOP,
    X,
    _b1_after,
    _card_spent,
    _chain,
    _counts,
    _fan,
    _first_hops,
    _h,
    _hold,
    _loop_end,
    _loop_row,
    _loops,
    _mark,
    _passes,
    _read,
    _reason,
    _refuse_starts,
    _refused,
    _rerequested_at,
    _result,
    _review_status,
    _reviewed_ai_result,
    _run,
    _run_ended,
    _run_row,
    _spent,
    _sql,
    _stage_assigned,
    _start_ai,
    _wire,
    assignments,
    b1,
    command,
    db_path,
    decided,
    edge,
    env,
    events,
    fork_join,
    json,
    ledger,
    memo_routes,
    node,
    node_run,
    one,
    put_draft,
    pytest,
    record_starts,
    roles,
    starts,
    stored,
    threading,
    traceback,
    wired,
    write_line,
)

def test_two_commands_in_a_row_open_seq_2_once(env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, _result, _first = _spent(env, monkeypatch, starts)
    command(env, "step")
    again = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": "full"}, headers=_h())
    assert again.status_code in (200, 201, 409), again.text                    # 409 run_already_active while it works
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2]
    assert len([l for l in ledger(env) if l["assignment_seq"] == 2]) == 1
def test_a_lost_compare_and_set_requests_nothing(env, decided, monkeypatch, starts, fork_join):
    """Another writer opened seq 2 between the judgement and the insert: insert-if-absent loses, nothing requested."""
    from flowgate.card_settings import assigned_review
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)

    from modules.flow_gate.db import documents as db_documents

    def other_writer():
        return sa.restart_review_stage(FA, first, sa.mark(db_documents.get_by_id(result)))

    with_flow_guard(FA, other_writer)
    monkeypatch.setattr(sa, "current_review_assignment", lambda attempt_id, stage: dict(first))   # a stale read
    requests = len(ledger(env))

    def body():
        ctx = run_apply.load_context({**_run(), "status": "running"}, "flowgate", None, [])
        row = next(r for r in run_store.node_runs(ctx.run_id) if r["node_key"] == a)
        return assigned_review.rerequest_review_locked(ctx, row), ctx.jobs

    opened, jobs = with_flow_guard(FA, body)
    assert opened is False and jobs == []
    assert len(ledger(env)) == requests and one(env, a, "result_review", 2)["intent_kind"] is None
def test_a_stop_during_the_re_requested_review_keeps_its_seq_and_the_next_command_goes_on(
        env, decided, monkeypatch, starts, fork_join):
    """§6.11 with the Stop contract as it is (0061 T#7, not changed here): the Stop cancels seq 2's request, puts the
    card back to pending and invalidates its attempt; the next command opens the card again as a new attempt. The
    stopped attempt's seq 1..2 stay as its history."""
    a, _a2, _a3, _result, first = _spent(env, monkeypatch, starts)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    res = env["client"].post(f"/api/v1/runs/{_run()['run_id']}/stop", json={}, headers=_h())
    assert res.status_code == 200, res.text
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "stopped")
    [req] = [l for l in ledger(env) if l["exec_id"] == second["intent_exec_id"]]
    assert req["status"] == "cancelled"
    assert node_run(env, a)["status"] == "pending"
    kept = sorted((r["seq"], r["stage_state"]) for r in assignments(env, a)
                  if r["attempt_id"] == first["attempt_id"] and r["stage"] == "result_review")
    assert [s for s, _st in kept] == [1, 2]                                    # the stopped seq is history
    command(env, "step")                                                       # the next command goes on
    assert node_run(env, a)["status"] == "open"
    assert sorted((r["seq"], r["stage_state"]) for r in assignments(env, a)
                  if r["attempt_id"] == first["attempt_id"] and r["stage"] == "result_review") == kept
    assert len({r["attempt_id"] for r in assignments(env, a)}) == 2          # a new attempt of the same card
def test_the_command_takes_no_stop_gate_inside_its_guard_and_a_stop_after_it_does_not_deadlock(
        env, decided, monkeypatch, starts, fork_join):
    from flowgate.card_settings import assigned_review
    from flowgate.graph_run import fencing
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.graph_workflow.services import run_service
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    a, _a2, _a3, _result, _first = _spent(env, monkeypatch, starts)
    monkeypatch.setattr(assigned_review, "hold_of", lambda *a_, **k: (_ for _ in ()).throw(
        AssertionError("a node hold inside the command's guard")))
    inside, release, gate_taken = threading.Event(), threading.Event(), threading.Event()
    real = assigned_review.ensure_intent_locked
    errors, seen = [], []

    def ensure(*args, **kwargs):
        inside.set()
        release.wait(20)
        return real(*args, **kwargs)

    monkeypatch.setattr(assigned_review, "ensure_intent_locked", ensure)
    group = db_groups.get_by_id(FA)

    def run_command():
        try:
            run_service.start_run(group, "u_kim", 1, "step")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def stop_order():
        try:
            with fencing.exclusive(FA):                                        # a Stop: the gate first ...
                gate_taken.set()
                with_flow_guard(FA, lambda: seen.append(one(env, a, "result_review", 2) is not None))  # ... the guard
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    commander = threading.Thread(target=run_command)
    commander.start()
    assert inside.wait(20)                                                     # the command is inside its guard
    stopper = threading.Thread(target=stop_order)
    stopper.start()
    assert gate_taken.wait(5)                                                  # it holds no gate there
    release.set()
    commander.join(30)
    stopper.join(30)
    assert not commander.is_alive() and not stopper.is_alive()                 # no deadlock
    assert errors == [] and seen == [True]                                     # the Stop's guard after the commit
    assert one(env, a, "result_review", 2) is not None
