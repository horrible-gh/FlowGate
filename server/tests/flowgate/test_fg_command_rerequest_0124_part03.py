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

def test_a_re_requested_review_spent_again_waits_again_and_only_the_next_command_opens_seq_3(
        env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, result, _first = _spent(env, monkeypatch, starts)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    _loop_row(result, SECOND_LOOP, "rework")
    _loop_end(env, second["intent_exec_id"], result, SECOND_LOOP, "retry_exhausted")
    assert one(env, a, "result_review", 2)["verdict"] == "not_passed:retry_exhausted"
    loops = len(_loops(starts))
    for _ in range(3):
        _read(env)                                                             # reads retry nothing
    assert one(env, a, "result_review", 3) is None and len(_loops(starts)) == loops
    assert _wire(env, a)["intervention"]["kind"] == "needs_intervention"
    assert _run_row(env)["idle_reason"] == "needs_intervention"
    command(env, "step")
    third = one(env, a, "result_review", 3)
    assert (third["provider_id"], third["rework_provider_id"], third["intent_kind"]) == (B, X, "execution")
    assert len(_loops(starts)) == loops + 1
def test_a_refused_start_of_the_re_requested_review_fails_seq_2_with_its_code_and_asks_no_other_ai(
        env, decided, monkeypatch, starts, fork_join):
    from modules.flow_gate.services import ai_invoke_service

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    tried = []

    class Refused(RuntimeError):
        code = "ai_disabled"                                                   # an admission refusal's own code

    def refused(**kwargs):
        tried.append((kwargs.get("graph_exec_id"), (kwargs.get("document_review_loop") or {}).get(
            "reviewer_provider_id")))
        raise Refused("the reviewer is disabled")

    monkeypatch.setattr(ai_invoke_service, "start_run", refused)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    assert (second["stage_state"], second["fail_cause"]) == ("failed", "start_refused:ai_disabled")
    assert tried == [(second["intent_exec_id"], B)]                            # once, the assigned reviewer only
    [req] = [l for l in ledger(env) if l["exec_id"] == second["intent_exec_id"]]
    assert req["status"] != "requested"                                        # the refused start ended its request
    assert one(env, a, "result_review", 1)["verdict"] == first["verdict"]
    assert ("review_not_started" in [r for _d, r, _m in b1(env)])
    assert _run()["step_node_keys"] == [a]
def test_a_refused_start_after_a_re_request_of_the_same_submission_is_retried_once_its_provider_is_back(
        env, decided, monkeypatch, starts, fork_join):
    """total_timeout of seq 1 (the submission mark unchanged), the command's seq 2 refused once: a system error of the
    new cycle — the review_not_passed record the command answered no longer stops its retry. The next run read opens
    seq 3 (the existing 0119 retry) with the reviewer back; its pass completes the card."""
    from flowgate.card_settings import assigned_review
    from modules.flow_gate.services import ai_invoke_service

    a, a2, _a3, result, first = _spent(env, monkeypatch, starts, stop="total_timeout")
    tried, recorder = _refuse_starts(monkeypatch)
    command(env, "step")
    second = one(env, a, "result_review", 2)
    assert (second["stage_state"], second["fail_cause"]) == ("failed", "start_refused:ai_disabled")
    floor = _rerequested_at(env, a)
    assert _b1_after(env, floor) == [("review_not_started", _mark(first))]           # the same submission's mark
    assert [r for r, _p in events(env, "auto_approval_skipped") if r == "review_not_passed:total_timeout"]  # kept
    assert assigned_review.command_cycle(second["run_id"], a, second["attempt_id"], "result") == (floor, 2)
    assert assigned_review.review_retry_due(second) is True                   # the probe the review ran: False before

    monkeypatch.setattr(ai_invoke_service, "start_run", recorder)             # the provider is back
    loops = len(_loops(starts))
    _read(env)

    third = one(env, a, "result_review", 3)
    assert third is not None
    assert (third["actor"], third["provider_id"], third["rework_provider_id"], third["stage_state"],
            third["intent_kind"]) == ("ai", B, X, "active", "execution")
    assert third["basis_doc_id"] == result
    [loop] = _loops(starts)[loops:]
    assert loop["graph_exec_id"] == third["intent_exec_id"] and loop["doc_ref"] == result
    assert len(tried) == 1                                                      # refused once, retried once
    assert one(env, a, "result_review", 1) == first                            # seq 1 / 2 kept as history
    assert (one(env, a, "result_review", 2)["stage_state"], one(env, a, "result_review", 2)["fail_cause"]) == \
        ("failed", "start_refused:ai_disabled")
    _passes(env, monkeypatch, a, result, THIRD_LOOP)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert node_run(env, a2)["status"] == "pending"                           # the step's next card: next command
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2, 3]
