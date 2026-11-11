"""v02test.0018: open/review cards no longer block a revision adoption as a whole.

Each branch of a fork keeps going on its own: a card waiting for review (or still open) keeps its row, its attempt and
the attempt's revision_no, while a card added to another branch is adopted and opened. ``protect_definition`` still
refuses a change to such a card's definition or input edges, and an active attempt outside an open/review row still
refuses the adoption (``active_attempts``).
"""
import pytest

from test_fg_revision_adoption_precheck_0010 import (
    FA, GROUP, _h, _sql, db_path, env, decided, candidate, document_write_ports,
    facts, assess, confirm_candidate,
)
from flowgate.graph_run import revision
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import definition_store, run_store

RUN = "run_precheck"


def _status(env, key, status):
    _sql(env["db"], "UPDATE graph_node_runs SET status = ? WHERE run_id = ? AND node_key = ?", (status, RUN, key))


def _attempt(env, key):
    _sql(env["db"], "INSERT INTO graph_node_attempts "
         "(attempt_id,run_id,node_key,revision_no,status,active_node_key,created_at) VALUES (?,?,?,1,'active',?,'t')",
         ("att_" + key, RUN, key, key))


def _states():
    return {r["node_key"]: r["status"] for r in run_store.node_runs(RUN)}


def _attempts(env, key):
    return [(a["status"], a["revision_no"]) for a in
            _sql(env["db"], "SELECT status, revision_no FROM graph_node_attempts WHERE run_id = ? AND node_key = ?",
                 (RUN, key))]


@pytest.mark.parametrize("status", ["review", "open"])
@pytest.mark.parametrize("with_attempt", [True, False], ids=["attempt", "pre151_row"])
def test_live_card_stays_and_the_added_card_enters_pending(candidate, status, with_attempt):
    env, keys, *_ = candidate
    gate = keys["tmp_g"]
    _status(env, gate, status)
    if with_attempt:
        _attempt(env, gate)
    confirm_candidate()
    before = facts(env)
    result = assess(env)
    assert (result["adoptable"], result["target"]) == (True, "revision")
    assert facts(env) == before                                   # the precheck never writes
    run = run_store.current_run(FA)
    assert with_flow_guard(FA, lambda: revision.adopt_revision(GROUP, run, "u_kim", [])) is True
    assert run_store.current_run(FA)["revision_no"] == 2
    states = _states()
    assert states[gate] == status                                 # the live card is left as it was
    assert states["gn_added"] == "pending"                        # the added card waits for the command's frontier
    assert _attempts(env, gate) == ([("active", 1)] if with_attempt else [])   # its attempt keeps revision 1


def test_command_opens_the_added_branch_and_keeps_the_review_branch_waiting(candidate):
    """The reproduction: one branch is done, another waits for review; a card after the done card and a card after the
    review card are added. The command adopts revision 2, opens only the card whose inputs are done, and leaves the
    review card and the card behind it as they are."""
    env, keys, nodes, edges = candidate
    gate = keys["tmp_g"]
    after_gate = {**next(n for n in nodes if n["node_key"] == "gn_added"), "node_key": "gn_after_review",
                  "label": "After review"}
    nodes.append(after_gate)
    edges.append({"edge_id": "ge_after_review", "source": gate, "target": "gn_after_review", "condition": None})
    with get_store().transaction():
        definition_store.replace_draft_content(FA, nodes, edges)
    _status(env, gate, "review")
    _attempt(env, gate)
    confirm_candidate()
    assert assess(env)["adoptable"] is True
    response = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"mode": "full"}, headers=_h())
    assert response.status_code == 201, response.text
    assert response.json()["run"]["revision_no"] == 2
    states = _states()
    assert states[gate] == "review"
    assert states["gn_added"] not in ("pending", None)          # its input (the done root) is complete: opened
    assert states["gn_after_review"] == "pending"               # opens when the review card is approved
    assert ("active", 1) in _attempts(env, gate)


@pytest.mark.parametrize("change", ["definition", "input_edge"])
def test_changing_a_review_card_is_still_protected(candidate, change):
    env, keys, nodes, edges = candidate
    gate = keys["tmp_g"]
    if change == "definition":
        nodes[:] = [{**n, "label": "changed review card"} if n["node_key"] == gate else n for n in nodes]
    else:
        edges.append({"edge_id": "ge_new_input", "source": "gn_added", "target": gate, "condition": None})
    with get_store().transaction():
        definition_store.replace_draft_content(FA, nodes, edges)
    _status(env, gate, "review")
    _attempt(env, gate)
    confirm_candidate()
    before = facts(env)
    result = assess(env)
    assert result["adoptable"] is False
    assert (result["error"]["code"], result["error"]["status"]) == ("graph_node_protected", 409)
    assert result["error"]["params"]["node_keys"] == [gate]
    assert facts(env) == before
    response = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"mode": "full"}, headers=_h())
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "graph_node_protected"
    assert facts(env) == before
    assert run_store.current_run(FA)["revision_no"] == 1


def test_active_attempt_outside_a_live_row_still_refuses(candidate):
    env, keys, *_ = candidate
    gate = keys["tmp_g"]
    _status(env, gate, "review")
    _attempt(env, gate)
    _sql(env["db"], "INSERT INTO graph_node_runs (run_id,node_key,status,root_bound,updated_at) "
         "VALUES (?,?,'pending',0,'t')", (RUN, "K_pending"))
    _attempt(env, "K_pending")
    confirm_candidate()
    before = facts(env)
    result = assess(env)
    assert result["error"]["code"] == "graph_revision_migration_blocked"
    params = result["error"]["params"]
    assert params["reason"] == "active_attempts"
    assert params["node_keys"] == ["K_pending"]                 # the review card is not a blocker
    assert params["blockers"] == [{"node_key": "K_pending", "row_status": "pending", "active_attempt": True}]
    assert facts(env) == before
