"""Pure impact/safety calculation from execution history, never the Canvas draft.

Each consumer uses its current attempt's revision; an unopened consumer uses the
run's adopted revision. Recorded input attempt IDs add actual result dependencies
(including ones forwarded through gates). Reachability follows these directed
dependencies only, so A -> J <- B, J -> C selects A/J/C and preserves B.

Snapshot attempts are the current active or latest ended attempt of each node,
as selected by run_store.current_attempts. Superseded/invalidated attempts are
history, not new rewind targets. Missing history fails closed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from flowgate.graph_validation.run_scope import run_scope


@dataclass
class Snapshot:
    flow_id: str
    run: dict | None = None
    nodes: list[dict] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    revisions: dict = field(default_factory=dict)  # revision_no -> immutable Graph
    ledger_rows: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    busy_runs: list[dict] = field(default_factory=list)
    executions: list[dict] = field(default_factory=list)
    integrations: list[dict] = field(default_factory=list)
    conflict_sessions: list[dict] = field(default_factory=list)
    incomplete_operations: list[dict] = field(default_factory=list)
    gate_decisions: list[dict] = field(default_factory=list)
    gate_jobs: list[dict] = field(default_factory=list)


def input_ids(attempt: dict) -> list[str]:
    raw = attempt.get("input_attempt_ids")
    value = json.loads(raw) if isinstance(raw, str) else raw
    if value is None and attempt.get("input_source") != "predecessors":
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError("invalid input_attempt_ids")
    return sorted(set(value))


def calculate(snapshot: Snapshot, node_key: str) -> dict:
    """Stable JSON-compatible contract; reasons use codes, localization is outside.

    impact.commits describes ownership, NOT an ordered revert plan. Only integration
    commits belong on the group branch; result commits must not be reverted again.
    no_commit ledger rows are valid attribution evidence, not Git targets.
    """
    run = snapshot.run or {}
    nodes = {n["node_key"]: n for n in snapshot.nodes}
    attempts = {a["node_key"]: a for a in snapshot.attempts
                if a["status"] in ("active", "ended") and a["run_id"] == run.get("run_id")}
    by_id = {a["attempt_id"]: a for a in attempts.values()}
    revision_no = run.get("revision_no")
    current_graph = snapshot.revisions.get(revision_no)
    targets = set(run_scope(current_graph).targets) if current_graph is not None else set()
    reasons = []

    def block(code, **params):
        reasons.append({"code": code, **params})

    if not run:
        block("run_missing")
    elif run.get("status") != "idle":
        block("run_not_idle", run_id=run["run_id"], status=run.get("status"))
    other_busy = [r for r in snapshot.busy_runs if r["run_id"] != run.get("run_id")]
    if other_busy:
        block("run_not_idle", runs=other_busy)
    if snapshot.executions:
        block("executions_in_progress", executions=snapshot.executions)
    if snapshot.integrations:
        block("integrations_in_progress", attempts=snapshot.integrations)
    if snapshot.conflict_sessions:
        block("merge_conflict_open", sessions=snapshot.conflict_sessions)
    if snapshot.incomplete_operations:
        block("previous_operation_incomplete", operations=snapshot.incomplete_operations)
    if snapshot.gate_jobs:
        block("gate_jobs_in_progress", jobs=snapshot.gate_jobs)
    selected_status = nodes.get(node_key, {}).get("status")
    selected_node = current_graph.node(node_key) if current_graph is not None else None
    is_gate = selected_node is not None and selected_node.node_type == "gate"
    reset_required = any(d["node_key"] == node_key and d.get("recovery_reset_required")
                         for d in snapshot.gate_decisions)
    if not (selected_status == "done" or is_gate and (
            selected_status == "rejected" or selected_status == "pending" and reset_required)):
        block("node_not_done", node_key=node_key, status=selected_status)
    if node_key not in targets:
        block("node_outside_run_scope", node_key=node_key)

    # Use each consumer's revision, not a union of all historical edge sets.
    # Pending nodes are included even when they have never acquired an attempt.
    dependencies = {key: set() for key in nodes}
    missing_revisions, bad_inputs = set(), []
    decoded = {}
    if run and current_graph is None:
        missing_revisions.add(revision_no)
    for key in nodes:
        attempt = attempts.get(key)
        number = attempt["revision_no"] if attempt else revision_no
        graph = snapshot.revisions.get(number)
        if graph is None or graph.node(key) is None:
            missing_revisions.add(number)
        else:
            for edge in graph.live_edges():
                if edge.target == key and edge.source in nodes:
                    dependencies[key].add(edge.source)
        if attempt:
            try:
                ids = input_ids(attempt)
            except (TypeError, ValueError):
                bad_inputs.append(attempt["attempt_id"])
                ids = []
            decoded[attempt["attempt_id"]] = ids
            for aid in ids:
                source = by_id.get(aid)
                if source is None:
                    bad_inputs.append(attempt["attempt_id"])
                else:
                    dependencies[key].add(source["node_key"])
    # An unknown consumer could depend on the selected attempt: do not offer a
    # seemingly safe, truncated plan when history needed to find descendants is lost.
    if missing_revisions:
        block("execution_history_incomplete", revision_nos=sorted(missing_revisions, key=str))
    if bad_inputs:
        block("execution_history_incomplete", attempt_ids=sorted(set(bad_inputs)))

    affected = {node_key} if node_key in nodes else set()
    while True:
        reached = {key for key, preds in dependencies.items() if preds & affected}
        if reached <= affected:
            break
        affected.update(reached)
    selected = [attempts[key] for key in sorted(affected) if key in attempts]
    selected_ids = {a["attempt_id"] for a in selected}
    ledger = [dict(r) for r in snapshot.ledger_rows
              if r.get("attempt_id") in selected_ids
              and r.get("node_key") == by_id[r["attempt_id"]]["node_key"]
              and r.get("group_id") == snapshot.flow_id]
    attributed = {r["attempt_id"] for r in ledger if r.get("state") in ("live", "no_commit")}
    def needs_attribution(attempt):
        if attempt.get("input_source") == "document_only":
            return False
        # Once a result exists, missing ownership must still fail closed.
        if attempt.get("result_commit") or attempt.get("integrated_commit"):
            return True
        graph = snapshot.revisions.get(attempt["revision_no"])
        node = graph.node(attempt["node_key"]) if graph is not None else None
        if node is not None and node.node_type == "gate":
            return False
        # Human-only attempts never prepare a workspace. Do not infer this from
        # NULL input_source alone: pre-T#8 Git attempts still require attribution.
        if (attempt.get("workspace_prepared") is False
                and not attempt.get("input_commit") and not attempt.get("input_source")):
            return False
        # Approval creates the result/ledger later. Pending/failed integration is
        # not this case, nor is an ended legacy attempt without a ledger entry.
        if (attempt["status"] == "active"
                and nodes[attempt["node_key"]]["status"] in ("open", "review")
                and attempt.get("integration_status") in (None, "", "none")):
            return False
        return True

    missing = [a["attempt_id"] for a in selected
               if a["attempt_id"] not in attributed and needs_attribution(a)]
    if missing:
        block("commit_attribution_missing", attempt_ids=sorted(missing))
    # A bound root deliberately has no attempt/token/Git result. Legacy progressed
    # nodes without attempts must not silently bypass the pre-T#8 attribution guard.
    missing_nodes = [key for key in sorted(affected) if key not in attempts
                     and nodes[key]["status"] in ("done", "open", "review")
                     and not nodes[key].get("root_bound")]
    if missing_nodes:
        block("commit_attribution_missing", node_keys=missing_nodes)
    mixed = [a["attempt_id"] for a in selected if a.get("input_source") == "integration_head"]
    if mixed:
        block("integration_head_input", attempt_ids=sorted(mixed))

    commits = []
    git_ids = {a["attempt_id"] for a in selected if a.get("input_source") != "document_only"}
    for row in sorted(ledger, key=lambda r: r["id"]):
        if row["attempt_id"] in git_ids and row.get("state") == "live" and row.get("commit_sha"):
            commits.append({"kind": "result", "commit_sha": row["commit_sha"],
                            "attempt_id": row["attempt_id"], "node_key": row["node_key"],
                            "ledger_row_id": row["id"]})
    for attempt in selected:
        if attempt["attempt_id"] in git_ids and attempt.get("integrated_commit"):
            commits.append({"kind": "integration", "commit_sha": attempt["integrated_commit"],
                            "attempt_id": attempt["attempt_id"], "node_key": attempt["node_key"]})

    fields = ("attempt_id", "node_key", "revision_no", "status", "input_source",
              "input_commit", "result_commit", "integration_status", "integrated_commit")
    impact = {
        "node_keys": sorted(affected),
        "revision_nos": sorted({a["revision_no"] for a in selected}
                               | ({revision_no} if run else set())),
        "attempts": [{**{k: a.get(k) for k in fields},
                      "input_attempt_ids": decoded.get(a["attempt_id"], [])} for a in selected],
        "documents": sorted([dict(d) for d in snapshot.documents if d["attempt_id"] in selected_ids],
                            key=lambda d: (d["doc_id"], d["attempt_id"])),
        "ledger_rows": sorted(ledger, key=lambda r: r["id"]),
        "commits": commits,
        "gate_decisions": [d for d in snapshot.gate_decisions if d["node_key"] in affected],
    }
    return {"flow_id": snapshot.flow_id, "run_id": run.get("run_id"), "node_key": node_key,
            "allowed": not reasons, "impact": impact, "reasons": reasons}
