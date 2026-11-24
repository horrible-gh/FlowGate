"""First-run adapters. Both entry points share auto_title's durable claim."""
from __future__ import annotations
import logging
from . import auto_title, activity, legacy_adapter, contracts as c

log = logging.getLogger(__name__)


def title_request(flow_id, locale="ko", action="chat"):
    from . import first_names_ports as p
    # new.title is document metadata; reviews/preflight cannot submit a title.
    if action not in ("chat", "edit"):
        return ""
    if not legacy_adapter.has_graph_definition(flow_id):
        return ""
    if p.guard(flow_id, lambda: p.ledger.load(flow_id).get("candidate")):
        return ""
    locale = str(locale or "ko").lower().split("-")[0]
    return {
        "ko": "답변 제출 시 선택 필드 `title`에 실제 작업 사실에 기반한 짧은 작업 제목을 보내세요. 플로우·시작 카드·채팅의 공통 이름으로 사용합니다. 문서 등록(new)의 `title`은 문서 제목이므로 이 용도로 바꾸지 마세요.",
        "ja": "回答の送信時に任意フィールド `title` で実際の作業内容に基づく短い作業名を送信してください。フロー・開始カード・チャットの共通名に使います。文書登録(new)の `title` は文書名なので、この用途で変更しないでください。",
        "en": "When submitting your answer, send a short task title based on actual task facts in the optional `title` field. It names the flow, start card and chat together. The `title` of document registration (new) is document metadata; preserve that document title.",
    }.get(locale, "답변의 선택 필드 `title`에 실제 작업 사실에 기반한 짧은 작업 제목을 보내세요. 문서 등록(new)의 `title`은 문서 제목으로 유지하세요.")

def submitted_title(token, title, doc_id=None):
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
    chat_id = doc_id if doc and doc.get("type_code") == "CH" else legacy_adapter.naming_chat(flow_id)
    # Read actual task facts for the single local fallback, never the report header.
    message = ""
    if not chat_id:
        from modules.flow_gate.graph_workflow.store import definition_store
        _, message, _ = run_context(flow_id, definition_store.latest_revision_no(flow_id), provider)
    cleaned = auto_title.clean_title(title, auto_title.TITLE_MAX_CHARS) if isinstance(title, str) else None
    if cleaned:
        auto_title.generate_flow_names(flow_id, {"user_id": user_id}, provider, message,
                                       chat_id, title=cleaned)
    else:
        # A local fallback must not delay delivery of an already committed reply.
        _schedule(auto_title.generate_flow_names, flow_id, {"user_id": user_id}, provider, message, chat_id)


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
    result = auto_title.generate_flow_names(flow_id, user, provider, chat_doc_id=doc_id)
    if result["status"] in ("failed", "pending", "unchanged"):
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
            auto_title.generate_flow_names(flow_id, {"user_id": user_id}, provider, text, allow_fallback=False)
        else:
            log.debug("flow naming skipped reason=provider_missing flow=%s", flow_id)
    except Exception as exc:
        log.warning("flow naming setup exception_type=%s", type(exc).__name__)


def _schedule(fn, *args):
    if not auto_title._first_names_slots.acquire(blocking=False):
        log.debug("flow naming skipped reason=pool_full")
        return False
    try:
        future = auto_title._first_names_pool.submit(fn, *args)
        future.add_done_callback(lambda _: auto_title._first_names_slots.release())
        return True
    except Exception as exc:
        auto_title._first_names_slots.release()
        log.debug("flow naming skipped reason=pool_unavailable exception_type=%s", type(exc).__name__)
        return False


def schedule_run(flow_id, user_id, revision_no):
    return _schedule(_run, flow_id, user_id, revision_no)


def schedule_completed(run):
    flow_id, user_id, provider = run.get("group_id"), run.get("issued_to"), run.get("provider_id")
    if not flow_id or not user_id or not provider:
        log.debug("flow naming skipped reason=run_context_missing")
        return False
    return _schedule(_completed, flow_id, user_id, provider, run.get("doc_ref"))


def _completed(flow_id, user_id, provider, doc_id):
    if not legacy_adapter.has_graph_definition(flow_id):
        log.debug("flow naming skipped reason=not_graph flow=%s", flow_id)
        return
    doc = legacy_adapter.get_document(doc_id) if doc_id else None
    if doc and doc.get("type_code") == "CH":
        # Reply append owns chat naming, including the first-reply requirement.
        return
    from modules.flow_gate.graph_workflow.store import definition_store
    _run(flow_id, user_id, definition_store.latest_revision_no(flow_id), provider)


def _reply(doc_id, user_id, provider_id):
    try:
        doc = legacy_adapter.get_document(doc_id)
        if doc and doc.get("group_id") and legacy_adapter.has_graph_definition(doc["group_id"]):
            auto_title.generate_flow_names(doc["group_id"], {"user_id": user_id}, provider_id, chat_doc_id=doc_id)
    except Exception as exc:
        log.warning("flow naming reply exception_type=%s", type(exc).__name__)


def schedule_reply(doc_id, turn):
    if turn.get("speaker") != "ai" or not str(turn.get("body") or "").strip():
        log.debug("flow naming skipped reason=not_ai_reply doc=%s", doc_id)
        return False
    run_id = turn.get("source_run_id")
    run = legacy_adapter.get_run_record(run_id) or legacy_adapter.persisted_naming_run(run_id) or {}
    token = turn.get("_naming_context") or {}
    if token.get("ai_run_id") == run_id and token.get("doc_ref") == doc_id:
        run = {**{k: token[k] for k in ("issued_to", "provider_id", "doc_ref") if token.get(k)},
               **{k: v for k, v in run.items() if v is not None}}
    user_id, provider_id = run.get("issued_to"), run.get("provider_id")
    if not user_id or not provider_id or run.get("doc_ref") != doc_id:
        log.debug("flow naming skipped reason=reply_context_missing doc=%s", doc_id)
        return False
    return _schedule(_reply, doc_id, user_id, provider_id)
