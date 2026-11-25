"""First-run adapters: the flow, its start card and each chat are named from their own content."""
from __future__ import annotations
import logging
from . import auto_title, activity, legacy_adapter, contracts as c

log = logging.getLogger(__name__)


def title_request(flow_id, locale="ko", action="chat", doc_id=None):
    # Naming has its own configured provider; executor replies no longer supply names.
    return ""


def submitted_title(token, title, doc_id=None, flow_title=None):
    """After a committed reply: queue naming of every target from its own content."""
    flow_id = token.get("group_id")
    if not flow_id or not legacy_adapter.has_graph_definition(flow_id):
        return
    run = (legacy_adapter.get_run_record(token.get("ai_run_id"))
           or legacy_adapter.persisted_naming_run(token.get("ai_run_id")) or {})
    provider = run.get("provider_id") or token.get("provider_id")
    user_id = token.get("issued_to")
    if not provider or not user_id:
        log.warning("flow_auto_title reason=reply_context_missing flow=%s", flow_id)
        return
    doc = legacy_adapter.get_document(doc_id) if doc_id else None
    chat_id = doc_id if doc and doc.get("type_code") == "CH" else None
    # Executor-supplied ``title`` / ``flow_title`` are ignored: every target (flow, start card,
    # the chat of this reply) is named by the configured naming provider from its own content.
    # Document reads and the model call must not delay delivery of a committed reply.
    _schedule(auto_title.generate_flow_names, flow_id, {"user_id": user_id}, provider, "", chat_id)


