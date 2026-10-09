"""flowgate.v02.0045 T0017 (S6) — gate decision, unattended run and editing during a run.

P0008 S6-1 (``POST /runs/{run_id}/nodes/{node_key}/gate-decision``) and §8.3 ``gate_confirmed`` /
``gate_rejected``; L0009 §2.6.3 (a gate decision needs ``gate_node`` on top of the revision's
policies), §2.6.4 / §3.2 (open -> done / rejected, propagation record), §2.6.6 (a gate hands out no
token), §2.6.7 (a stopped run takes no gate decision). D0004 §8 Q-11 is unanswered, so the product
refuses every gate decision with ``run_policy_undecided``; the decided paths use a *test* gate
policy. Q-7 (editing during a run) and Q-9 (unattended run) are unanswered too: a run keeps the
revision it started with and an AI node is started once with no continuation target.

Same harness as ``test_graph_run_api_0045`` (real SQLite with every migration, TestClient, the real
token service; auth / RBAC / SSE delivery stubbed).
"""
from __future__ import annotations

import pytest

from test_graph_run_api_0045 import (  # noqa: F401 — fixtures are used by name
    FG, TEST_VERSION, _TestPolicy, _confirm, _confirm_next, _doc, _edge, _h, _node, _sql, control, count,
    current, db_path, decide_review, env, nodes_of, run_events, start, submit, token_of,
)

FT = "flowgate.v02.0075"      # Graph: R(approved, bound) -> G(gate) -> D(u_lee)
GATE_VALUE = "s6_gate_test"


class _GatePolicy(_TestPolicy):
    """The S5 test double plus a decided gate slot: confirm -> confirmed, reject -> rejected."""

    def judge(self, _node_key, decision, _comment):
        return "confirmed" if decision == "confirm" else "rejected"


_GATE = _GatePolicy()


def _gate(key, label="확인"):
    return {"node_key": key, "node_type": "gate", "doc_type": None, "label": label,
            "assignee_kind": None, "assignee_user_id": None, "assignee_provider_id": None}


@pytest.fixture()
def gate_flow(env, monkeypatch):
    """A confirmed revision with a gate node. Confirming it needs a non-undecided validation slot
    for ``gate_node`` (P0008 S3-5); the product slot stays ``undecided``."""
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.graph_workflow.domain import policies

    monkeypatch.setitem(policies._registered, "gate_node", object())
    db_groups.create({"group_id": FT, "project_id": "flowgate", "module": "v02", "title": FT, "status": "OPEN"})
    _doc(FT, 1, "R", "approved")
    _confirm(FT, [_node("gn_r", "R"), _gate("gn_g"), _node("gn_d", "D", "human", user="u_lee")],
             [_edge("ge_rg", "gn_r", "gn_g"), _edge("ge_gd", "gn_g", "gn_d")])
    env["events"].clear()
    return env


@pytest.fixture()
def gate_decided():
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    old = {}
    for key in policies.POLICY_KEYS:
        run_policies.register(key, GATE_VALUE, TEST_VERSION, _GATE)
        old[key] = policies.set_system_default(key, GATE_VALUE, TEST_VERSION)
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


def gate(env, run_id, node_key="gn_g", body=None, headers=None):
    return env["client"].post(f"/api/v1/runs/{run_id}/nodes/{node_key}/gate-decision",
                              json=body if body is not None else {"decision": "confirm", "comment": "검토 완료"},
                              headers=headers or _h())


def started(env):
    res = start(env, FT)
    assert res.status_code == 201, res.text
    return res.json()["run"]


# ── S6-1: Q-11 unanswered ────────────────────────────────────────────────────

def test_a_gate_revision_cannot_start_while_the_gate_rule_is_undecided(gate_flow, gate_decided):
    from modules.flow_gate.graph_workflow.domain import policies

    policies.set_system_default("gate_node", policies.UNDECIDED, policies.UNDECIDED_IMPL_VERSION)
    res = start(gate_flow, FT)
    assert res.status_code == 409 and res.json()["error"]["params"] == {
        "flow_id": FT, "revision_no": 1, "undecided_policies": [{"policy": "gate_node", "rule_ref": "Q-11"}]}
    assert count(gate_flow, "graph_runs") == 0


