"""flowgate.v02.0053 T#9 — the top band's 다음 단계 · 전체 실행 · 중지 on the Graph run API, as the client sends them.

flowgate.v02.0113 T#3 rework (0025-T ⑤): the client sends one command per button since 0061 T#14
(``center/run/flowRunControl.ts``), and these tests follow it — no start-then-stop any more:

- 다음 단계: ``POST /flows/{id}/runs {"revision_no": null, "mode": "step"}`` — a new run, or the Flow's idle run going on
  with a new step command. A step opens the run candidates of that moment (its targets) and the run settles by itself
  (``idle``): what follows them waits for the next command (``step_done`` once its targets are done).
- 전체 실행: the same with ``"mode": "full"`` — the run goes on by itself as cards finish (an approval wakes it).
- 중지: ``POST /runs/{run}/stop`` (0061 T#7) — only a running or paused run; an idle run is refused ``run_not_running``.
- 확인 card: ``POST /runs/{run}/nodes/{node}/gate-decision``.

On the same real SQLite DB and fixtures as ``test_fg_card_settings`` (card documents and card settings registered as
``startup`` does): a real run is made, its status and node states change and are published, the run hands out with the
card's own settings (T#2) and binds its result into the card's document line made when the card was placed (T#1).
Without decided run rules (the product state: D0004 §8 unanswered, every policy slot ``undecided``) a start is refused
with ``run_policy_undecided`` and no run exists — the reason the client shows as a toast. The person cards finish with
their result registered at the opening (the pre-feature contract, ``pre_feature_attempts``); the assigned path of the
same buttons is ``test_fg_next_step_wait_0113`` / ``test_fg_ai_roles_l2_*``.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — pytest fixtures and helpers of the card settings tests
    legacy_cards, pre_feature_attempts,
    FA, TEST_VERSION, _TestPolicy, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft, setting,
)

# flowgate.v02.0113 T#3 (T §6.4): this suite's cards are cards made before the AI role feature (their legacy fields:
# assignee, run_ai, review_count / review_ai — L-1 §2.4.5); the stage assignment feature stays on, so each new attempt
# runs the assigned path (L-2) resolved from those fields. Pre-feature attempts are made explicitly
# (``pre_feature_attempts``) by the tests of the pre-feature contract.
pytestmark = pytest.mark.usefixtures("legacy_cards")

LEE = {"kind": "human", "user_id": "u_lee", "provider_id": None}
AI = {"kind": "ai", "user_id": None, "provider_id": "aip_assignee"}


def _confirmed(env, cards):
    """R -> card 1 -> card 2 … (``cards``: (doc_type or 'gate', assignee)), confirmed as revision 1."""
    nodes = [node("tmp_r", "R")]
    for i, (kind, assignee) in enumerate(cards):
        nodes.append(node(f"tmp_{i}", None, node_type="gate") if kind == "gate" else node(f"tmp_{i}", kind, assignee=assignee))
    refs = ["tmp_r"] + [f"tmp_{i}" for i in range(len(cards))]
    body = put_draft(env, FA, 0, nodes, [edge(f"tmp_e{i}", a, b) for i, (a, b) in enumerate(zip(refs, refs[1:]))])
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    return [body["key_map"][f"tmp_{i}"] for i in range(len(cards))], body


def _current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _post(env, path, expect=200, body=None):
    res = env["client"].post(f"/api/v1{path}", json=body or {}, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _command(env, mode):
    """One run button as the client sends it: a step / full command (a new run, or the Flow's idle run going on)."""
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=_h())
    assert res.status_code in (200, 201), res.text
    return res.json()["run"]


def _all(env):
    """전체 실행."""
    return _command(env, "full")


def _next(env):
    """다음 단계."""
    return _command(env, "step")


def _states(env):
    return {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}


def _run_row(env):
    return _sql(env["db"], "SELECT run_id, status, idle_reason, run_mode FROM graph_runs")


def _run_statuses(env):
    return [(e.payload["from"], e.payload["to"]) for e in env["events"] if e.event_type == "graph_run_status_changed"]


def _token(env, key):
    return _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid", (key,))[0]["token_id"]


