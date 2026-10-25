"""0083 T#1, 0056 T#1 개정: bound R/B is input, not work or a Step.

Start/resume/settings use real HTTP routes and SQLite, with the existing AI job recorder.
Completion below is an explicit resolver-boundary mock, not an AI/UI end-to-end claim.
No direct v01_contact or inbox submission substitutes for the missing end-to-end check.
"""
import json

import pytest

from test_fg_card_settings import (
    FA, FB, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft, setting,
)
from test_fg_card_approval_0056 import LEE, card_approval_port


def _chain(env, typ="R", bound=True):
    flow = FA if typ == "R" and bound else FB
    if bound and typ == "B":
        _doc(flow, 1, "B", "approved")
    body = put_draft(env, flow, 0,
                     [node("tmp_r", typ, assignee=LEE), node("tmp_wp", "WP", assignee=LEE),
                      node("tmp_t", "T", assignee=LEE)],
                     [edge("tmp_e1", "tmp_r", "tmp_wp"), edge("tmp_e2", "tmp_wp", "tmp_t")])
    res = env["client"].post(f"/api/v1/flows/{flow}/graph/revisions",
                             json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    if not bound:
        # Isolated DB fixture: card placement creates a draft root and confirmation binds it.
        # Remove only this fixture binding to exercise a genuinely unbound R/B work card.
        _sql(env["db"], "DELETE FROM graph_root_bindings WHERE group_id = ?", (flow,))
    return flow, [body["key_map"][k] for k in ("tmp_r", "tmp_wp", "tmp_t")]


def _command(env, flow, mode, rid=None):
    url = f"/api/v1/runs/{rid}/resume" if rid else f"/api/v1/flows/{flow}/runs"
    res = env["client"].post(url, json={"mode": mode}, headers=_h())
    assert res.status_code == (200 if rid else 201), res.text
    return _read(env, flow) if rid else res.json()["run"]


def _rows(run):
    return {n["node_key"]: n for n in run["node_runs"]}


def _read(env, flow):
    res = env["client"].get(f"/api/v1/flows/{flow}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


@pytest.mark.parametrize("typ", ["R", "B"])
@pytest.mark.parametrize("mode", ["step", "full"])
@pytest.mark.parametrize("status,review", [
    ("draft", None), ("open", None), ("open", "pending_review"),
    ("open", "rejected"), ("open", "approved"), ("open", "wf_in_progress"), ("open", "wf_done"),
])
def test_0056_T1_개정_root_input_is_done_before_first_work(env, decided, typ, mode, status, review):
    from modules.flow_gate.db import documents

    flow, (root, wp, following) = _chain(env, typ)
    doc = f"{flow}.0001-{typ}"
    documents.update(doc, {"status": status, "doc_review_status": review})
    run = _command(env, flow, mode)
    rows = _rows(run)
    assert (rows[root]["status"], rows[root]["result_doc_id"], rows[root]["awaiting_approval"]) == (
        "done", doc, False)
    assert rows[root]["root_bound"] and rows[root]["dispatch"] is None
    assert (rows[wp]["status"], rows[following]["status"]) == ("open", "pending")
    assert run["open_node_keys"] == [wp] and run["idle_reason"] != "no_targets"
    assert _sql(env["db"], "SELECT status, doc_review_status FROM documents WHERE doc_id = ?", (doc,)) == [
        {"status": status, "doc_review_status": review}]
    props = _sql(env["db"], "SELECT outcome, status FROM graph_propagations WHERE node_key = ?", (root,))
    assert props == [{"outcome": "approved", "status": "done"}]
    assert not _sql(env["db"], "SELECT * FROM graph_run_events WHERE cause = 'review_rejected'")
    if mode == "step":
        targets = _sql(env["db"], "SELECT step_node_keys FROM graph_runs WHERE run_id = ?", (run["run_id"],))
        assert json.loads(targets[0]["step_node_keys"]) == [wp]


@pytest.mark.parametrize("typ", ["R", "B"])
@pytest.mark.parametrize("mode", ["step", "full"])
def test_0056_T1_개정_unbound_requirement_still_opens_as_work(env, decided, typ, mode):
    flow, (root, wp, following) = _chain(env, typ, bound=False)
    run = _command(env, flow, mode)
    rows = _rows(run)
    assert not rows[root]["root_bound"]
    assert (rows[root]["status"], rows[wp]["status"], rows[following]["status"]) == (
        "open", "pending", "pending")


@pytest.mark.parametrize("mode", ["step", "full"])
def test_0056_T1_개정_held_rejected_root_binds_once_on_next_command(env, decided, mode):
    from modules.flow_gate.db import documents

    flow, (root, wp, following) = _chain(env)
    documents.update(f"{flow}.0001-R", {"doc_review_status": "rejected"})
    setting(env, flow, root, "hold", True)
    run = _command(env, flow, mode)
    assert _rows(run)[root]["status"] == "pending"
    assert not _sql(env["db"], "SELECT * FROM graph_propagations")
    setting(env, flow, root, "hold", False)
    assert _rows(_read(env, flow))[wp]["status"] == "pending"
    resumed = _command(env, flow, mode, run["run_id"])
    assert (_rows(resumed)[root]["status"], _rows(resumed)[wp]["status"]) == ("done", "open")
    _command(env, flow, mode, run["run_id"])
    assert len(_sql(env["db"], "SELECT * FROM graph_propagations WHERE node_key = ?", (root,))) == 1


def _complete_at_resolver_boundary(env, flow, rid, key):
    """Mock work completion only; exercise real decisions/settlement, not a result-submission path."""
    from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, resolve
    from modules.flow_gate.graph_workflow.domain.run_policies import policies_of
    from modules.flow_gate.graph_workflow.services import run_apply, run_service
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    jobs = []
    def complete():
        ctx = run_apply.load_context(run_store.run_by_id(rid), "flowgate", "u_kim", jobs)
        policies = policies_of(run_store.policy_entries(rid))
        for event in (RunEvent("result_submitted", key, doc_id="mock-result"),
                      RunEvent("review_approved", key, doc_id="mock-result")):
            decision = resolve(ctx.revision, run_apply.read_run_state(ctx), event, policies)
            assert not decision.ignored
            run_apply.apply_decision(ctx, decision)
    with_flow_guard(flow, complete)
    run_service.run_jobs(jobs)


@pytest.mark.parametrize("mode", ["step", "full"])
def test_0056_T1_개정_step_boundary_and_full_continuation_with_mock_completion(env, decided, mode):
    flow, (root, wp, following) = _chain(env)
    run = _command(env, flow, mode)
    _complete_at_resolver_boundary(env, flow, run["run_id"], wp)
    current = _read(env, flow)
    assert _rows(current)[wp]["status"] == "done"
    if mode == "step":
        assert (current["status"], current["idle_reason"]) == ("idle", "step_done")
        assert _rows(current)[following]["status"] == "pending"
        current = _command(env, flow, "step", run["run_id"])
    assert _rows(current)[following]["status"] == "open"
