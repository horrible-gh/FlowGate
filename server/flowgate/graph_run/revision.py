"""0061 T#3: adopt the latest confirmed revision inside the command's Flow guard.

All checks precede writes. The guard transaction covers the revision, policy entries,
node rows and audit event; dispatch stays in the existing post-commit job path.
"""
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain.run_policies import policies_of, required_policies, undecided_for
from modules.flow_gate.graph_workflow.domain.run_scope_ports import run_scope
from modules.flow_gate.graph_workflow.services import policy_snapshot, run_events
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.run_apply import load_context
from modules.flow_gate.graph_workflow.store import definition_store, layout_store, run_store
from flowgate.graph_run.editing import protect_definition


def adopt_revision(group: dict, run: dict, actor: str, jobs: list) -> bool:
    flow_id, run_id = run["group_id"], run["run_id"]
    latest = definition_store.latest_revision_no(flow_id)
    old_no = int(run["revision_no"])
    if latest is None or latest <= old_no:
        return False
    rows = run_store.node_runs(run_id)
    # Pre-151 open/review rows have no attempt record: do not migrate their live work either.
    if run_store.has_active_attempts(run_id) or any(r["status"] in ("open", "review") for r in rows):
        raise GraphApiError(409, "graph_revision_migration_blocked", {
            "flow_id": flow_id, "run_id": run_id, "reason": "active_attempts",
            "from_revision_no": old_no, "to_revision_no": latest})
    record = definition_store.revision(flow_id, latest)
    if record is None:
        raise GraphApiError(404, "revision_not_found", {"flow_id": flow_id, "revision_no": latest})
    protect_definition(flow_id, record["nodes"], record["edges"], run)
    candidate = {**run, "revision_no": latest}
    ctx = load_context(candidate, group.get("project_id"), actor, jobs)
    targets = run_scope(ctx.revision).targets
    snapshot = policy_snapshot.policy_set_for_run(flow_id)
    root = ctx.root_document() if ctx.binding else None
    missing = undecided_for(required_policies(ctx.revision, (ctx.binding or {}).get("node_key"),
                                             (root or {}).get("doc_review_status")), snapshot["entries"])
    if missing:
        raise GraphApiError(409, "run_policy_undecided", {
            "flow_id": flow_id, "revision_no": latest, "undecided_policies": missing})
    policies_of(snapshot["entries"])  # Restore/implementation check before any write.
    store, now = get_store(), layout_store.utc_now_ms()
    existing = {r["node_key"] for r in rows}
    for row in rows:
        if row["status"] == "pending" and row["node_key"] not in targets:
            store._execute("DELETE FROM graph_node_runs WHERE run_id = ? AND node_key = ? AND status = 'pending'",
                           [run_id, row["node_key"]])
    for key in sorted(set(targets) - existing):
        store._execute("INSERT INTO graph_node_runs (run_id, node_key, status, root_bound, updated_at) "
                       "VALUES (?, ?, 'pending', 0, ?)", [run_id, key, now])
    store._execute("DELETE FROM graph_run_policy_entries WHERE run_id = ?", [run_id])
    for key, entry in snapshot["entries"].items():
        store._execute("INSERT INTO graph_run_policy_entries "
                       "(run_id, policy_key, policy_value, impl_version) VALUES (?, ?, ?, ?)",
                       [run_id, key, entry["value"], entry["impl_version"]])
    moved = store._execute_affected(
        "UPDATE graph_runs SET revision_no = ?, policy_source = ?, policy_set_id = ?, policy_set_version = ?, "
        "policy_digest = ?, step_node_keys = NULL, updated_at = ? WHERE run_id = ? AND revision_no = ?",
        [latest, snapshot["source"], snapshot["set_id"], snapshot["set_version"], snapshot["digest"], now, run_id, old_no])
    if moved != 1:
        raise AssertionError("revision adoption missed its run")
    seq = int(run["last_event_seq"]) + 1
    payload = run_events.run_status_payload(candidate, seq, run["status"], run["status"],
                                            "revision_adopted", actor, None)
    payload.update(from_revision_no=old_no, to_revision_no=latest,
                   from_policy_digest=run["policy_digest"], to_policy_digest=snapshot["digest"])
    run_store.append_event(run_id, seq, run_events.RUN_STATUS, payload, node_key=None,
                           from_status=run["status"], to_status=run["status"], cause="revision_adopted",
                           result_doc_id=None, canvas_state=None, actor_user_id=actor, reason=None)
    run_events.queue(group.get("project_id"), flow_id, run_events.RUN_STATUS, payload)
    run.update(revision_no=latest, policy_source=snapshot["source"], policy_set_id=snapshot["set_id"],
               policy_set_version=snapshot["set_version"], policy_digest=snapshot["digest"],
               last_event_seq=seq, step_node_keys=None)
    # The existing command path reloads this context and invokes frontier.resolve(command).
    return True
