"""flowgate.v02.0124 T#2 (0003-NR §5.3, §6.7~9 server side) — the command result tells which cards block a run that waits
for a person's intervention: ``command_result.blocking`` (``node_key``, ``title``, ``doc_type``, ``kind``, ``reason`` as the
card's intervention tells it, ``release`` judged from the stage assignment rows).

The harness of ``test_fg_command_rerequest_0124`` (real SQLite DB, routes and migrations; every AI start is a recorder, no AI
is called). An isolated test Flow only: R -> a -> a2 -> a3, ``a`` an AI card spent through its review budget.

- S1: a step / full command over an excluded card is a no-op that ends waiting: ``nodes=[]`` and ``counts`` as before,
  ``blocking`` the card with its own reason and the ``release`` of its author / reason;
- S2: a Hold card is no intervention of the card states the set names: what the run reads is recorded and kept consistent;
- S3: a re-requested review spent again: the command's result lists the card (an AI card the next command requests again:
  no release), and the next command is another ``command_seq``;
- S4: only the latest command has ``blocking``; an older command keeps its own result;
- S5: a Flow mixing a manual write wait (``m``) and an intervention card (``a``) side by side: ``blocking`` is ``a``
  alone, ``nodes`` / ``counts`` are what they were (``m`` keeps its manual write wait);
- S6: ``blocking`` only for the idle reasons of a person's wait (here a copy of S1's run with its idle reason changed; the
  real gate confirm wait of 0053 is ``test_fg_command_result_blocking_gate_0124``; a gate waiting beside an intervention
  card in one real run is ``test_s6_a_gate_waiting_beside_an_intervention_card_blocks_only_the_card``);
- S7: a refused re-request start is the command's failure with the real code and detail;
- S8: reading changes nothing; S9: a failed read leaves the card out / its release unsaid and the read goes on.
"""
from __future__ import annotations

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, A, X, _sql, assignments, command, db_path, decided, env, events, ledger, node_run, one, record_starts, roles,
    starts, stored, wired, write_line,
)
from test_fg_card_intervention_assigned_0119 import (  # noqa: F401 — fixtures used by name
    _loop_end, _loop_row, _read, _reviewed_ai_result, _wire, fork_join, memo_routes,
)
from test_fg_command_rerequest_0124 import EXCLUDED, SECOND_LOOP, _hold, _run, _spent
from test_fg_card_settings import _h, edge, node, put_draft
from test_fg_card_settings_review import REVIEW_RUN
from test_fg_command_result_0113 import result as read_result

PERSON_WAITS = ("waiting_confirm", "needs_intervention", "waiting_approval")
RELEASE = {
    "review_verdict_hold": "memo_rereview", "review_stalled": "memo_rereview", "run_not_clean": "memo_rereview",
    "unknown_reason": "memo_rereview", "completion_missing": "memo_rereview", "legacy_attempt": "memo_rereview",
    "issuer_lacks_approve": "issuer_permission", "person_author": "new_submission",
}


def _latest(env):
    return read_result(env, _run()["run_id"])


def _rows(env):
    return tuple(_sql(env["db"], f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] for table in (
        "graph_run_events", "graph_stage_assignments", "graph_node_runs", "documents"))


@pytest.mark.parametrize("case", sorted(RELEASE))
def test_s1_a_no_op_command_lists_the_blocking_card_with_its_reason_and_release(
        env, decided, monkeypatch, starts, fork_join, case):
    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED[case](env, a, result, first)
    if case in {"review_verdict_hold", "run_not_clean", "completion_missing", "person_author"}:
        # 0126: these cases now have a real execution target and an outstanding review request.
        command(env,"step")
        r=_latest(env)
        assert r["state"]=="running" and [n["node_key"] for n in r["nodes"]]==[a]
        assert r["blocking"]==[] and one(env,a,"result_review",2) is not None
        return
    for mode in ("step", "full"):
        command(env, mode)
        r = _latest(env)
        assert (r["state"], r["continues"], r["ended"]) == ("waiting", True, True), (case, mode, r["idle_reason"])
        assert r["idle_reason"] == "needs_intervention", (case, mode)
        assert r["nodes"] == [] and r["counts"] == {"done": 0, "failed": 0, "waiting": 0}      # the old meaning stays
        [b] = r["blocking"]
        assert (b["node_key"], b["doc_type"], b["kind"]) == (a, "T", "needs_intervention")
        assert b["title"] and b["reason"] == _wire(env, a)["intervention"]["reason"]            # the card's own words
        assert b["release"] == RELEASE[case], (case, mode, b)


