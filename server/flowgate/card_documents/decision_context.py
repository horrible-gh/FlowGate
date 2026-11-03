"""0126 T#1: current card context, authority and fail-closed recovery boundary.

T#3 must replace ``require_rejection_safe`` with journaled invalidation before any badge
write. This module does not rewind, integrate code, cancel workers or start a run.
"""
from __future__ import annotations

from modules.flow_gate.db import users
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services import engine_router, graph_service
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import definition_store, run_store
from flowgate.card_documents import lines, slots
from flowgate.card_settings import legacy_adapter as settings_legacy, repository as settings_store, review_stage
from flowgate.card_settings.report_baseline import fingerprint
from flowgate.graph_run import editing
from . import decision_repository as repository


def refuse(code, **params):
    raise GraphApiError(409, code, params, message=code.replace("_", " "))


def permissions(user_id):
    # Same actual action authority as the existing document approve/reject routes;
    # project write alone and client-supplied actor/admin flags never grant approval.
    from modules.flow_gate.workflow.routers.workflow import _get_user_permissions
    user = users.get_by_id(user_id)
    if not user or not user.get("is_active"):
        return set()
    return _get_user_permissions(user)


def authorize(user_id, group, action):
    envelope.authorize(user_id, group, "write")
    if "document." + action not in permissions(user_id):
        raise GraphApiError(403, "document_decision_forbidden", {"action": action},
                            message="Document decision permission is required.")


def current(flow_id, node_key):
    run = run_store.current_run(flow_id)
    revision_no = run["revision_no"] if run else None
    definition = definition_store.revision(flow_id, revision_no) if run else definition_store.draft(flow_id)
    if definition is None and not run:
        revision_no = definition_store.latest_revision_no(flow_id)
        definition = definition_store.revision(flow_id, revision_no) if revision_no else None
    node = next((n for n in (definition or {}).get("nodes", []) if n["node_key"] == node_key), None)
    if node is None:
        raise GraphApiError(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key})
    rows = run_store.node_runs(run["run_id"]) if run else []
    node_run = next((r for r in rows if r["node_key"] == node_key), None)
    attempt = run_store.current_attempts(run["run_id"]).get(node_key) if run else None
    context = {"run_id": run["run_id"] if run else None, "attempt_id": attempt,
               "graph_revision_no": revision_no,
               "draft_version": definition.get("draft_version") if not run else None,
               "settings_version": settings_store.settings_version(flow_id, node_key)}
    docs = {}
    for slot in slots.approval_slots(node.get("node_type"), node.get("doc_type")):
        line = lines.line_document(flow_id, node_key, slot.slot, node.get("node_type"), node.get("doc_type"))
        doc = (line or {}).get("document")
        if doc and (doc.get("group_id") != flow_id or not lines.legacy.is_alive(doc, flow_id)):
            refuse("document_slot_mismatch", slot=slot.slot)
        docs[slot.slot] = doc
    context["documents"] = {slot: ({**fingerprint(doc), "doc_review_status": doc.get("doc_review_status")}
                                    if doc else None) for slot, doc in docs.items()}
    return node, node_run, context, docs


def require_editable(flow_id, node_key, docs):
    graph_service.require_writable(flow_id)
    engine_router.require_graph_editable(flow_id, engine_router.read_ownership(flow_id))
    editing.require_settings_editable(flow_id)
    if not repository.available():
        refuse("document_decisions_unavailable")
    store = get_store()
    busy = store._fetch_one(
        "SELECT x.exec_id FROM graph_attempt_runs x JOIN graph_runs r ON r.run_id = x.run_id "
        "JOIN graph_node_attempts a ON a.attempt_id = x.attempt_id "
        "WHERE r.group_id = ? AND a.node_key = ? AND x.status IN ('requested','running') LIMIT 1",
        [flow_id, node_key])
    if busy or settings_legacy.node_running(flow_id, node_key) or any(
            review_stage.ai_working(flow_id, node_key, doc["doc_id"]) for doc in docs.values() if doc):
        refuse("document_decision_locked", flow_id=flow_id, node_key=node_key, reason="card_ai_working")


def require_rejection_safe(flow_id, node_key, docs):
    """T#3 integration point. Until atomic recovery exists, refuse BEFORE any mutation.

    Check durable effects even if the run is no longer active or its UI status is stale.
    Independent nodes are never blocked by this node-scoped check.
    """
    store = get_store()
    checks = [
        ("SELECT 1 FROM graph_node_runs n JOIN graph_runs r ON r.run_id = n.run_id "
         "WHERE r.group_id = ? AND n.node_key = ? AND n.status = 'done'", [flow_id, node_key]),
        ("SELECT 1 FROM graph_card_completions WHERE group_id = ? AND node_key = ? "
         "AND status IN ('recorded','applied')", [flow_id, node_key]),
        ("SELECT 1 FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
         "WHERE r.group_id = ? AND a.node_key = ? AND (a.integrated_commit IS NOT NULL "
         "OR (a.integration_status IS NOT NULL AND a.integration_status NOT IN ('','none')))", [flow_id, node_key]),
        ("SELECT 1 FROM tr_commit_ledger WHERE group_id = ? AND node_key = ? AND commit_sha IS NOT NULL",
         [flow_id, node_key]),
    ]
    if any(store._fetch_one(sql + " LIMIT 1", params) for sql, params in checks):
        refuse("document_rejection_recovery_required", flow_id=flow_id, node_key=node_key, continuation="T#3")
    # TS admission and TR2 apply have their own effects even without a Graph completion.
    for doc in docs.values():
        if not doc:
            continue
        has_effect = doc.get("type_code") in ("TS", "TR2") and doc.get("doc_review_status") in ("approved", "wf_done")
        if doc.get("type_code") == "TS":
            has_effect = has_effect or store._fetch_one("SELECT 1 FROM test_runs WHERE doc_id = ? LIMIT 1", [doc["doc_id"]])
        if doc.get("type_code") == "TR2":
            from modules.flow_gate.db import tr2_approval_attempts
            has_effect = has_effect or tr2_approval_attempts.latest_success(doc["doc_id"])
        if has_effect:
            refuse("document_rejection_recovery_required", doc_id=doc["doc_id"], continuation="T#3")
