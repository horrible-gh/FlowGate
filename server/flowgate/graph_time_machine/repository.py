"""Read an execution-history snapshot using existing stores (0061 T#12).

Call under the Flow guard for a deciding read. No Git, Canvas draft, mutations,
settle, reconciliation or latest-revision adoption happen here.

T#13's durable operation/event contract: graph_run_status_changed events with
cause=graph_time_machine, payload.operation_id and payload.operation_status.
started/partial_failed remain blocking until that SAME operation is completed
or blocked (Git compensation succeeded). A different completed operation cannot
clear a prior partial failure. Its full recovery journal is T#13's concern.
"""
from __future__ import annotations

import json

from modules.flow_gate.db import git_integration
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge
from modules.flow_gate.graph_workflow.projection.graph_projector import to_graph_node
from modules.flow_gate.graph_workflow.store import definition_store, run_store

from .impact import Snapshot

OPERATION_CAUSE = "graph_time_machine"


def _documents(flow_id: str, current_ids: set[str]) -> list[dict]:
    # Same durable authorship rule as registered_document_attempt: the latest
    # mapped new/edit registration wins. Review tokens never reattribute a doc.
    mapped = run_store.authoring_token_map(flow_id)
    seen, out = set(), []
    for event in run_store._all(
            "SELECT metadata FROM workflow_events WHERE group_id = ? AND event_type = 'token_consumed' "
            "ORDER BY id DESC", [flow_id]):
        try:
            data = json.loads(event.get("metadata") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        owner = mapped.get(data.get("token_id"))
        doc_id = data.get("doc_id")
        if owner and isinstance(doc_id, str) and doc_id and doc_id not in seen:
            seen.add(doc_id)
            if owner["attempt_id"] in current_ids:
                out.append({"doc_id": doc_id, "attempt_id": owner["attempt_id"],
                            "node_key": owner["node_key"], "stage": owner.get("stage")})
    return out


def incomplete_operations(flow_id: str) -> list[dict]:
    latest = {}
    for event in run_store._all(
            "SELECT e.run_id, e.event_seq, e.payload_json FROM graph_run_events e "
            "JOIN graph_runs r ON r.run_id = e.run_id WHERE r.group_id = ? "
            "AND e.event_type = 'graph_run_status_changed' AND e.cause = ? "
            "ORDER BY e.created_at, e.run_id, e.event_seq", [flow_id, OPERATION_CAUSE]):
        try:
            data = json.loads(event["payload_json"])
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        # Malformed operation events fail closed too; never overwrite one with
        # another unrelated event which happens to lack an operation ID.
        operation_id = data.get("operation_id") or f"{event['run_id']}:{event['event_seq']}"
        latest[operation_id] = {"operation_id": operation_id, "run_id": event["run_id"],
                                "status": data.get("operation_status") or "unknown"}
    return [latest[k] for k in sorted(latest)
            if latest[k]["status"] not in ("completed", "blocked")]


def read_snapshot(flow_id: str) -> Snapshot:
    snapshot = Snapshot(flow_id)
    if not run_store.available():
        return snapshot
    run = run_store.active_run(flow_id)
    if run is None:
        # Pre-151 completed runs released active_group_id, yet still own results.
        history, _total = run_store.list_runs(flow_id, 1, 0)
        run = history[0] if history else None
    snapshot.run = run
    snapshot.busy_runs = run_store._all(
        "SELECT run_id, status FROM graph_runs WHERE group_id = ? "
        "AND status IN ('running','paused','stopping','stopped') ORDER BY run_id", [flow_id])
    snapshot.executions = run_store._all(
        "SELECT x.exec_id, x.attempt_id, x.run_id, x.status FROM graph_attempt_runs x "
        "JOIN graph_runs r ON r.run_id = x.run_id WHERE r.group_id = ? "
        "AND x.status IN ('requested','running') ORDER BY x.exec_id", [flow_id])
    snapshot.integrations = run_store._all(
        "SELECT a.attempt_id, a.node_key, a.run_id, a.integration_status FROM graph_node_attempts a "
        "JOIN graph_runs r ON r.run_id = a.run_id WHERE r.group_id = ? "
        "AND a.integration_status IN ('pending','integrating') ORDER BY a.attempt_id", [flow_id])
    sessions = run_store._all(
        "SELECT * FROM git_merge_session WHERE group_id = ? AND status = 'open' ORDER BY merge_id", [flow_id])
    snapshot.conflict_sessions = [
        {"merge_id": s["merge_id"], "attempt_id": git_integration.session_context(s).get("attempt_id")}
        for s in sessions if git_integration.session_kind(s) == git_integration.SESSION_KIND_GRAPH_ATTEMPT_MERGE]
    snapshot.incomplete_operations = incomplete_operations(flow_id)
    if run is None:
        return snapshot
    run_id = run["run_id"]
    snapshot.nodes = run_store.node_runs(run_id)
    current_ids = set(run_store.current_attempts(run_id).values())
    snapshot.attempts = [r for r in run_store._all(
        "SELECT * FROM graph_node_attempts WHERE run_id = ? AND status IN ('active','ended') "
        "ORDER BY node_key, created_at, attempt_id", [run_id]) if r["attempt_id"] in current_ids]
    # Keep durable preparation evidence after worktree cleanup. Absence is only
    # established when the journal is readable; malformed history fails closed.
    prepared, readable = set(), True
    for event in run_store._all(
            "SELECT payload_json FROM graph_run_events WHERE run_id = ? "
            "AND event_type = 'graph_run_status_changed' AND cause = 'attempt_workspace'",
            [run_id]):
        try:
            data = json.loads(event["payload_json"])
        except (TypeError, ValueError):
            readable = False
            continue
        if not isinstance(data, dict) or not data.get("attempt_id"):
            readable = False
            continue
        prepared.add(data["attempt_id"])
    for attempt in snapshot.attempts:
        attempt["workspace_prepared"] = (
            True if attempt["attempt_id"] in prepared else False if readable else None)
    for number in sorted({run["revision_no"], *(a["revision_no"] for a in snapshot.attempts)}):
        record = definition_store.revision(flow_id, number)
        if record is not None:
            snapshot.revisions[number] = Graph(
                nodes=tuple(to_graph_node(n) for n in record["nodes"]),
                edges=tuple(GraphEdge(e["edge_id"], e["source"], e["target"], e.get("condition"))
                            for e in record["edges"]))
    snapshot.ledger_rows = run_store._all(
        "SELECT id, group_id, doc_id, attempt_id, node_key, state, commit_sha, skip_reason "
        "FROM tr_commit_ledger WHERE group_id = ? AND attempt_id IS NOT NULL ORDER BY id", [flow_id])
    snapshot.documents = _documents(flow_id, current_ids)
    from flowgate.gate_decisions import rounds
    graph = snapshot.revisions.get(run["revision_no"])
    gates = {n.key for n in graph.nodes if n.node_type == "gate"} if graph else set()
    snapshot.gate_jobs = rounds.unfinished(flow_id)
    snapshot.gate_decisions = rounds.impact_entries(flow_id, gates)
    return snapshot