def notify(flow_id, targets=None, doc_id=None):
    """Post-commit hints; clients read authoritative values, never apply event titles."""
    from modules.flow_gate.api.v1.events.publisher import broadcast_event_nowait, FlowEvent
    from modules.flow_gate.api.v1.events.event_types import EventType
    group = legacy_adapter.get_group(flow_id)
    if not group:
        return
    broadcast_event_nowait(FlowEvent(event_type=EventType.GROUP_VIEW_REFRESH,
        payload={"group_id": flow_id, "reason": "first_names", "targets": targets or [],
                 **({"doc_id": doc_id} if doc_id else {})},
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
    # The flow and start card are named from their own content in the background. Queued
    # before the chat's own (waited-for) name so the three names are made at the same time.
    _schedule(auto_title.generate_flow_names, flow_id, dict(user), provider, "", doc_id, None, True, None,
              ("flow", "root"))
    result = auto_title.generate_flow_names(flow_id, user, provider, chat_doc_id=doc_id, kinds=("chat",))
    if result["targets"].get("chat", "unchanged") in ("failed", "pending", "unchanged"):
        raise c.ConversationError(409, c.ERR_CONVERSATION_AUTO_TITLE_UNAVAILABLE, "naming pending")
    saved = legacy_adapter.get_document(doc_id) or doc
    return {"ok": True, "doc_id": doc_id, "title": saved["title"], "updated_at": saved.get("updated_at")}


def run_context(flow_id, revision_no, provider_id=None):
    """Read the accepted revision's configured AI and bound root, never a guessed task."""
    from modules.flow_gate.storage import paths
    from modules.flow_gate.db import documents
    from modules.flow_gate.graph_workflow.store import definition_store, assignment_store
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    from flowgate.card_settings import stage_assignment
    from flowgate.ai_roles import resolve
    definition = definition_store.revision(flow_id, revision_no) or {}
    group = legacy_adapter.get_group(flow_id) or {}
    provider = provider_id
    for node in ([] if provider else definition.get("nodes", [])):
        if stage_assignment.enabled() and stage_assignment.role_card(node):
            # Current Designer roles live in role slots, not legacy run_ai settings.
            # A person/unavailable role must never fall back to a stale legacy AI.
            role = resolve.resolve_role(flow_id, node, "execution", project_id=group.get("project_id"))
            provider = role.provider_id if role.outcome == "ai" else None
        elif card_settings_ports.uses_ai(flow_id, node):
            plan = card_settings_ports.run_settings(flow_id, node)
            provider = plan.get("provider_id") or node.get("assignee_provider_id")
        if provider:
            break
    binding = assignment_store.root_binding(flow_id) or {}
    root = documents.get_by_id(binding.get("doc_id")) if binding.get("doc_id") else None
    if not binding.get("doc_id"):
        # A draft card can already hold a document before confirmation persists a
        # run binding. Reuse the naming target's resolution; never guess among roots.
        from .first_names_ports import root_binding_context
        _, preview = root_binding_context(flow_id)
        if preview.status in ("bindable", "inherited") and preview.doc_id:
            root = documents.get_by_id(preview.doc_id)
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
    return provider, text, None


def _run(flow_id, user_id, revision_no, provider_id=None):
    try:
        provider, text, chat_id = (run_context(flow_id, revision_no, provider_id) if provider_id
                                   else run_context(flow_id, revision_no))
        provider = provider_id or provider
        if provider:
            auto_title.generate_flow_names(flow_id, {"user_id": user_id}, provider, text, allow_fallback=True)
        else:
            log.info("flow naming skipped reason=provider_missing flow=%s", flow_id)
    except Exception as exc:
        log.warning("flow naming setup exception_type=%s", type(exc).__name__)


def _schedule(fn, *args):
    if not auto_title._first_names_slots.acquire(blocking=False):
        log.info("flow naming skipped reason=pool_full")
        return False
    try:
        future = auto_title._first_names_pool.submit(fn, *args)
        future.add_done_callback(lambda _: auto_title._first_names_slots.release())
        return True
    except Exception as exc:
        auto_title._first_names_slots.release()
        log.info("flow naming skipped reason=pool_unavailable exception_type=%s", type(exc).__name__)
        return False


def schedule_run(flow_id, user_id, revision_no):
    return _schedule(_run, flow_id, user_id, revision_no)


def schedule_completed(run):
    flow_id, user_id, provider = run.get("group_id"), run.get("issued_to"), run.get("provider_id")
    if not flow_id or not user_id or not provider:
        log.info("flow naming skipped reason=run_context_missing")
        return False
    return _schedule(_completed, flow_id, user_id, provider, run.get("doc_ref"))


def _completed(flow_id, user_id, provider, doc_id):
    if not legacy_adapter.has_graph_definition(flow_id):
        log.info("flow naming skipped reason=not_graph flow=%s", flow_id)
        return
    doc = legacy_adapter.get_document(doc_id) if doc_id else None
    if doc and doc.get("type_code") == "CH":
        # Reply append owns chat naming, including the first-reply requirement.
        log.info("flow naming skipped reason=reply_owns_chat doc=%s", doc_id)
        return
    from modules.flow_gate.graph_workflow.store import definition_store
    _run(flow_id, user_id, definition_store.latest_revision_no(flow_id), provider)


def _reply(doc_id, user_id, provider_id):
    try:
        doc = legacy_adapter.get_document(doc_id)
        if not doc:
            log.info("flow naming skipped reason=doc_missing doc=%s", doc_id)
            return
        if not doc.get("group_id") or not legacy_adapter.has_graph_definition(doc["group_id"]):
            log.info("flow naming skipped reason=not_graph doc=%s", doc_id)
            return
        auto_title.generate_flow_names(doc["group_id"], {"user_id": user_id}, provider_id, chat_doc_id=doc_id)
    except Exception as exc:
        log.warning("flow naming reply exception_type=%s", type(exc).__name__)


def schedule_reply(doc_id, turn):
    if turn.get("speaker") != "ai" or not str(turn.get("body") or "").strip():
        log.info("flow naming skipped reason=not_ai_reply doc=%s", doc_id)
        return False
    run_id = turn.get("source_run_id")
    # The append service supplies verified worker-token fields, never request body fields.
    # Bound token fields only fill gaps; non-None fields from the live or persisted run record win when present.
    token = turn.get("_naming_context") or {}
    bound_token = bool(run_id and token.get("ai_run_id") == run_id
                       and token.get("doc_ref") == doc_id)
    run = legacy_adapter.get_run_record(run_id) or legacy_adapter.persisted_naming_run(run_id) or {}
    if bound_token:
        run = {**{k: token[k] for k in ("issued_to", "provider_id", "doc_ref") if token.get(k)},
               **{k: v for k, v in run.items() if v is not None}}
    user_id, provider_id = run.get("issued_to"), run.get("provider_id")
    if not user_id or not provider_id or run.get("doc_ref") != doc_id:
        log.info("flow naming skipped reason=reply_context_missing doc=%s", doc_id)
        return False
    return _schedule(_reply, doc_id, user_id, provider_id)

