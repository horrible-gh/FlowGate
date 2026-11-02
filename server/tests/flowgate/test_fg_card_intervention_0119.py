"""flowgate.v02.0119 T#3 (T-2) — a general card nothing completes is ``needs_intervention`` (or a ``system_error``), never
a person's approval; it stops only its own descendants, and the idle reason tells a confirm wait from an intervention.

On the same real SQLite DB and fixtures as ``test_fg_card_completion_0119`` (every migration, the card documents and card
settings registered as ``startup`` does, plus ``flowgate.card_completion``). The cards are person cards whose result is
bound when it is registered with the node's token (``pre_feature_attempts``); the card stage's end is the record it
writes (``card_settings.legacy_adapter.record_approval_wait``: a review that did not pass after its automatic rework, or
a failed automatic completion), its automatic completion the T-1 completion (``card_settings.legacy_adapter.approve``).
Fault injection happens here only, never on an operating run:

- a review that did not pass -> ``needs_intervention`` with its reason; the card approval route refuses it (409);
- its descendants stay pending while an independent branch goes on to its end; the run then goes idle
  ``needs_intervention`` (never ``completed``, never the former ``waiting_approval``);
- the automatic completion of a card in intervention (what its re-review's pass makes) clears its intervention and opens
  what follows it — only that propagation; the request path itself (the automatic rework spent first, a memo, the real
  ``POST /ai-invoke/start`` rework and re-review, the pass completing the card) is
  ``test_fg_card_intervention_assigned_0119``'s end-to-end case;
- the Time Machine rewinds it: the card leaves its intervention and its rework attempt completes;
- a failed automatic completion path is a ``system_error``: the run's error family (idle ``error``);
- a run that went idle before T-2 with ``waiting_approval`` is read as ``needs_intervention`` and never converted.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — pytest fixtures and helpers of the card settings tests
    legacy_cards, pre_feature_attempts,
    FA, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft,
)

pytestmark = pytest.mark.usefixtures("legacy_cards", "pre_feature_attempts", "completions", "fork_join")

LEE = {"kind": "human", "user_id": "u_lee", "provider_id": None}
NOT_PASSED = "review_not_passed:review_count_exhausted"


@pytest.fixture()
def completions(env):
    """``flowgate.card_completion`` registered as ``startup`` does (its ports and its install mark)."""
    from flowgate.card_completion import integration

    integration.register()
    yield
    integration.unregister()


@pytest.fixture()
def fork_join(decided):
    """The production branch and join rules (``flowgate.graph_policies``, as ``test_fg_document_progress_0113``) for the
    validator and the runs started while it is used, over ``decided``'s other slots."""
    from flowgate.graph_policies import fork, join
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    slots = fork.SLOTS + join.SLOTS
    validators = dict(policies._registered)
    old = {}
    for key, value, version, impl in slots:
        run_policies.register(key, value, version, impl)
        old[key] = policies.set_system_default(key, value, version)
    saved = policies._registered
    policies._registered = {**validators, **{key: impl for key, _v, _ver, impl in slots}}
    yield
    policies._registered = saved
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


@pytest.fixture()
def no_gates(monkeypatch):
    """The automatic completion's read-only document gates (body / test gate) pass: no file is written here."""
    from flowgate.card_settings import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)


def _branches(env):
    """R -> a -> a2 and R -> b -> b2: two independent branches of person cards. -> (a, a2, b, b2)."""
    refs = ("tmp_a", "tmp_a2", "tmp_b", "tmp_b2")
    nodes = [node("tmp_r", "R")] + [node(ref, "T", assignee=LEE) for ref in refs]
    edges = [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_a", "tmp_a2"),
             edge("tmp_e3", "tmp_r", "tmp_b"), edge("tmp_e4", "tmp_b", "tmp_b2")]
    body = put_draft(env, FA, 0, nodes, edges)
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    return tuple(body["key_map"][ref] for ref in refs)


def _command(env, mode="full"):
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=_h())
    assert res.status_code in (200, 201), res.text
    return res.json()["run"]


def _current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _node_run(env, key):
    return next(n for n in _current(env)["node_runs"] if n["node_key"] == key)


def _states(env):
    return {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}


def _stored_run(env):
    row = _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE group_id = ? ORDER BY rowid DESC", (FA,))[0]
    return row["status"], row["idle_reason"]


def _token(env, key):
    return _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid DESC",
                (key,))[0]["token_id"]


def _attempts(env, key):
    return _sql(env["db"], "SELECT attempt_id, status FROM graph_node_attempts WHERE node_key = ? ORDER BY created_at",
                (key,))


def _submit(env, key, seq):
    """The card's result registered with its token (the inbox contact): the node goes to review."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    result = _doc(FA, seq, "T", "pending_review")
    v01_contact.after_inbox_registered(FA, result, "T", _token(env, key))
    assert _states(env)[key] == "review"
    return result


def _stage_ended(key, result, reason=NOT_PASSED):
    """The card's stage ended without a completion: its B-1 reason, as ``review_stage._after_review`` records it."""
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.graph_workflow.store import run_store

    run = run_store.active_run(FA)
    legacy_adapter.record_approval_wait(FA, key, result, reason,
                                        attempt_id=run_store.active_attempt_id(run["run_id"], key))


def _completed(key, result):
    """The card's automatic completion (a passed review, review 'none'), as the card stage makes it (T-1)."""
    from flowgate.card_settings import legacy_adapter

    assert legacy_adapter.approve(FA, result, "u_kim", legacy_adapter.AUTOMATION, node_key=key) is True


def _approve(env, key, expect):
    run_id = _current(env)["run_id"]
    res = env["client"].post(f"/api/v1/runs/{run_id}/nodes/{key}/approval", json=None,
                             headers={**_h(), "Authorization": "Bearer user:u_kim"})
    assert res.status_code == expect, res.text
    return res.json()


