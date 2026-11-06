"""v02test.0010 T: draft adoption checks share the command's refusal path and never write."""
import copy

import pytest

from test_fg_card_settings import FA, _h, _sql, db_path, decided, edge, env, node, put_draft
from flowgate.graph_run import revision
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain import policies
from modules.flow_gate.graph_workflow.services import policy_snapshot
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.services.run_apply import load_context
from modules.flow_gate.graph_workflow.store import definition_store, run_store

GROUP = {"group_id": FA, "project_id": "flowgate"}
TABLES = (
    "graph_runs", "graph_node_runs", "graph_node_attempts", "graph_run_policy_entries",
    "graph_run_events", "graph_revisions", "graph_revision_nodes", "graph_revision_edges",
    "graph_drafts", "graph_draft_nodes", "graph_draft_edges", "documents",
)


@pytest.fixture(autouse=True)
def document_write_ports(monkeypatch):
    # Match startup's document write registration before the shared fixture seeds its root document.
    from flowgate.card_document_edit import integration
    from modules.flow_gate.graph_workflow.services import card_document_ports
    monkeypatch.setattr(card_document_ports, "_handlers", dict(card_document_ports._handlers))
    integration.register()


@pytest.fixture
def candidate(env, monkeypatch):
    from flowgate.graph_policies import integration
    from modules.flow_gate.graph_workflow.domain import run_policies, run_scope_ports
    monkeypatch.setattr(policies, "_registered", dict(policies._registered))
    monkeypatch.setattr(policies, "_system_default", dict(policies._system_default))
    monkeypatch.setattr(run_policies, "_implementations", dict(run_policies._implementations))
    monkeypatch.setattr(run_scope_ports, "_handler", run_scope_ports._handler)
    integration.register()
    saved = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_g", None, "gate")],
                      [edge("tmp_e", "tmp_r", "tmp_g")])
    response = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions",
                                  json={"draft_version": 1}, headers=_h())
    assert response.status_code == 201, response.text
    record = definition_store.revision(FA, 1)
    probe = {"run_id": "run_precheck", "group_id": FA, "revision_no": 1}
    graph = load_context(probe, "flowgate", "u_kim").revision
    with get_store().transaction():
        run_store.create_run("run_precheck", FA, 1, "u_kim",
                             policy_snapshot.policy_set_for_run(FA), graph)
        get_store()._execute("UPDATE graph_runs SET status = 'idle', idle_reason = 'completed' WHERE run_id = ?",
                             ["run_precheck"])
        get_store()._execute("UPDATE graph_node_runs SET status = 'done' WHERE run_id = ?", ["run_precheck"])
        definition_store.create_draft(FA, 1, "u_kim")
        added = {**record["nodes"][0], "node_key": "gn_added", "doc_type": "T", "label": "Added"}
        nodes = record["nodes"] + [added]
        edges = record["edges"] + [{"edge_id": "ge_added", "source": record["nodes"][0]["node_key"],
                                    "target": "gn_added", "condition": None}]
        definition_store.replace_draft_content(FA, nodes, edges)
    return env, saved["key_map"], nodes, edges


def facts(env):
    return {**{table: _sql(env["db"], "SELECT * FROM " + table) for table in TABLES},
            "events": copy.deepcopy(env["events"])}


def assess(env):
    response = env["client"].get(f"/api/v1/flows/{FA}/runs/revision-adoption", headers=_h())
    assert response.status_code == 200, response.text
    return response.json()


def confirm_candidate():
    with get_store().transaction():
        definition_store.insert_revision_from_draft(FA, 2, 1, 1, "u_kim")
        definition_store.delete_draft(FA)


