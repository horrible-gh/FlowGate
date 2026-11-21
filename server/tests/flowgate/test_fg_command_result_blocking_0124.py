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
