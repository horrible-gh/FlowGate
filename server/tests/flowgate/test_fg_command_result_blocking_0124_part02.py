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

def test_s3_a_re_requested_review_spent_again_is_listed_and_the_next_command_is_another_command(
        env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, result, _first = _spent(env, monkeypatch, starts)
    first_command = command(env, "full")
    second = one(env, a, "result_review", 2)
    _loop_row(result, SECOND_LOOP, "rework")
    _loop_end(env, second["intent_exec_id"], result, SECOND_LOOP, "retry_exhausted")
    _read(env)
    r = _latest(env)
    print("S3 state =", r["state"], "nodes =", [(n["node_key"], n["outcome"], n["reason"]) for n in r["nodes"]])  # measured
    # The existing failure rule counts the re-requested loop's B-1 (``auto_approval_skipped``) as this command's failure,
    # so the command reads ``failed`` (the 1st window, D12) while ``blocking`` still names the card; the earlier implementation keeps that rule (D7).
    assert (r["idle_reason"], r["state"]) == ("needs_intervention", "failed")
    assert [n["node_key"] for n in r["nodes"]] == [a]
    [b] = r["blocking"]
    assert (b["node_key"], b["reason"], b["release"]) == (a, "review_not_passed:retry_exhausted", None)
    command(env, "step")
    r2 = _latest(env)
    assert r2["command_seq"] != r["command_seq"] and r2["end_seq"] != r["end_seq"]
    assert first_command["run_id"] == _run()["run_id"]
def test_s4_only_the_latest_command_lists_blocking_cards(env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED["review_stalled"](env, a, result, first)
    command(env, "step")
    c1 = _latest(env)
    assert len(c1["blocking"]) == 1
    seen = {k: c1[k] for k in ("command_seq", "end_seq", "state", "nodes", "counts")}
    command(env, "step")
    c2 = _latest(env)
    assert len(c2["blocking"]) == 1 and c2["command_seq"] != c1["command_seq"]
    old = read_result(env, _run()["run_id"], c1["command_seq"])
    assert old["latest"] is False and old["blocking"] == []
    assert {k: old[k] for k in seen} == seen                                                   # nothing of now is mixed in
@pytest.mark.parametrize("excluded", ["review_verdict_hold", "completion_missing"])
def test_s5_a_manual_write_wait_beside_an_intervention_card_blocks_nothing_and_keeps_nodes_and_counts(
        env, decided, monkeypatch, starts, fork_join, excluded):
    """S5 on the real run path (the Flow R -> a, R -> m): the result of the first full command, read after
    ``a`` was excluded. ``review_verdict_hold`` records a B-1 mark after the command (the existing rule counts it as the
    command's failure, D7); ``completion_missing`` leaves no such mark, so ``a`` is a waiting card of the command."""
    from flowgate.graph_run import legacy_adapter as engine

    a, m, _result = _mixed_spent(env, monkeypatch, starts, excluded)
    intervention = _wire(env, a)["intervention"]
    assert intervention["kind"] == "needs_intervention" and intervention["reason"]
    if excluded == "review_verdict_hold":
        assert intervention["reason"] == "review_not_passed:review_verdict_hold"
    assert _wire(env, m)["intervention"] is None                                   # m is no intervention card
    before = _rows(env)

    r = _latest(env)
    print("S5", excluded, "first full command: state =", r["state"], "idle =", r["idle_reason"], "continues =", r["continues"],
          "counts =", r["counts"], "(nodes, blocking) =", _shape(r, a, m))         # measured (see the TR)
    assert r["idle_reason"] == "needs_intervention" and r["ended"] is True and r["latest"] is True
    # m: the manual write wait stays what it was (the old meaning of nodes / counts)
    by = {n["node_key"]: n for n in r["nodes"]}
    assert (by[m]["outcome"], by[m]["reason"]) == ("waiting", "manual_write_wait")
    # a: the card's own words and its release; blocking lists a alone (m has no intervention)
    [b] = r["blocking"]
    assert (b["node_key"], b["doc_type"], b["kind"]) == (a, "T", "needs_intervention")
    assert b["reason"] == intervention["reason"] and b["release"] is None and b["title"]
    assert [x["node_key"] for x in r["blocking"]] == [a]
    # the counts are those of the nodes (the old rule) ...
    tally = {"done": 0, "failed": 0, "waiting": 0}
    for n in r["nodes"]:
        tally[n["outcome"]] += 1
    assert r["counts"] == tally and r["counts"]["waiting"] >= 1
    if excluded == "review_verdict_hold":
        assert r["state"] == "waiting" and r["counts"]["failed"] == 0
        assert by[a]["reason"] == "review_hold"
        assert by[a]["detail"] == "review_not_passed:review_verdict_hold"
        assert r["failure"] is None
    # ... and nothing but ``blocking`` differs from the result before the earlier implementation (the same read with the intervention read failing)
    with monkeypatch.context() as patched:
        def broken(*_a, **_k):
            raise RuntimeError("a read failed")

        patched.setattr(engine, "intervention", broken)
        old = _latest(env)
    assert old["blocking"] == []
    assert {k: v for k, v in old.items() if k != "blocking"} == {k: v for k, v in r.items() if k != "blocking"}
    assert _rows(env) == before                                                    # the reads wrote nothing
