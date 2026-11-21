"""Pre-decision cancellation through real HTTP routes and SQLite transactions."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from test_fg_gate_intake_0085 import (
    product, env, db_path, pre_feature_attempts, slot, allowed,
    FA, _h, _sql, _linear, _start, _current, _nodes, _attempt,
    _decide, _decisions, _jobs, _gate_events, _confirmed, node, edge, setting,
)


def intent(env, run_id, key, request="cancel-1"):
    return {"request_id": request, "expected_attempt_id": _attempt(env, run_id, key)["attempt_id"],
            "expected_settings_version": 0}


def cancel(env, run_id, key, body):
    return env["client"].post(f"/api/v1/runs/{run_id}/nodes/{key}/gate-cancel", json=body, headers=_h())


def test_cancel_resets_unlocks_and_restarts_without_effects(product, monkeypatch):
    from flowgate.gate_decisions import preflight, legacy_adapter
    def forbidden(*args, **kwargs):
        pytest.fail("cancellation must not preflight or queue approval work")
    monkeypatch.setattr(preflight, "plan", forbidden)
    monkeypatch.setattr(legacy_adapter, "insert_gate_publish", forbidden)
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    before = _nodes(_current(product))
    body = intent(product, run_id, g)
    res = cancel(product, run_id, g, body)
    assert res.status_code == 200, res.text
    answer = res.json()
    assert answer["decision"]["result"] == "canceled"
    assert answer["decision"]["status"] == "canceled" and answer["job"] is None
    current = _current(product)
    assert (current["status"], current["idle_reason"]) == ("idle", "stopped")
    assert _nodes(current)[g]["status"] == "pending"
    assert _nodes(current)[t]["status"] == "pending"
    for key, old in before.items():
        if old["status"] == "done":
            assert _nodes(current)[key]["result_doc_id"] == old["result_doc_id"]
            assert _nodes(current)[key]["status"] == "done"
    assert _attempt(product, run_id, g)["status"] == "invalidated"
    assert _jobs(product) == [] and _gate_events(product, g) == []
    assert _sql(product["db"], "SELECT * FROM gate_decision_effects") == []
    assert _sql(product["db"], "SELECT * FROM gate_archive_checkpoints") == []
    events = _sql(product["db"], "SELECT payload_json FROM graph_run_events WHERE cause = 'gate_decision_canceled'")
    assert len(events) == 1
    assert json.loads(events[0]["payload_json"])["gate_decision_id"] == answer["decision"]["decision_id"]
    history = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions", headers=_h()).json()
    assert history["latest"]["result"] == "canceled"
    assert cancel(product, run_id, g, body).status_code == 200
    assert len(_decisions(product)) == 1
    restarted = _start(product)
    assert _nodes(_current(product))[g]["status"] == "open"
    assert _attempt(product, restarted["run_id"], g)["attempt_id"] != body["expected_attempt_id"]
    # Replaying the old request must not stop the new command or duplicate events.
    assert cancel(product, run_id, g, body).status_code == 200
    assert _nodes(_current(product))[g]["status"] == "open"
    assert len(_sql(product["db"], "SELECT * FROM graph_run_events WHERE cause = 'gate_decision_canceled'")) == 1


def test_approval_winner_refuses_cancel(product):
    g, _ = _linear(product)
    run_id = _start(product)["run_id"]
    body = intent(product, run_id, g)
    _decide(product, run_id, g, {**body, "request_id": "approve", "decision": "confirm"})
    assert cancel(product, run_id, g, body).status_code == 409
    assert [r["status"] for r in _decisions(product)] == ["succeeded"]


def test_cancel_refuses_stale_context_and_revoked_permission(product, allowed):
    g, _ = _linear(product)
    run_id = _start(product)["run_id"]
    body = intent(product, run_id, g)
    for changes in ({"expected_attempt_id": "old"}, {"expected_settings_version": 99}):
        assert cancel(product, run_id, g, {**body, **changes}).status_code == 409
    assert _decisions(product) == []
    assert cancel(product, run_id, g, body).status_code == 200
    allowed["allow"] = False
    assert cancel(product, run_id, g, body).status_code == 403


@pytest.mark.parametrize("action", ["gate-cancel", "gate-decision"])
def test_concurrent_requests_have_one_decision(product, action):
    g, _ = _linear(product)
    run_id = _start(product)["run_id"]
    body = intent(product, run_id, g)
    barrier = Barrier(2)
    def post(endpoint, payload):
        with TestClient(product["client"].app) as client:
            barrier.wait(timeout=10)
            return client.post(f"/api/v1/runs/{run_id}/nodes/{g}/{endpoint}", json=payload, headers=_h())
    other = body if action == "gate-cancel" else {**body, "request_id": "approve", "decision": "confirm"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(post, "gate-cancel", body)
        b = pool.submit(post, action, other)
        responses = [a.result(timeout=30), b.result(timeout=30)]
    assert sorted(r.status_code for r in responses) == ([200, 200] if action == "gate-cancel" else [200, 409]), [r.text for r in responses]
    assert len(_decisions(product)) == 1


def test_accepted_publish_refuses_predecision_cancel(product, slot):
    g, _ = _linear(product)
    setting(product, FA, g, "merge_mode", "commit")
    run_id = _start(product)["run_id"]
    body = intent(product, run_id, g)
    _decide(product, run_id, g, {"decision": "confirm", "request_id": "accepted"}, expect=202)
    res = cancel(product, run_id, g, body)
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "gate_decision_in_progress"
    assert _decisions(product)[0]["status"] == "accepted"


def test_canceled_gate_can_be_removed_from_the_draft(product):
    from modules.flow_gate.graph_workflow.store import definition_store
    g, _ = _linear(product)
    run_id = _start(product)["run_id"]
    assert cancel(product, run_id, g, intent(product, run_id, g)).status_code == 200
    record = definition_store.revision(FA, 1)
    nodes = [{"node_key": n["node_key"], "client_key": None, "node_type": n["node_type"],
              "doc_type": n.get("doc_type"), "label": n.get("label"), "palette_item_id": n.get("palette_item_id"),
              "assignee": None} for n in record["nodes"] if n["node_key"] != g]
    res = product["client"].put(f"/api/v1/flows/{FA}/graph/draft",
                                 json={"draft_version": 0, "nodes": nodes, "edges": []}, headers=_h())
    assert res.status_code == 200, res.text
    canvas = product["client"].get(f"/api/v1/flows/{FA}/canvas?view=draft", headers=_h()).json()
    assert g not in [n["node_key"] for n in canvas["nodes"]]


def test_cancel_requires_versioned_request(product):
    g, _ = _linear(product)
    run_id = _start(product)["run_id"]
    for body in ({}, {"request_id": "x"}, {"request_id": "x", "expected_attempt_id": "a", "expected_settings_version": True}):
        assert cancel(product, run_id, g, body).status_code == 422
    assert _decisions(product) == []