def test_s2_a_hold_card_is_listed_only_when_the_run_reads_a_person_s_wait(env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, _result, _first = _spent(env, monkeypatch, starts)
    _hold(env, a)
    command(env, "step")
    r = _latest(env)
    print("S2 idle_reason =", r["idle_reason"], "blocking =", r["blocking"])                   # measured (see the TR)
    if r["idle_reason"] in PERSON_WAITS:
        assert [x["node_key"] for x in r["blocking"]] in ([], [a])
    else:
        assert r["blocking"] == []


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
    # so the command reads ``failed`` (the 1st window, D12) while ``blocking`` still names the card; T#2 keeps that rule (D7).
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


def _mixed(env):
    """R -> a and R -> m side by side. a: an AI card (X writes, B reviews, X reworks, count 1); m: a person's report, no
    AI role (the person writes it). -> (a, m)."""
    body = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T"), node("tmp_m", "T")],
                     [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_r", "tmp_m")])
    a, m = (body["key_map"][ref] for ref in ("tmp_a", "tmp_m"))
    for key in (a, m):
        stored(key, "instruction", "omit")
    stored(a, "review_count", 1)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    roles(a, execution=X, review=B)
    roles(m, execution="not_used", review="not_used")
    return a, m


def _mixed_spent(env, monkeypatch, starts, reason="review_stalled"):
    """The first full command opens m (a manual write wait) and starts a's AI; a's review loop spends its budget and a
    gets the excluded ``reason``. -> (a, m, result)."""
    a, m = _mixed(env)
    command(env, "full")
    assert node_run(env, m)["status"] == "open"                                   # m waits for its person
    _key, result, rr = _reviewed_ai_result(env, monkeypatch, starts, a)
    _loop_row(result, REVIEW_RUN, "rework")
    _loop_end(env, rr["intent_exec_id"], result, REVIEW_RUN, "retry_exhausted")
    _read(env)
    EXCLUDED[reason](env, a, result, one(env, a, "result_review", 1))             # the exclusion reason is recorded
    return a, m, result


def _shape(r, a, m):
    """What a result says of the two cards, for the record: (card, outcome, reason) per node, (card, release) per blocking."""
    name = {a: "a", m: "m"}
    return ([(name[n["node_key"]], n["outcome"], n["reason"]) for n in r["nodes"]],
            [(name[b["node_key"]], b["reason"], b["release"]) for b in r["blocking"]])


@pytest.mark.parametrize("excluded", ["review_verdict_hold", "completion_missing"])
def test_s5_a_manual_write_wait_beside_an_intervention_card_blocks_nothing_and_keeps_nodes_and_counts(
        env, decided, monkeypatch, starts, fork_join, excluded):
    """T#2 §5.1 S5 on the real run path (the Flow R -> a, R -> m): the result of the first full command, read after
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
    # ... and nothing but ``blocking`` differs from the result before T#2 (the same read with the intervention read failing)
    with monkeypatch.context() as patched:
        def broken(*_a, **_k):
            raise RuntimeError("a read failed")

        patched.setattr(engine, "intervention", broken)
        old = _latest(env)
    assert old["blocking"] == []
    assert {k: v for k, v in old.items() if k != "blocking"} == {k: v for k, v in r.items() if k != "blocking"}
    assert _rows(env) == before                                                    # the reads wrote nothing


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


def test_s7_a_refused_re_request_start_is_the_command_s_failure_with_its_real_code(
        env, decided, monkeypatch, starts, fork_join):
    from modules.flow_gate.services import ai_invoke_service

    a, _a2, _a3, _result, _first = _spent(env, monkeypatch, starts)

    class Refused(RuntimeError):
        code = "ai_disabled"

    def refused(**kwargs):
        raise Refused("the reviewer is disabled")

    monkeypatch.setattr(ai_invoke_service, "start_run", refused)
    command(env, "step")
    r = _latest(env)
    print("S7 state =", r["state"], "failure =", r["failure"], "blocking =", r["blocking"])  # measured (see the TR)
    assert r["state"] == "failed"
    assert r["failure"]["node_key"] == a and r["failure"]["code"] == "stage_failed"
    assert r["failure"]["detail"] == "start_refused:ai_disabled"


def test_s8_reading_the_command_result_writes_nothing(env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED["review_stalled"](env, a, result, first)
    command(env, "step")
    before = _rows(env)
    for _ in range(3):
        assert len(_latest(env)["blocking"]) == 1
    assert _rows(env) == before


def test_s9_a_failed_read_leaves_the_card_out_or_its_release_unsaid_and_the_read_goes_on(
        env, decided, monkeypatch, starts, fork_join):
    from flowgate.card_settings import stage_assignment as sa
    from flowgate.graph_run import legacy_adapter as engine

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED["review_stalled"](env, a, result, first)
    command(env, "step")
    whole = _latest(env)
    assert whole["blocking"][0]["release"] == "memo_rereview"

    def broken(*_a, **_k):
        raise RuntimeError("a read failed")

    with monkeypatch.context() as m:
        m.setattr(sa, "current_review_assignment", broken)
        r = _latest(env)
        [b] = r["blocking"]
        assert b["node_key"] == a and b["release"] is None                                     # the release unsaid
        assert {k: r[k] for k in ("state", "nodes", "counts", "idle_reason")} == {k: whole[k] for k in ("state", "nodes", "counts", "idle_reason")}
    with monkeypatch.context() as m:
        m.setattr(engine, "intervention", broken)
        r = _latest(env)
        assert r["blocking"] == []                                                             # the card left out
        assert {k: r[k] for k in ("state", "nodes", "counts", "idle_reason")} == {k: whole[k] for k in ("state", "nodes", "counts", "idle_reason")}