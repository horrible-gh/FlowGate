"""Real-store regression: a stale decision must respect newly confirmed inputs."""
import pytest

from test_fg_revision_adoption_precheck_0010 import (
    FA, _sql, db_path, env, decided, candidate, document_write_ports, confirm_candidate,
)
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain.frontier import Decision
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import definition_store, run_store


@pytest.mark.parametrize("status", [None, "pending", "open", "review", "rejected"])
def test_confirmed_join_blocks_saved_opening_then_opens_once(candidate, status):
    env, keys, nodes, edges = candidate
    target = keys["tmp_g"]
    run_id = "run_precheck"
    # Revision 1 has root -> target. Revision 2 adds root -> added -> target.
    edges.append({"edge_id": "ge_join", "source": "gn_added", "target": target, "condition": None})
    with get_store().transaction():
        definition_store.replace_draft_content(FA, nodes, edges)
    confirm_candidate()
    _sql(env["db"], "UPDATE graph_node_runs SET status = 'pending' WHERE node_key = ?", (target,))
    _sql(env["db"], "UPDATE graph_runs SET status = 'running', idle_reason = NULL WHERE run_id = ?", (run_id,))
    if status is not None:
        _sql(env["db"], "INSERT INTO graph_node_runs (run_id,node_key,status,root_bound,updated_at) "
             "VALUES (?, 'gn_added', ?, 0, 't')", (run_id, status))

    def apply(settle=True):
        def body():
            ctx = run_apply.load_context(run_store.run_by_id(run_id), "flowgate", "u_kim", [])
            run_apply.apply_decision(ctx, Decision(open=[target]), settle=settle)
        with_flow_guard(FA, body)

    # Includes settlement: a veto must not recursively keep offering the stale target.
    apply()
    assert next(r for r in run_store.node_runs(run_id) if r["node_key"] == target)["status"] == "pending"
    assert run_store.active_attempt_id(run_id, target) is None
    assert run_store.token_ids(run_id, target) == []
    assert not _sql(env["db"], "SELECT * FROM graph_run_events WHERE node_key = ? AND cause = 'opened'", (target,))
    assert run_store.run_by_id(run_id)["revision_no"] == 1

    if status is None:
        _sql(env["db"], "INSERT INTO graph_node_runs (run_id,node_key,status,root_bound,updated_at) "
             "VALUES (?, 'gn_added', 'done', 0, 't')", (run_id,))
    else:
        _sql(env["db"], "UPDATE graph_node_runs SET status = 'done' WHERE node_key = 'gn_added'")
    _sql(env["db"], "UPDATE graph_runs SET status = 'running', idle_reason = NULL WHERE run_id = ?", (run_id,))
    apply(settle=False)
    apply(settle=False)
    assert next(r for r in run_store.node_runs(run_id) if r["node_key"] == target)["status"] == "open"
    assert len(_sql(env["db"], "SELECT * FROM graph_node_attempts WHERE node_key = ?", (target,))) == 1
    assert len(_sql(env["db"], "SELECT * FROM graph_run_events WHERE node_key = ? AND cause = 'opened'", (target,))) == 1
    assert run_store.run_by_id(run_id)["revision_no"] == 1