def _finish(env, key, seq, typ="T"):
    """The card's result registered with its token and approved (the v0.1 contacts, as the inbox and review do)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    result = _doc(FA, seq, typ)
    v01_contact.after_inbox_registered(FA, result, typ, _token(env, key))
    db_documents.update(result, {"doc_review_status": "approved"})
    v01_contact.after_review_decided(FA, result, "approve")
    return result


# ── the product state: no decided run rules ──────────────────────────────────

def test_without_decided_run_rules_a_start_is_refused_and_no_run_is_made(env):
    _confirmed(env, [("T", LEE)])
    refused = _post(env, f"/flows/{FA}/runs", 409, {"revision_no": None})
    assert refused["error"]["code"] == "run_policy_undecided"
    assert {p["policy"] for p in refused["error"]["params"]["undecided_policies"]} >= {"start_node", "completion"}
    assert _current(env) is None
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_runs")[0]["n"] == 0


# ── 다음 단계: one step command, then the next command ────────────────────────

def test_next_step_opens_one_step_at_a_time(pre_feature_attempts, env, decided):
    (t1, t2), _body = _confirmed(env, [("T", LEE), ("T", LEE)])

    first = _next(env)                                             # the step opens its target and settles by itself
    assert (first["status"], first["mode"], first["open_node_keys"]) == ("idle", "step", [t1])
    assert first["idle_reason"] == "needs_intervention"              # its person card writes: never no_targets
    assert _states(env)[t1] == "open" and _states(env)[t2] == "pending"

    _finish(env, t1, 90)                                           # the step's target is done ...
    assert _states(env)[t1] == "done" and _states(env)[t2] == "pending"     # ... and nothing after it opens by itself
    held = _current(env)
    assert (held["run_id"], held["status"], held["idle_reason"]) == (first["run_id"], "idle", "step_done")
    assert {n["node_key"]: n["awaiting_approval"] for n in held["node_runs"]}[t1] is False

    second = _next(env)                                            # the next command: the same run, the next card
    assert (second["run_id"], second["status"], second["open_node_keys"]) == (first["run_id"], "idle", [t2])
    assert second["idle_reason"] == "needs_intervention" and _states(env)[t1] == "done"
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_runs")[0]["n"] == 1
    # the step, then the next step. 0119 T-2: its target's completion no longer wakes the step's command within its bound
    # (``command.resume_approval`` wakes a confirm wait only); the idle run's reason turns ``step_done`` in place
    assert _run_statuses(env) == [(None, "running"), ("running", "idle"), ("idle", "running"), ("running", "idle")]

    _finish(env, t2, 91)
    assert [(r["status"], r["idle_reason"]) for r in _run_row(env)] == [("idle", "completed")]


def test_next_step_with_nothing_to_open_says_why_the_run_waits(pre_feature_attempts, env, decided):
    """A step on a run whose open card waits (a person writes) opens nothing new and tells that wait, not
    "nothing to run" (0084)."""
    (t1, _t2), _body = _confirmed(env, [("T", LEE), ("T", LEE)])
    first = _next(env)
    again = _next(env)
    assert (again["run_id"], again["open_node_keys"]) == (first["run_id"], [t1])
    assert (again["status"], again["idle_reason"]) == ("idle", "needs_intervention")


# ── 전체 실행 · 중지 ─────────────────────────────────────────────────────────────

def test_run_all_goes_on_by_itself_as_its_cards_finish(pre_feature_attempts, env, decided):
    (t1, t2), _body = _confirmed(env, [("T", LEE), ("T", LEE)])
    assert _current(env) is None

    run = _all(env)
    assert (run["status"], run["mode"], run["open_node_keys"]) == ("idle", "full", [t1])
    assert run["idle_reason"] == "needs_intervention"
    assert _current(env)["run_id"] == run["run_id"]
    # A second run is never made while the Flow has one: the same command lets that run go on.
    assert _all(env)["run_id"] == run["run_id"]

    _finish(env, t1, 90)                                           # the full command goes on: no button
    assert _states(env)[t1] == "done" and _states(env)[t2] == "open"
    _finish(env, t2, 91)
    assert [(r["run_id"], r["status"], r["idle_reason"]) for r in _run_row(env)] == [(run["run_id"], "idle", "completed")]
    # Every node change is published too (the Canvas reads itself again on these).
    assert any(e.event_type == "graph_node_state_changed" for e in env["events"])


def test_stop_refuses_a_run_that_is_not_running(env, decided):
    _confirmed(env, [("T", LEE)])
    run = _next(env)
    assert _post(env, f"/runs/{run['run_id']}/stop", 409)["error"]["code"] == "run_not_running"


# ── the run uses the card ────────────────────────────────────────────────────

def test_the_run_hands_out_with_the_card_settings_and_binds_into_the_placed_card_document(env, decided):
    (t1,), body = _confirmed(env, [("T", AI)])
    instruction = next(line["doc_id"] for line in body["card_documents"][t1] if line["slot"] == "instruction")
    legacy_setting(env, FA, t1, "run_ai", "aip_card")                     # T#2: the card's own run AI
    legacy_setting(env, FA, t1, "restart", 2)

    run = _all(env)
    dispatch = {n["node_key"]: n["dispatch"] for n in run["node_runs"]}[t1]
    assert dispatch["token_issued"] is True and dispatch["issued_to"] == "aip_card"
    [job] = env["jobs"]
    assert (job["node_key"], job["provider_id"], job["restart_max"], job["doc_type"]) == (t1, "aip_card", 2, "T")

    from modules.flow_gate.graph_workflow.api import v01_contact
    result = _doc(FA, 92, "T")
    v01_contact.after_inbox_registered(FA, result, "T", job["token"]["token_id"])
    lines = _sql(env["db"], "SELECT slot, doc_id FROM graph_node_documents WHERE node_key = ? ORDER BY ordinal", (t1,))
    assert lines[0] == {"slot": "instruction", "doc_id": result}      # T#1: the card's line takes the result
    assert _sql(env["db"], "SELECT superseded_by FROM documents WHERE doc_id = ?", (instruction,))[0]["superseded_by"] == result


# ── 확인 card ─────────────────────────────────────────────────────────────────

class _GatePolicy(_TestPolicy):
    """The test double plus a decided gate slot: confirm -> confirmed, reject -> rejected."""

    def judge(self, _node_key, decision, _comment):
        return "confirmed" if decision == "confirm" else "rejected"


@pytest.fixture()
def gate_decided(monkeypatch):
    """Decided test rules with a gate rule; confirming a gate revision needs a validation slot (P0008 S3-5)."""
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    monkeypatch.setitem(policies._registered, "gate_node", object())
    gate = _GatePolicy()
    old = {}
    for key in policies.POLICY_KEYS:
        run_policies.register(key, "run_buttons_gate", TEST_VERSION, gate)
        old[key] = policies.set_system_default(key, "run_buttons_gate", TEST_VERSION)
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


# 0113 T#3 (T §6.4) pre-feature contract: its person card's result registered at the opening moves the run to the gate; an assigned person RUN card writes its instruction first (L-2 §2.1/§2.7) — test_fg_ai_roles_l2_assignment.py::test_g2_*
def test_a_gate_the_run_opened_is_decided_and_completes_the_run(pre_feature_attempts, env, gate_decided):
    (t1, gate), _body = _confirmed(env, [("T", LEE), ("gate", None)])
    run = _all(env)
    _finish(env, t1, 90)
    assert _states(env)[gate] == "open"
    waiting = _current(env)
    assert (waiting["status"], waiting["idle_reason"]) == ("idle", "waiting_confirm")    # the gate waits for a person
    # An idle run has nothing to stop (0061 T#7): the client offers no 중지 then.
    assert _post(env, f"/runs/{run['run_id']}/stop", 409)["error"]["code"] == "run_not_running"

    decided_run = _post(env, f"/runs/{run['run_id']}/nodes/{gate}/gate-decision",
                        body={"decision": "confirm", "comment": "확인"})["run"]
    assert {n["node_key"]: n["status"] for n in decided_run["node_runs"]}[gate] == "done"
    assert [(r["status"], r["idle_reason"]) for r in _run_row(env)] == [("idle", "completed")]


# Fixture-only legacy fields for the pre-T#3 execution contract.
from test_fg_card_settings import legacy_setting
