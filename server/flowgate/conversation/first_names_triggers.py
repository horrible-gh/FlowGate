"""First-run adapters: the flow, its start card and each chat are named from their own content."""
from __future__ import annotations
import logging
from . import auto_title, activity, legacy_adapter, contracts as c

log = logging.getLogger(__name__)


_REQUEST_RULE = {
    "ko": "실제 작업 사실에 기반한 30자 이내로 쓰고, 문서 번호·플로우 ID·카드 번호를 넣지 마세요.",
    "en": "Base it on actual task facts, at most 30 characters. Never include document IDs, flow IDs or card numbers.",
    "ja": "実際の作業内容に基づく30文字以内で書き、文書番号・フローID・カード番号を含めないでください。",
}
_REQUEST_TEXT = {
    "new": {
        "ko": "결과 제출 시 `flow_title`에 플로우 전체 작업을 나타내는 이름을 반드시 함께 보내세요. 플로우 이름으로만 쓰며, 카드와 채팅 이름은 각자 내용으로 따로 정합니다. 문서 제목 필드 `title`은 그대로 유지하세요.",
        "en": "When submitting the result, you must also send a name for the whole task of the flow in the `flow_title` field. It names only the flow; cards and chats are named from their own content. Preserve `title` as the document title.",
        "ja": "結果の送信時に `flow_title` でフロー全体の作業を表す名前を必ず送信してください。フロー名にのみ使い、カードとチャットの名前はそれぞれの内容で別に決めます。文書名フィールド `title` はそのまま維持してください。",
    },
    "edit": {
        "ko": "편집 제출 시 선택 필드 `title`에 플로우 전체 작업을 나타내는 이름을 보내세요. 플로우 이름으로만 씁니다.",
        "en": "When submitting the edit, send a name for the whole task of the flow in the optional `title` field. It names only the flow.",
        "ja": "編集の送信時に任意フィールド `title` でフロー全体の作業を表す名前を送信してください。フロー名にのみ使います。",
    },
    "chat": {
        "ko": "답변 제출 시 선택 필드 `title`에 이 채팅에서 다루는 내용을 나타내는 제목을 보내세요. 이 채팅의 이름으로만 씁니다.",
        "en": "When submitting your answer, send a title for what this chat is about in the optional `title` field. It names only this chat.",
        "ja": "回答の送信時に任意フィールド `title` でこのチャットの内容を表すタイトルを送信してください。このチャットの名前にのみ使います。",
    },
    "flow": {
        "ko": "선택 필드 `flow_title`에는 이 채팅 한 건이 아니라 플로우 전체 작업을 나타내는 이름을 보내세요. 플로우 이름으로만 씁니다.",
        "en": "In the optional `flow_title` field, send a name for the whole task of the flow, not just this chat. It names only the flow.",
        "ja": "任意フィールド `flow_title` には、このチャット1件ではなくフロー全体の作業を表す名前を送信してください。フロー名にのみ使います。",
    },
}
_REQUEST_NEW_DOC = {
    "ko": "문서 등록(new)의 `title`은 문서 제목이므로 이 용도로 바꾸지 마세요.",
    "en": "The `title` of document registration (new) is document metadata; preserve that document title.",
    "ja": "文書登録(new)の `title` は文書名なので、この用途で変更しないでください。",
}


def _wanted_names(flow_id, doc_id):
    """(flow still needs a name, chat still needs a name) for the next request."""
    from . import first_names_ports as p

    def read():
        _, found = p.targets(flow_id, None, root=False)
        names = p.ledger.load(flow_id).get("names", {})
        want_flow = p.eligible("flow", found["flow"]) and not names.get("flow", {}).get("candidate")
        want_chat = True
        if doc_id:
            try:
                chat = p.targets(flow_id, doc_id, chat_only=True)[1]["chat"]
                want_chat = (p.eligible("chat", chat)
                             and not names.get("chat:" + doc_id, {}).get("candidate"))
            except ValueError:
                want_chat = False
        return want_flow, want_chat
    return p.guard(flow_id, read)


def title_request(flow_id, locale="ko", action="chat", doc_id=None):
    """Naming guidance: the flow name (``flow_title``) and, in a chat, that chat's own title."""
    if action not in ("chat", "edit", "new"):
        return ""
    if not legacy_adapter.has_graph_definition(flow_id):
        return ""
    want_flow, want_chat = _wanted_names(flow_id, doc_id if action == "chat" else None)
    locale = str(locale or "ko").lower().split("-")[0]
    locale = locale if locale in _REQUEST_RULE else "ko"
    parts = []
    if action == "chat":
        if want_chat:
            parts.append(_REQUEST_TEXT["chat"][locale])
        if want_flow:
            parts.append(_REQUEST_TEXT["flow"][locale])
    elif want_flow:
        parts.append(_REQUEST_TEXT[action][locale])
    if not parts:
        return ""
    parts.append(_REQUEST_RULE[locale])
    if action == "chat":
        parts.append(_REQUEST_NEW_DOC[locale])
    return " ".join(parts)


def submitted_title(token, title, doc_id=None, flow_title=None):
    """A submitted ``title`` names only the chat it was posted in; ``flow_title`` only the flow."""
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
    chat_seed = auto_title.clean_flow_title(title) if chat_id else None
    flow_seed = auto_title.clean_flow_title(flow_title)
    user = {"user_id": user_id}
    seeded = tuple(k for k, seed in (("chat", chat_seed), ("flow", flow_seed)) if seed)
    if seeded:
        auto_title.generate_flow_names(flow_id, user, provider, "", chat_id, title=chat_seed,
                                       flow_title=flow_seed, kinds=seeded)
    rest = tuple(k for k in auto_title.NAME_KINDS if k not in seeded and (k != "chat" or chat_id))
    # Document reads and the local fallback must not delay delivery of a committed reply.
    _schedule(auto_title.generate_flow_names, flow_id, user, provider, "", chat_id, None, True, None, rest)


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
    result = auto_title.generate_flow_names(flow_id, user, provider, chat_doc_id=doc_id, kinds=("chat",))
    # The flow and start card are named from their own content in the background.
    _schedule(auto_title.generate_flow_names, flow_id, dict(user), provider, "", doc_id, None, True, None,
              ("flow", "root"))
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

