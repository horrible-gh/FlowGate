"""flowgate.v02.0053 T#9 — the top band's 다음 단계 · 전체 실행 · 중지 on the Graph run API, as the client sends them.

The Designer's run buttons (client ``center/run/flowRunControl.ts``) call the existing run routes
(``graph_workflow/api/run_routes.py``) in these orders:

- 전체 실행: ``GET /flows/{id}/runs/current`` -> a stopped run is resumed (``POST /runs/{run}/resume``), else a
  run starts (``POST /flows/{id}/runs``).
- 다음 단계: the same, then ``POST /runs/{run}/stop`` at once — the cards it opened keep their work (tokens);
  their results wait (pending) and nothing after them opens until the next 다음 단계 / 전체 실행 resumes.
- 중지: ``POST /runs/{run}/stop``.
- 확인 card: ``POST /runs/{run}/nodes/{node}/gate-decision``.

On the same real SQLite DB and fixtures as ``test_fg_card_settings`` (card documents and card settings
registered as ``startup`` does): a real run is made, its status and node states change and are published,
stop really stops the frontier, the run hands out with the card's own settings (T#2) and binds its result
into the card's document line made when the card was placed (T#1). Without decided run rules (the product
state: D0004 §8 unanswered, every policy slot ``undecided``) a start is refused with
``run_policy_undecided`` and no run exists — the reason the client shows as a toast.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — pytest fixtures and helpers of the card settings tests
    FA, TEST_VERSION, _TestPolicy, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft, setting,
)

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


def _all(env):
    """전체 실행 as the client sends it."""
    current = _current(env)
    if current and current["status"] == "stopped":
        return _post(env, f"/runs/{current['run_id']}/resume")["run"]
    return _post(env, f"/flows/{FA}/runs", 201, {"revision_no": None})["run"]


def _next(env):
    """다음 단계 as the client sends it: 전체 실행, then stop at once."""
    run = _all(env)
    return _post(env, f"/runs/{run['run_id']}/stop")["run"] if run["status"] == "running" else run


def _states(env):
    return {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}


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


# ── 전체 실행 · 중지 ─────────────────────────────────────────────────────────────

def test_run_all_makes_a_real_run_stop_stops_it_and_run_all_again_resumes_the_same_run(env, decided):
    (t1, t2), _body = _confirmed(env, [("T", LEE), ("T", LEE)])
    assert _current(env) is None

    run = _all(env)
    assert run["status"] == "running" and run["open_node_keys"] == [t1]
    assert _current(env)["run_id"] == run["run_id"]
    assert _states(env)[t1] == "open" and _states(env)[t2] == "pending"

    stopped = _post(env, f"/runs/{run['run_id']}/stop")["run"]
    assert stopped["status"] == "stopped" and _current(env)["status"] == "stopped"
    # A second start is refused while the run is stopped: the client resumes it instead.
    assert _post(env, f"/flows/{FA}/runs", 409, {"revision_no": None})["error"]["code"] == "run_already_active"

    # Stopped means stopped: the open card finishes, nothing after it opens.
    _finish(env, t1, 90)
    assert _states(env)[t1] == "done" and _states(env)[t2] == "pending"

    resumed = _all(env)
    assert resumed["run_id"] == run["run_id"] and resumed["status"] == "running"
    assert _states(env)[t2] == "open"
    _finish(env, t2, 91)
    assert _current(env) is None                                   # all done: completed, no current run
    assert _sql(env["db"], "SELECT status FROM graph_runs")[0]["status"] == "completed"
    assert _run_statuses(env) == [(None, "running"), ("running", "stopped"), ("stopped", "running"), ("running", "completed")]
    # Every node change is published too (the Canvas reads itself again on these).
    assert any(e.event_type == "graph_node_state_changed" for e in env["events"])


def test_stop_refuses_a_run_that_is_not_running(env, decided):
    _confirmed(env, [("T", LEE)])
    run = _next(env)
    assert _post(env, f"/runs/{run['run_id']}/stop", 409)["error"]["code"] == "run_not_running"


# ── 다음 단계 ─────────────────────────────────────────────────────────────────

def test_next_step_opens_one_step_at_a_time(env, decided):
    (t1, t2), _body = _confirmed(env, [("T", LEE), ("T", LEE)])

    first = _next(env)                                             # start, then stop at once
    assert first["status"] == "stopped" and first["open_node_keys"] == [t1]
    assert _states(env)[t1] == "open"
    _finish(env, t1, 90)                                           # its card works on while stopped
    assert _states(env)[t2] == "pending"

    second = _next(env)                                            # resume -> the next card opens, stop again
    assert second["run_id"] == first["run_id"] and second["status"] == "stopped"
    assert second["open_node_keys"] == [t2]
    assert _run_statuses(env) == [(None, "running"), ("running", "stopped"), ("stopped", "running"), ("running", "stopped")]


# ── the run uses the card ────────────────────────────────────────────────────

def test_the_run_hands_out_with_the_card_settings_and_binds_into_the_placed_card_document(env, decided):
    (t1,), body = _confirmed(env, [("T", AI)])
    instruction = next(line["doc_id"] for line in body["card_documents"][t1] if line["slot"] == "instruction")
    setting(env, FA, t1, "run_ai", "aip_card")                     # T#2: the card's own run AI
    setting(env, FA, t1, "restart", 2)

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


def test_a_gate_is_decided_while_its_run_runs_and_not_while_it_is_stopped(env, gate_decided):
    (t1, gate), _body = _confirmed(env, [("T", LEE), ("gate", None)])
    run = _all(env)
    _finish(env, t1, 90)
    assert _states(env)[gate] == "open"

    stopped = _post(env, f"/runs/{run['run_id']}/stop")["run"]
    refused = _post(env, f"/runs/{stopped['run_id']}/nodes/{gate}/gate-decision", 409, {"decision": "confirm"})
    assert refused["error"]["code"] == "run_not_running"

    _all(env)
    decided_run = _post(env, f"/runs/{run['run_id']}/nodes/{gate}/gate-decision", body={"decision": "confirm", "comment": "확인"})["run"]
    assert {n["node_key"]: n["status"] for n in decided_run["node_runs"]}[gate] == "done"
    assert decided_run["status"] == "completed"
