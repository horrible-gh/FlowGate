"""First-run adapters. Both entry points share auto_title's durable claim."""
from __future__ import annotations
import logging
from . import auto_title, activity, legacy_adapter, contracts as c

log = logging.getLogger(__name__)


def notify(flow_id):
    """Post-commit hints; clients read authoritative values, never apply event titles."""
    from modules.flow_gate.api.v1.events.publisher import broadcast_event_nowait, FlowEvent
    from modules.flow_gate.api.v1.events.event_types import EventType
    group = legacy_adapter.get_group(flow_id)
    if not group:
        return
    broadcast_event_nowait(FlowEvent(event_type=EventType.GROUP_VIEW_REFRESH,
        payload={"group_id": flow_id, "reason": "first_names"},
        project=group["project_id"], group_id=flow_id, audience="*"))


def chat(doc_id, user, body):
    provider = auto_title.validate_input(body)
    doc = activity.readable_chat_document(doc_id, user)
    flow_id = doc.get("group_id")
    # Preserve the standalone/legacy title contract outside Graph Flows.
    if not flow_id or not legacy_adapter.has_graph_definition(flow_id):
        return auto_title.generate(doc_id, user, body)
    if not legacy_adapter.has_permission(user, auto_title.UPDATE_PERMISSION, doc["project_id"]):
        raise c.ConversationError(403, c.ERR_ACTIVITY_FORBIDDEN, "no update permission")
    if legacy_adapter.is_group_disposed(flow_id):
        raise c.ConversationError(409, c.ERR_CONVERSATION_GROUP_DISPOSED, "disposed flow")
    if not any(t.get("speaker") == "ai" and str(t.get("body") or "").strip()
               for t in legacy_adapter.first_turns(doc_id, 20)):
        raise c.ConversationError(409, c.ERR_CONVERSATION_AUTO_TITLE_NO_MESSAGE, "no AI reply yet")
    result = auto_title.generate_flow_names(flow_id, user, provider, chat_doc_id=doc_id)
    if result["status"] in ("failed", "pending", "unchanged"):
        raise c.ConversationError(409, c.ERR_CONVERSATION_AUTO_TITLE_UNAVAILABLE, "naming pending")
    saved = legacy_adapter.get_document(doc_id) or doc
    return {"ok": True, "doc_id": doc_id, "title": saved["title"], "updated_at": saved.get("updated_at")}


def run_context(flow_id, revision_no):
    """Read the accepted revision's configured AI and bound root, never a guessed task."""
    from modules.flow_gate.storage import paths
    from modules.flow_gate.db import documents
    from modules.flow_gate.graph_workflow.store import definition_store, assignment_store
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    definition = definition_store.revision(flow_id, revision_no) or {}
    provider = None
    for node in definition.get("nodes", []):
        if card_settings_ports.uses_ai(flow_id, node):
            plan = card_settings_ports.run_settings(flow_id, node)
            provider = plan.get("provider_id") or node.get("assignee_provider_id")
            if provider:
                break
    binding = assignment_store.root_binding(flow_id) or {}
    root = documents.get_by_id(binding.get("doc_id")) if binding.get("doc_id") else None
    text = ""
    if root:
        path, _ = paths.locate_storage_path(root.get("file_path") or "", root["project_id"],
                                           branch=root.get("branch") or "main")
        if path:
            text = path.read_text(encoding="utf-8")
            # Metadata alone is not task content.
            if text.startswith("---"):
                parts = text.split("---", 2)
                text = parts[2] if len(parts) == 3 else ""
    group = legacy_adapter.get_group(flow_id)
    chats = documents.list_documents(group["project_id"], group_id=flow_id, type_code="CH", limit=100)
    live = [doc for doc in chats if doc.get("status") != "cancelled" and not doc.get("superseded_by")]
    # Stable first conversation; subsequent chats never replace the ledger's first target.
    first = min(live, key=lambda d: (d.get("created_at") or "", d["doc_id"])) if live else None
    return provider, text, first["doc_id"] if first else None


def _run(flow_id, user_id, revision_no):
    try:
        provider, text, chat_id = run_context(flow_id, revision_no)
        if provider:
            auto_title.generate_flow_names(flow_id, {"user_id": user_id}, provider, text, chat_id)
    except Exception as exc:
        log.warning("flow naming setup exception_type=%s", type(exc).__name__)


def schedule_run(flow_id, user_id, revision_no):
    # Reuse the same bounded pool, including context reads; the run response never waits.
    if not auto_title._first_names_slots.acquire(blocking=False):
        return False
    try:
        future = auto_title._first_names_pool.submit(_run, flow_id, user_id, revision_no)
        future.add_done_callback(lambda _: auto_title._first_names_slots.release())
        return True
    except Exception:
        auto_title._first_names_slots.release()
        return False


def _reply(doc_id, user_id, provider_id):
    try:
        doc = legacy_adapter.get_document(doc_id)
        if doc and doc.get("group_id") and legacy_adapter.has_graph_definition(doc["group_id"]):
            auto_title.generate_flow_names(doc["group_id"], {"user_id": user_id}, provider_id, chat_doc_id=doc_id)
    except Exception as exc:
        log.warning("flow naming reply exception_type=%s", type(exc).__name__)


def schedule_reply(doc_id, turn):
    if turn.get("speaker") != "ai" or not str(turn.get("body") or "").strip():
        return False
    run = legacy_adapter.get_run_record(turn.get("source_run_id")) or {}
    user_id, provider_id = run.get("issued_to"), run.get("provider_id")
    if not user_id or not provider_id or run.get("doc_ref") != doc_id:
        return False
    if not auto_title._first_names_slots.acquire(blocking=False):
        return False
    try:
        future = auto_title._first_names_pool.submit(_reply, doc_id, user_id, provider_id)
        future.add_done_callback(lambda _: auto_title._first_names_slots.release())
        return True
    except Exception:
        auto_title._first_names_slots.release()
        return False