@pytest.mark.parametrize("reason", [
    "active_attempts", "open", "review", "gate_rejected", "gate_recovery_reset_required",
    "gate_jobs_in_progress", "reservation", "run_policy_undecided", "graph_node_protected",
])
def test_assessment_and_adoption_have_identical_refusals_without_writes(candidate, monkeypatch, reason):
    from flowgate.gate_decisions import rounds, repository
    env, keys, nodes, edges = candidate
    gate = keys["tmp_g"]
    if reason == "active_attempts":
        monkeypatch.setattr(run_store, "has_active_attempts", lambda _: True)
    elif reason in ("open", "review", "gate_rejected"):
        _sql(env["db"], "UPDATE graph_node_runs SET status = ? WHERE node_key = ?",
             ("rejected" if reason == "gate_rejected" else reason, gate))
    elif reason == "gate_recovery_reset_required":
        monkeypatch.setattr(repository, "reset_required", lambda _flow, key: {"id": "reset"} if key == gate else None)
    elif reason == "gate_jobs_in_progress":
        monkeypatch.setattr(rounds, "unfinished", lambda _: [{"job_id": "job", "target_key": gate}])
    elif reason == "reservation":
        monkeypatch.setattr(repository, "reservations_of_group", lambda _: [{"decision_id": "decision", "node_key": gate}])
    elif reason == "run_policy_undecided":
        old = policies.set_system_default("report_pair", policies.UNDECIDED, policies.UNDECIDED_IMPL_VERSION)
    elif reason == "graph_node_protected":
        nodes[0] = {**nodes[0], "label": "changed protected root"}
        with get_store().transaction():
            definition_store.replace_draft_content(FA, nodes, edges)
    try:
        before = facts(env)
        result = assess(env)
        assert result["adoptable"] is False
        assert facts(env) == before
        assert definition_store.latest_revision_no(FA) == 1
        confirm_candidate()
        before_adopt = facts(env)
        run = run_store.current_run(FA)
        with pytest.raises(GraphApiError) as error:
            with_flow_guard(FA, lambda: revision.adopt_revision(GROUP, run, "u_kim", []))
        assert result["error"]["code"] == error.value.code
        assert result["error"]["params"] == error.value.params
        assert result["error"]["status"] == error.value.status == 409
        assert facts(env) == before_adopt
    finally:
        if reason == "run_policy_undecided":
            policies.set_system_default("report_pair", *old)


def test_allowed_assessment_does_not_write_and_existing_command_adopts(candidate):
    env, _keys, _nodes, _edges = candidate
    before = facts(env)
    assert assess(env)["adoptable"] is True
    assert facts(env) == before
    confirm_candidate()
    response = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"mode": "full"}, headers=_h())
    assert response.status_code == 201, response.text
    assert response.json()["run"]["revision_no"] == 2
    assert {n["node_key"] for n in run_store.node_runs("run_precheck")} >= {"gn_added"}


@pytest.mark.parametrize("status", ["running", "stopping", "paused"])
def test_non_idle_run_is_refused_without_writes(candidate, status):
    env, *_ = candidate
    _sql(env["db"], "UPDATE graph_runs SET status = ? WHERE run_id = ?", (status, "run_precheck"))
    before = facts(env)
    result = assess(env)
    assert result["adoptable"] is False
    assert result["error"]["code"] == "run_already_active"
    assert result["error"]["params"] == {"flow_id": FA, "run_id": "run_precheck", "status": status}
    assert facts(env) == before


def test_no_run_is_not_an_adoption_target(env):
    before = facts(env)
    assert assess(env)["adoptable"] is True
    assert assess(env)["target"] is None
    assert facts(env) == before


def test_no_draft_is_not_an_adoption_target(candidate):
    env, *_ = candidate
    with get_store().transaction():
        definition_store.delete_draft(FA)
    before = facts(env)
    assert assess(env)["target"] is None
    assert facts(env) == before


def test_endpoint_enforces_read_auth(candidate, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    env, *_ = candidate
    path = f"/api/v1/flows/{FA}/runs/revision-adoption"
    assert env["client"].get(path).status_code == 401
    monkeypatch.setattr(envelope, "has_permission", lambda *_: False)
    assert env["client"].get(path, headers=_h()).status_code == 403