# ── needs_intervention: only its descendants wait ───────────────────────────

def test_a_review_that_did_not_pass_stops_only_its_descendants_and_the_run_waits_for_its_intervention(
        env, decided, no_gates):
    a, a2, b, b2 = _branches(env)
    _command(env)
    assert (_states(env)[a], _states(env)[b]) == ("open", "open")
    result_a = _submit(env, a, 90)
    _stage_ended(a, result_a)

    shown = _node_run(env, a)
    assert shown["intervention"] == {"kind": "needs_intervention", "reason": NOT_PASSED}
    assert (shown["awaiting_approval"], shown["approval_wait_reason"]) == (False, NOT_PASSED)
    assert _approve(env, a, 409)["error"]["code"] == "needs_intervention"         # never a person's approval
    assert _states(env)[a] == "review"

    result_b = _submit(env, b, 91)                                      # the independent branch goes on
    _completed(b, result_b)
    assert (_states(env)[b], _states(env)[b2], _states(env)[a2]) == ("done", "open", "pending")
    result_b2 = _submit(env, b2, 92)
    _completed(b2, result_b2)
    run = _current(env)
    assert (_states(env)[b2], _states(env)[a2]) == ("done", "pending")  # a's descendant never opened
    assert (run["status"], run["idle_reason"]) == ("idle", "needs_intervention")
    assert _stored_run(env) == ("idle", "needs_intervention")
    assert _node_run(env, b2)["intervention"] is None


def test_the_automatic_completion_of_a_card_in_intervention_clears_it_and_opens_what_follows(env, decided, no_gates):
    a, a2, b, _b2 = _branches(env)
    _command(env)
    result_a = _submit(env, a, 90)
    _stage_ended(a, result_a)
    assert _node_run(env, a)["intervention"]["kind"] == "needs_intervention"

    _completed(a, result_a)                     # its automatic completion (as a passed re-review makes it)
    assert (_states(env)[a], _states(env)[a2]) == ("done", "open")
    assert _node_run(env, a)["intervention"] is None
    assert len(_attempts(env, a2)) == 1


def test_the_time_machine_takes_a_card_out_of_its_intervention_and_its_rework_completes(env, decided, no_gates):
    from flowgate.graph_time_machine import rewind
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    a, a2, b, _b2 = _branches(env)
    _command(env)
    result_a = _submit(env, a, 90)
    _stage_ended(a, result_a)
    result_b = _submit(env, b, 91)
    _completed(b, result_b)
    assert _node_run(env, a)["intervention"]["kind"] == "needs_intervention"

    def step():
        ctx = run_apply.load_context(run_store.active_run(FA), "flowgate", None, [])
        ids = [x["attempt_id"] for x in _attempts(env, a)]
        return rewind.rewind(ctx, {"node_keys": [a], "attempt_ids": ids, "documents": []}, [])

    assert with_flow_guard(FA, step)["reset_node_keys"] == [a]
    assert _states(env)[a] == "pending" and _node_run(env, a)["intervention"] is None

    _command(env, "step")                                               # the rework: a new attempt of a
    rework = _submit(env, a, 93)
    assert _node_run(env, a)["intervention"] == {"kind": "needs_intervention", "reason": "completion_missing"}
    _completed(a, rework)
    assert _states(env)[a] == "done" and len(_attempts(env, a)) == 2
    assert _node_run(env, a)["intervention"] is None
    _command(env)                                                       # a step's propagation waits for the next command
    assert _states(env)[a2] == "open"


# ── system_error: the run's error family ────────────────────────────────────

def test_a_failed_automatic_completion_is_a_system_error_and_the_run_is_in_error(env, decided, no_gates):
    a, a2, b, b2 = _branches(env)
    _command(env)
    result_a = _submit(env, a, 90)
    _stage_ended(a, result_a, "approval_refused")
    for key, seq in ((b, 91), (b2, 92)):
        _completed(key, _submit(env, key, seq))
    shown = _node_run(env, a)
    assert shown["intervention"] == {"kind": "system_error", "reason": "approval_refused"}
    run = _current(env)
    assert (run["status"], run["idle_reason"]) == ("idle", "error")
    assert _approve(env, a, 409)["error"]["code"] == "system_error"
    assert _states(env)[a2] == "pending"


# ── a run idle before T-2: read, never converted ────────────────────────────

def test_a_stored_waiting_approval_is_read_as_needs_intervention_and_never_converted(env, decided, no_gates):
    a, _a2, b, b2 = _branches(env)
    _command(env)
    result_a = _submit(env, a, 90)
    _stage_ended(a, result_a)
    for key, seq in ((b, 91), (b2, 92)):
        _completed(key, _submit(env, key, seq))
    assert _stored_run(env) == ("idle", "needs_intervention")
    _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'waiting_approval' WHERE group_id = ? AND status = 'idle'",
         (FA,))                                                         # as a run idle before T-2 stored it
    for _ in range(2):
        assert _current(env)["idle_reason"] == "needs_intervention"
    assert _stored_run(env) == ("idle", "waiting_approval")             # the stored value is not converted


def test_no_card_awaits_a_persons_approval_any_more(env, decided, no_gates):
    from modules.flow_gate.graph_workflow.services import node_approval

    a, _a2, _b, _b2 = _branches(env)
    _command(env)
    result_a = _submit(env, a, 90)
    _stage_ended(a, result_a)
    row = next(r for r in _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (a,)))
    assert node_approval.refusal(FA, dict(row)) == "needs_intervention"
    assert node_approval.awaiting_approval(FA, dict(row)) is False
    assert all(n["awaiting_approval"] is False for n in _current(env)["node_runs"])
