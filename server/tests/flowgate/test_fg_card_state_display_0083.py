"""flowgate.v02.0083 T#4 — the Canvas state of a Graph card follows the run facts the server already tells apart.

``canvas_mapping.node_run_state`` maps an open node whose writer ended without a result (the run wire's
``dispatch.error_code``: ``no_result``, a failed AI start or run) to ``blocked`` instead of ``in_progress``; every other
node keeps its table value. ``graph_projector`` hands each node its run wire, so the Canvas read and the run read say
the same thing. No new state value: ``blocked`` is P0008 §2.3's.

rev1: the Canvas read's ``run`` block carries the run band's facts of the same run read (``run_payload``: command, idle
reason, AI executions, waiting cards), so a Canvas read newer than the client's last run read tells the band as of
itself and the client never shows an older read's reason or executions beside its status.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.projection.graph_projector import GraphView, project_graph
from modules.flow_gate.graph_workflow.services import canvas_service

FLOW = "test2.default.0083"


def wire(status, error_code=None, running=0, requested=0, question=False, result_doc_id=None):
    """A node run as ``run_service.node_run_wire`` sends it (the fields this mapping may read)."""
    return {"status": status, "result_doc_id": result_doc_id, "running_execution_count": running,
            "requested_execution_count": requested, "question": question,
            "dispatch": {"token_id": "tok_x", "error_code": error_code} if error_code is not None else {"token_id": "tok_x"}}


@pytest.mark.parametrize("run,state", [
    (wire("open", running=1), "in_progress"),                       # its AI works
    (wire("open", requested=1), "in_progress"),                     # its AI is about to start
    (wire("open"), "in_progress"),                                  # a person / an answer to come: no Canvas state of its own
    (wire("open", question=True), "in_progress"),
    (wire("open", error_code="no_result"), "blocked"),              # the AI ended without a result
    (wire("open", error_code="ai_invoke_failed"), "blocked"),       # the AI run failed
    (wire("open", error_code="provider_unavailable"), "blocked"),   # the AI start was refused
    (wire("review", error_code="no_result", result_doc_id="d"), "review"),   # only an open writer is judged
    (wire("done", error_code="no_result", result_doc_id="d"), "done"),
    (wire("pending"), "draft"),
    (wire("rejected"), "blocked"),
    ({"status": "open", "dispatch": None}, "in_progress"),          # a node no token was issued for
    ({"status": "open"}, "in_progress"),
])
def test_the_node_run_state_reads_the_run_wire(run, state):
    assert cm.node_run_state(run["status"], run) == state


def test_without_a_run_wire_the_table_is_unchanged():
    assert [cm.node_run_state(s) for s in ("pending", "open", "review", "done", "rejected", "odd")] == \
        ["draft", "in_progress", "review", "done", "blocked", None]
    assert cm.node_run_state(None) == "draft"
    assert cm.node_run_state(None, wire("open", error_code="no_result")) == "draft"


def _card(key, doc_type):
    return {"node_key": key, "node_type": "document", "doc_type": doc_type, "label": doc_type, "palette_item_id": None}


def test_the_canvas_projection_gives_each_node_the_state_of_its_run_wire():
    view = GraphView(mode="revision", nodes=[_card("r", "R"), _card("wp", "WP"), _card("hp", "WP"), _card("t", "T")],
                     bound_node_key="r", bound_doc_id=f"{FLOW}.0001-R",
                     node_runs={"r": wire("done", result_doc_id=f"{FLOW}.0001-R"),
                                "wp": wire("open", error_code="no_result"),
                                "hp": wire("open"),
                                "t": wire("open", running=1)})
    docs = [{"doc_id": f"{FLOW}.0001-R", "group_id": FLOW, "type_code": "R", "title": "R", "status": "open",
             "doc_review_status": None}]
    out = project_graph(FLOW, "graph", docs, view)
    states = {n["node_key"]: n["state"] for n in out.nodes if n.get("identity_source") == "graph_node"}
    assert states == {"r": "in_progress", "wp": "blocked", "hp": "in_progress", "t": "in_progress"}
    by_key = {n["node_key"]: n for n in out.nodes}
    # the wire itself goes to the client unchanged (the card reads its own facts from it)
    assert by_key["wp"]["node_run"]["dispatch"]["error_code"] == "no_result"
    assert by_key["hp"]["node_run"]["running_execution_count"] == 0


def payload(status, idle_reason=None, seq=15, running=0, requested=0, pending=(), execution=True):
    """A run read as ``run_service.run_payload(with_nodes=True)`` gives it (``execution``: the execution hook's counts)."""
    out = {"run_id": "run_1", "flow_id": FLOW, "revision_no": 1, "status": status, "started_at": 1, "started_by": "u",
           "stopped_at": None, "finished_at": None, "open_node_keys": [], "last_event_seq": seq,
           "pending_propagation_count": len(pending), "pending_node_keys": list(pending), "mode": "step",
           "idle_reason": idle_reason, "node_runs": [{"node_key": "t", "status": "done"}]}
    if execution:
        out.update(running_execution_count=running, requested_execution_count=requested, execution_limit=4)
    return out


@pytest.mark.parametrize("read,band", [
    (payload("idle", "completed"), {"mode": "step", "idle_reason": "completed", "running_execution_count": 0,
                                    "requested_execution_count": 0, "execution_limit": 4,
                                    "pending_propagation_count": 0, "pending_node_keys": []}),
    (payload("idle", "step_done", pending=("hp",)), {"idle_reason": "step_done", "pending_propagation_count": 1,
                                                    "pending_node_keys": ["hp"]}),
    (payload("running", running=1), {"idle_reason": None, "running_execution_count": 1, "requested_execution_count": 0}),
])
def test_the_canvas_run_block_carries_the_band_facts_of_the_same_read(read, band):
    block = canvas_service._run_block(read)
    assert {k: block[k] for k in ("run_id", "status", "revision_no", "open_node_keys", "last_event_seq")} ==         {"run_id": "run_1", "status": read["status"], "revision_no": 1, "open_node_keys": [], "last_event_seq": 15}
    assert {k: block[k] for k in band} == band
    assert "node_runs" not in block and "started_by" not in block      # the nodes carry their own runs


def test_the_canvas_run_block_sends_no_count_the_read_does_not_carry():
    block = canvas_service._run_block(payload("idle", "waiting_approval", execution=False))
    assert block["idle_reason"] == "waiting_approval" and block["mode"] == "step"
    assert not {"running_execution_count", "requested_execution_count", "execution_limit"} & set(block)