def test_a_gate_decision_is_refused_while_the_gate_rule_is_undecided(gate_flow, gate_decided):
    run = started(gate_flow)
    _sql(gate_flow["db"], "UPDATE graph_run_policy_entries SET policy_value = 'undecided', "
                          "impl_version = 'undecided.1' WHERE policy_key = 'gate_node'")
    seq = current(gate_flow, FT)["last_event_seq"]
    res = gate(gate_flow, run["run_id"])
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "run_policy_undecided"
    assert err["message"] == "confirm 카드의 실행 의미가 아직 정해지지 않았습니다."
    assert err["params"] == {"flow_id": FT, "revision_no": 1,
                             "undecided_policies": [{"policy": "gate_node", "rule_ref": "Q-11"}]}
    after = current(gate_flow, FT)
    assert after["last_event_seq"] == seq and nodes_of(after)["gn_g"]["status"] == "open"
    en = gate(gate_flow, run["run_id"], body={"decision": "confirm"}, headers={**_h(), "x-locale": "en"})
    assert en.json()["error"]["message"] == "What the confirm card does in a run is not decided yet."


# ── S6-1: decided gate (test policy) ─────────────────────────────────────────

def test_an_open_gate_hands_out_nothing(gate_flow, gate_decided):
    run = started(gate_flow)
    assert run["open_node_keys"] == ["gn_g"]
    g = nodes_of(run)["gn_g"]
    assert g["status"] == "open" and g["dispatch"] is None and g["result_doc_id"] is None
    assert count(gate_flow, "graph_run_token_map", "node_key = ?", ("gn_g",)) == 0
    opened = [e for e in gate_flow["events"] if e.payload.get("cause") == "opened"]
    assert [(e.payload["node_key"], e.payload["canvas_state"], e.payload["dispatch"]) for e in opened] == [
        ("gn_g", "in_progress", None)]


