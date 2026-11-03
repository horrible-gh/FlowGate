"""Current card context, authority and scoped journaled rejection preconditions.

This module does not rewind, integrate code, cancel workers or start a run.
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
    from flowgate.graph_time_machine import rejection_store
    context['recovery_generation'] = rejection_store.generation(flow_id, node_key)
    context["documents"] = {slot: ({**fingerprint(doc), "doc_review_status": doc.get("doc_review_status")}
                                    if doc else None) for slot, doc in docs.items()}
    return node, node_run, context, docs


def require_editable(flow_id, node_key, docs, *, recovery_operation=None):
    graph_service.require_writable(flow_id)
    engine_router.require_graph_editable(flow_id, engine_router.read_ownership(flow_id))
    if recovery_operation is None:
        editing.require_settings_editable(flow_id)
    else:
        from flowgate.graph_time_machine import operation
        if any(op['operation_id'] != recovery_operation for op in operation._unfinished(flow_id)):
            refuse('graph_time_machine_in_progress', flow_id=flow_id)
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
    """Assess the exact dependency scope before a badge can change (0126 T#3)."""
    from flowgate.graph_time_machine import document_rejection
    return document_rejection.assess(flow_id, node_key, docs)
