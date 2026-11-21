"""Independently selectable scenarios; shared fixtures remain in test_fg_command_result_blocking_0124.py."""
from __future__ import annotations

from test_fg_command_result_blocking_0124 import (
    A,
    B,
    EXCLUDED,
    FA,
    PERSON_WAITS,
    RELEASE,
    REVIEW_RUN,
    SECOND_LOOP,
    X,
    _h,
    _hold,
    _latest,
    _loop_end,
    _loop_row,
    _mixed,
    _mixed_spent,
    _read,
    _reviewed_ai_result,
    _rows,
    _run,
    _shape,
    _spent,
    _sql,
    _wire,
    assignments,
    command,
    db_path,
    decided,
    edge,
    env,
    events,
    fork_join,
    ledger,
    memo_routes,
    node,
    node_run,
    one,
    put_draft,
    pytest,
    read_result,
    record_starts,
    roles,
    starts,
    stored,
    wired,
    write_line,
)

def test_s5_the_next_no_op_commands_block_the_same_card_alone_and_the_first_command_keeps_none(
        env, decided, monkeypatch, starts, fork_join):
    a, m, _result = _mixed_spent(env, monkeypatch, starts)
    first = _latest(env)
    for mode in ("step", "full"):
        command(env, mode)
        r = _latest(env)
        print("S5 no-op", mode, ": state =", r["state"], "counts =", r["counts"], "(nodes, blocking) =", _shape(r, a, m))   # measured
        assert r["command_seq"] != first["command_seq"] and r["idle_reason"] == "needs_intervention"
        assert [b["node_key"] for b in r["blocking"]] == [a]                       # a alone, never m
        assert r["blocking"][0]["release"] == "memo_rereview"
    old = read_result(env, _run()["run_id"], first["command_seq"])
    assert old["latest"] is False and old["blocking"] == []
    assert {k: old[k] for k in ("end_seq", "state", "nodes", "counts")} == \
        {k: first[k] for k in ("end_seq", "state", "nodes", "counts")}
def test_s6_a_gate_waiting_beside_an_intervention_card_blocks_only_the_card(
        env, decided, monkeypatch, starts, fork_join):
    """T#2 §5.1 S6 with both in one real run: R -> a (an AI card spent through its review and excluded) and R -> g (a
    gate waiting for a person's confirmation). The gate is no blocking card; the card is."""
    from modules.flow_gate.graph_workflow.domain import policies

    monkeypatch.setitem(policies._registered, "gate_node", object())                # confirming a gate revision needs it
    body = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T"), node("tmp_g", None, node_type="gate")],
                     [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_r", "tmp_g")])
    a, g = (body["key_map"][ref] for ref in ("tmp_a", "tmp_g"))
    stored(a, "instruction", "omit")
    stored(a, "review_count", 1)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    roles(a, execution=X, review=B)
    command(env, "full")
    assert node_run(env, g)["status"] == "open"                                   # the gate waits for a person
    _key, result, rr = _reviewed_ai_result(env, monkeypatch, starts, a)
    _loop_row(result, REVIEW_RUN, "rework")
    _loop_end(env, rr["intent_exec_id"], result, REVIEW_RUN, "retry_exhausted")
    _read(env)
    EXCLUDED["review_stalled"](env, a, result, one(env, a, "result_review", 1))
    assert _wire(env, a)["intervention"]["kind"] == "needs_intervention"
    assert _wire(env, g)["intervention"] is None

    r = _latest(env)
    print("S6 gate+card run", _run()["run_id"], ": state =", r["state"], "idle =", r["idle_reason"], "counts =",
          r["counts"], "nodes =", [(n["node_key"] == g and "g" or "a", n["outcome"], n["reason"]) for n in r["nodes"]],
          "blocking =", [(b["node_key"] == g and "g" or "a", b["reason"], b["release"]) for b in r["blocking"]])   # measured
    assert r["idle_reason"] in PERSON_WAITS and r["ended"] is True and r["latest"] is True
    assert [b["node_key"] for b in r["blocking"]] == [a]                          # the card alone; never the gate
    assert r["blocking"][0]["reason"] == "review_not_passed:review_stalled"
    command(env, "step")                                                           # a no-op command: the same
    r2 = _latest(env)
    print("S6 gate+card no-op: idle =", r2["idle_reason"], "nodes =", r2["nodes"], "blocking =",
          [(b["node_key"] == g and "g" or "a", b["release"]) for b in r2["blocking"]])   # measured
    assert r2["command_seq"] != r["command_seq"] and [b["node_key"] for b in r2["blocking"]] == [a]
@pytest.mark.parametrize("reason,listed", [("waiting_confirm", True), ("waiting_approval", True), ("needs_intervention", True),
                                           ("step_done", False), ("completed", False), ("error", False)])
def test_s6_blocking_only_for_the_idle_reasons_of_a_person_s_wait(env, decided, monkeypatch, starts, fork_join, reason, listed):
    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED["review_stalled"](env, a, result, first)
    command(env, "step")                                                                       # the copy: S1's run, its idle reason told
    _sql(env["db"], "UPDATE graph_runs SET idle_reason = ? WHERE group_id = ?", (reason, FA))
    r = _latest(env)
    assert r["idle_reason"] == reason
    assert bool(r["blocking"]) is listed, r["blocking"]