def test_a_confirmed_gate_completes_and_opens_the_next_node(gate_flow, gate_decided):
    run_id = started(gate_flow)["run_id"]
    res = gate(gate_flow, run_id)
    assert res.status_code == 200, res.text
    body = res.json()["run"]
    n = nodes_of(body)
    assert n["gn_g"]["status"] == "done" and n["gn_g"]["finished_at"] and n["gn_g"]["result_doc_id"] is None
    assert n["gn_d"]["status"] == "open" and n["gn_d"]["dispatch"] == {
        "token_issued": True, "mention_id": None, "issued_to": "u_lee"}
    assert body["open_node_keys"] == ["gn_d"] and body["status"] == "running" and body["last_event_seq"] == 5
    assert run_events(gate_flow)[3:] == [(4, "graph_node_state_changed", "gn_g", "gate_confirmed"),
                                         (5, "graph_node_state_changed", "gn_d", "opened")]
    confirmed = gate_flow["events"][3].payload
    assert (confirmed["from"], confirmed["to"], confirmed["canvas_state"], confirmed["dispatch"]) == (
        "open", "done", "done", None)
    assert "comment" not in confirmed and "actor_user_id" not in confirmed       # P0008 §8.3 payload
    row = _sql(gate_flow["db"], "SELECT actor_user_id, reason FROM graph_run_events WHERE event_seq = 4")[0]
    assert row == {"actor_user_id": "u_kim", "reason": "검토 완료"}
    prop = _sql(gate_flow["db"], "SELECT node_key, outcome, source_event_seq, status FROM graph_propagations")
    assert prop == [{"node_key": "gn_r", "outcome": "approved", "source_event_seq": 2, "status": "done"},
                    {"node_key": "gn_g", "outcome": "approved", "source_event_seq": 4, "status": "done"}]
    again = gate(gate_flow, run_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "node_not_open"
    assert again.json()["error"]["params"] == {"run_id": run_id, "node_key": "gn_g", "status": "done"}
    assert again.json()["error"]["message"] == "열린 게이트 노드가 아닙니다."
    d = _doc(FT, 2, "D")
    submit(FT, d, "D", token_of(gate_flow, "gn_d"))
    decide_review(FT, d, "approve", "approved")
    assert gate_flow["client"].get(f"/api/v1/runs/{run_id}", headers=_h()).json()["run"]["status"] == "completed"


def test_a_rejected_gate_follows_the_rejection_path(gate_flow, gate_decided):
    run_id = started(gate_flow)["run_id"]
    res = gate(gate_flow, run_id, body={"decision": "reject"})
    assert res.status_code == 200, res.text
    n = nodes_of(res.json()["run"])
    assert n["gn_g"]["status"] == "rejected" and n["gn_d"]["status"] == "pending"   # test path: wait
    assert run_events(gate_flow)[-1] == (4, "graph_node_state_changed", "gn_g", "gate_rejected")
    assert gate_flow["events"][-1].payload["canvas_state"] == "blocked"
    assert _sql(gate_flow["db"], "SELECT outcome, status FROM graph_propagations WHERE node_key = 'gn_g'") == [
        {"outcome": "rejected", "status": "done"}]
    assert _sql(gate_flow["db"], "SELECT reason FROM graph_run_events WHERE event_seq = 4") == [{"reason": None}]


def test_gate_decision_refusals_follow_their_order(gate_flow, gate_decided):
    run_id = started(gate_flow)["run_id"]
    missing = gate(gate_flow, "run_nope")
    assert missing.status_code == 404 and missing.json()["error"]["params"] == {"run_id": "run_nope"}
    doc_node = gate(gate_flow, run_id, "gn_d").json()["error"]
    assert doc_node["code"] == "node_not_gate" and doc_node["message"] == "게이트 노드가 아닙니다."
    assert doc_node["params"] == {"run_id": run_id, "node_key": "gn_d", "node_type": "document"}
    unknown = gate(gate_flow, run_id, "gn_zz").json()["error"]
    assert unknown["code"] == "node_not_gate" and unknown["params"]["node_type"] is None
    for body, field, reason in (({}, "decision", "required"),
                                ({"decision": "ok"}, "decision", "not_one_of:confirm,reject"),
                                ({"decision": "confirm", "comment": 3}, "comment", "not_a_string"),
                                ({"decision": "confirm", "comment": "x" * 501}, "comment", "too_long"),
                                ([], "body", "not_an_object")):
        bad = gate(gate_flow, run_id, body=body)
        assert bad.status_code == 422 and bad.json()["error"]["params"]["errors"] == [
            {"field": field, "reason": reason}], body
    assert control(gate_flow, run_id, "stop").status_code == 200
    stopped = gate(gate_flow, run_id)
    assert stopped.status_code == 409 and stopped.json()["error"]["code"] == "run_not_running"
    assert stopped.json()["error"]["params"] == {"run_id": run_id, "status": "stopped"}
    assert nodes_of(current(gate_flow, FT))["gn_g"]["status"] == "open"            # nothing deferred
    assert control(gate_flow, run_id, "resume").status_code == 200
    assert gate(gate_flow, run_id).status_code == 200
    assert gate(gate_flow, run_id, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_a_gate_decision_with_an_unrestorable_snapshot_writes_nothing(gate_flow, gate_decided):
    run_id = started(gate_flow)["run_id"]
    _sql(gate_flow["db"], "UPDATE graph_run_policy_entries SET impl_version = 'gone.9' WHERE policy_key = 'join'")
    seq = current(gate_flow, FT)["last_event_seq"]
    res = gate(gate_flow, run_id)
    assert res.status_code == 500 and res.json()["error"]["code"] == "internal_error"
    assert current(gate_flow, FT)["last_event_seq"] == seq
    assert nodes_of(current(gate_flow, FT))["gn_g"]["status"] == "open"


# ── Q-7 / Q-9 unanswered: the S5 behaviour stays ─────────────────────────────

def test_a_revision_confirmed_during_a_run_does_not_change_the_run(env, gate_decided):
    run_id = start(env).json()["run"]["run_id"]                          # revision 1: R -> D -> P
    _confirm_next(FG, [_node("gn_r", "R"), _node("gn_d", "D", "human", user="u_lee"), _node("gn_q", "T", "human", user="u_kim")],
                  [_edge("ge_rd", "gn_r", "gn_d"), _edge("ge_dq", "gn_d", "gn_q")])
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env, "gn_d"))
    decide_review(FG, d, "approve", "approved")
    run = current(env)
    assert run["run_id"] == run_id and run["revision_no"] == 1
    assert run["open_node_keys"] == ["gn_p"] and "gn_q" not in nodes_of(run)
    canvas = env["client"].get(f"/api/v1/flows/{FG}/canvas", headers=_h()).json()
    assert canvas["graph"]["latest_revision_no"] == 2 and canvas["view"]["revision_no"] == 1


def test_an_ai_node_carries_no_continuation_target(env, gate_decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import node_dispatch
    from modules.flow_gate.services import token_service

    issued, jobs = [], []
    real_issue = token_service.issue
    monkeypatch.setattr(token_service, "issue", lambda **kw: issued.append(kw) or real_issue(**kw))
    node_dispatch.set_ai_starter(jobs.append)
    start(env)
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env, "gn_d"))
    decide_review(FG, d, "approve", "approved")
    assert [kw["continuation_target_seq"] for kw in issued] == [None, None]     # gn_d, gn_p: one call each
    assert [j["node_key"] for j in jobs] == ["gn_p"]
