"""flowgate.v02.0124 T#2 (T §5.1 S6, rework of rej_01M6G7JDA4A8RN63) — a gate that waits for a person's confirmation is no
blocking card of the command result.

The flow of ``test_fg_run_buttons_0053`` on its isolated test DB (the cards of 0053: pre-feature attempts, a person card
finished with its result registered, then the 확인 card opens and the run idles ``waiting_confirm``). The command result of
that real run is read over ``GET /api/v1/runs/{run}/command-result``: the idle reason is one of the person's waits
(``waiting_confirm``), the command is the latest, ended and not cut — so the only thing that keeps ``blocking`` empty is
that the gate has no intervention (T §2 D2). The gate and an intervention card together in one run are in
``test_fg_command_result_blocking_0124::test_s6_a_gate_waiting_beside_an_intervention_card_blocks_only_the_card``.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    TEST_VERSION, _h, db_path, decided, env, legacy_cards, pre_feature_attempts,
)
from test_fg_run_buttons_0053 import LEE, _all, _confirmed, _current, _finish, _GatePolicy, _next, _post

pytestmark = pytest.mark.usefixtures("legacy_cards")                      # as test_fg_run_buttons_0053
PERSON_WAITS = ("waiting_confirm", "needs_intervention", "waiting_approval")
GATE_RULES = "blocking_gate_0124"


@pytest.fixture()
def gate_rules(monkeypatch):
    """``test_fg_run_buttons_0053``'s ``gate_decided`` (decided test rules plus a gate rule, confirming a gate revision
    needs a validation slot) under this suite's own rule name. The policy registry has no unregister and
    ``gate_decided`` registers its name once per process, so more than one test needs an idempotent registration."""
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    monkeypatch.setitem(policies._registered, "gate_node", object())
    gate, old = _GatePolicy(), {}
    for key in policies.POLICY_KEYS:
        try:
            run_policies.register(key, GATE_RULES, TEST_VERSION, gate)
        except ValueError:                                              # registered by an earlier test: the same rules
            pass
        old[key] = policies.set_system_default(key, GATE_RULES, TEST_VERSION)
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


def _result(env, run_id, seq=None):
    query = f"?command_seq={seq}" if seq is not None else ""
    res = env["client"].get(f"/api/v1/runs/{run_id}/command-result{query}", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["command_result"]


def test_s6_the_gate_of_a_real_confirm_wait_is_no_blocking_card(pre_feature_attempts, env, gate_rules):
    (t1, gate), _body = _confirmed(env, [("T", LEE), ("gate", None)])
    run = _all(env)                                                    # 전체 실행: t1 opens, a person's card waits
    _finish(env, t1, 90)                                               # t1 done -> the gate opens and waits
    waiting = _current(env)
    assert (waiting["status"], waiting["idle_reason"]) == ("idle", "waiting_confirm")
    assert {n["node_key"]: n["status"] for n in waiting["node_runs"]}[gate] == "open"

    r = _result(env, run["run_id"])
    print("S6 gate run", run["run_id"], ": state =", r["state"], "idle =", r["idle_reason"], "ended =", r["ended"],
          "latest =", r["latest"], "continues =", r["continues"], "counts =", r["counts"],
          "nodes =", [(n["outcome"], n["reason"]) for n in r["nodes"]], "blocking =", r["blocking"])   # measured
    # every condition of the blocking rule holds except a card with an intervention: the gate has none
    assert (r["latest"], r["ended"]) == (True, True) and r["idle_reason"] in PERSON_WAITS
    assert r["idle_reason"] == "waiting_confirm"
    assert r["blocking"] == []
    for _ in range(2):                                                 # a re-read says the same
        assert _result(env, run["run_id"])["blocking"] == []

    # the gate decided: the run completes, nothing blocks
    decided_run = _post(env, f"/runs/{run['run_id']}/nodes/{gate}/gate-decision",
                        body={"decision": "confirm", "comment": "확인"})["run"]
    assert {n["node_key"]: n["status"] for n in decided_run["node_runs"]}[gate] == "done"
    after = _result(env, run["run_id"])
    print("S6 gate run", run["run_id"], "after the confirm: state =", after["state"], "idle =", after["idle_reason"],
          "blocking =", after["blocking"])                             # measured
    assert after["blocking"] == []


def test_s6_a_gate_after_a_step_command_is_no_blocking_card_either(pre_feature_attempts, env, gate_rules):
    (t1, gate), _body = _confirmed(env, [("T", LEE), ("gate", None)])
    run = _next(env)                                                   # 다음 단계: t1 only
    _finish(env, t1, 90)
    assert _current(env)["idle_reason"] == "step_done"
    again = _next(env)                                                 # the next command opens the gate
    assert again["run_id"] == run["run_id"]
    waiting = _current(env)
    assert (waiting["status"], waiting["idle_reason"]) == ("idle", "waiting_confirm")
    r = _result(env, run["run_id"])
    print("S6 gate step run", run["run_id"], ": state =", r["state"], "idle =", r["idle_reason"], "ended =", r["ended"],
          "counts =", r["counts"], "nodes =", [(n["outcome"], n["reason"]) for n in r["nodes"]],
          "blocking =", r["blocking"])                                 # measured
    assert (r["latest"], r["ended"], r["idle_reason"]) == (True, True, "waiting_confirm")
    assert r["blocking"] == []
