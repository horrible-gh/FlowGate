from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from modules.flow_gate import template_provision
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import templates as db_templates
from modules.flow_gate.documents.constants import WORK_PLAN_TYPE
from modules.flow_gate.services import (
    engine_recipe_service,
    remote_tool_service,
    test_command_service,
    tool_registry,
    tr_scope_service,
    work_plan_service,
)

from . import chat_content
from modules.flow_gate.services.prompt_blocks.blocks import documents as _documents_block

_logger = logging.getLogger(__name__)
from .locale import (FALLBACK_LOCALE, NOTICE_LINES, QUESTION_HELP_COPY, _AUTHORING_GUIDE_TITLES, _ITEM_NOTES, _TR_AUTHORING_GUIDE, _copy, normalize_locale)
from ..compat.tree import (GROUP_DOCUMENTS_LIMIT, GROUP_DOCUMENTS_MORE_LIMIT, HelpSupplierError, INVESTIGATION_TYPES, MUTATING_TYPES)

_DOCUMENT_CONTENT_STATES: tuple[tuple[str, str], ...] = (
    ("readable", "본문을 읽었다. front matter뿐이면 빈 문서이며 읽기 실패가 아니다"),
    ("file_path_missing", "문서의 파일 경로가 기록되지 않았다"),
    ("file_not_found", "파일 경로는 있으나 파일이 없다"),
    ("path_unresolved", "파일 경로를 저장소 안에서 해석할 수 없다"),
)


def _body_unreadable_rules() -> tuple[str, ...]:
    from modules.flow_gate.services import prompt_blocks   # noqa: PLC0415 - the rule owner, imported on use

    return prompt_blocks.detail("documents.unreadable")


ANSWERS_HELP_COPY: dict[str, dict[str, str]] = {
    "ko": {
        "summary": "답변 조회 = GET /document/{doc_id}의 answers (id, doc_id, Q, A).",
        "note": (
            "A=null은 미답변입니다. 답변을 지어내지 마십시오. 해당 문서의 answers만 반환하므로 "
            "triggered_by/target_id 참조 문서도 같은 경로로 조회하십시오. "
            "작업 토큰은 기존 문서 조회 권한을 사용하며 /q 아래 GET은 공식 작업자 조회 경로가 아닙니다."
        ),
    },
    "en": {
        "summary": "Read answers from GET /document/{doc_id}: answers (id, doc_id, Q, A).",
        "note": (
            "A=null means unanswered. Do not invent an answer. Only this document's answers are returned; "
            "read triggered_by/target_id reference documents through the same route. "
            "Worker tokens use existing document read permissions; GET under /q is not an official worker lookup route."
        ),
    },
    "ja": {
        "summary": "回答の参照 = GET /document/{doc_id} の answers (id, doc_id, Q, A)。",
        "note": (
            "A=null は未回答です。回答を作り上げないでください。この文書の answers のみを返すため、"
            "triggered_by/target_id の参照文書も同じ経路で取得してください。"
            "作業トークンは既存の文書閲覧権限を使います。/q 配下の GET は公式の作業者用参照経路ではありません。"
        ),
    },
}


def _answers_lookup(ctx: dict) -> dict:
    copy = ANSWERS_HELP_COPY[normalize_locale(ctx.get("locale"))]
    return {
        "method": "GET",
        "url": f"{ctx['base_url']}/document/{{doc_id}}",
        "field": "answers",
        **copy,
    }


def _content_document_access(ctx: dict) -> dict:
    base = ctx["base_url"]
    doc_id = ctx.get("doc_id") or "{doc_id}"
    project_filter = f"&project={ctx['project']}" if ctx.get("project") else ""
    payload = {
        "body": {
            "method": "GET",
            "url": f"{base}/document/{{doc_id}}",
            "example": f"{base}/document/{doc_id}",
        },
        "flows": {
            "list": {"method": "GET", "url": f"{base}/list/projects/{{project}}/groups",
                     "summary": "List flows with their names."},
            "info": {"method": "GET", "url": f"{base}/list/groups/{{group_id}}",
                     "summary": "Read flow name, module, status, dates and document_count."},
            "permission": "The requesting user must have document read permission on the target project. Unreadable and missing targets return 404. Other flow canvases and writes remain restricted.",
        },
        "answers": _answers_lookup(ctx),
        "path": {"method": "GET", "url": f"{base}/document/{{doc_id}}/path"},
        # Bounded reads. The full-body GET is the expensive one; these
        # exist so a worker can read only the part it needs.
        "partial": {
            "note": (
                "Read only the document data you need; use the full-document GET only "
                "when necessary."
            ),
            "meta": {"method": "GET", "url": f"{base}/document/{{doc_id}}/meta",
                     "summary": "Metadata without the body."},
            "outline": {"method": "GET", "url": f"{base}/document/{{doc_id}}/outline",
                        "summary": "Section outline without the body."},
            "section": {"method": "GET",
                        "url": f"{base}/document/{{doc_id}}/section?section_id=<section_id>",
                        "summary": "One section from that outline.",
                        "rule": _documents_block.SECTION_READ_RULE},
            "relations": {"method": "GET", "url": f"{base}/document/{{doc_id}}/relations",
                          "summary": "Relationships without the body."},
            "content_search": {
                "method": "GET",
                "url": (f"{base}/search/documents/content?q=<keyword>{project_filter}"
                        + _documents_block.CONTENT_SEARCH_OPTIONS),
                "summary": "Search bodies and receive match locations with context. CH documents not yet migrated retain document-body/title/id search, document totals and default detailed matches until migration completes.",
                "parameters": {
                    "doc_id": "Restrict to one conversation; lifts the per-conversation turn cap and enables cursor paging.",
                    "group_id": "Restrict to one flow; mutually exclusive with doc_id. Group/global turn results remain capped without cursor paging.",
                    "include_matches": "Single migrated CH doc_id: defaults to false (compact); true adds match_total/matches with context and locators. Other searches default to true.",
                    "sort": "Turn order: desc (default, newest first) or asc (oldest first).",
                    "cursor": "With a CH doc_id, pass next_cursor unchanged using the same search conditions. Non-conversation documents/types, changed conditions or malformed cursors return 400.",
                },
                "response": {
                    "items": "Single migrated CH doc_id without include_matches=true: only seq, speaker, display_name, created_at, snippet (about 150 characters). Otherwise document-body results followed by conversation_turn results (no separate turn_items key).",
                    "turn_read": "Single migrated CH doc_id: GET /flowgate/api/v1/conversation/{doc_id}/turns?after_seq=<seq-1>&through_seq=<seq>&limit=1; substitute the result seq to read one turn. doc_id appears once at the top level in compact results.",
                    "context_lines/hits_per_doc": "Single migrated CH doc_id: omitted unless include_matches=true, then reflect clamped values actually applied.",
                    "turn_total": "Number of conversation-turn results returned on this page, not the total across all pages.",
                    "next_cursor": "Cursor for the next conversation-turn page; null when there is no next page.",
                    "total": "Single migrated CH doc_id: actual matching turn count across all pages using the same filters and max_seq snapshot, including cursor pages. Other searches retain document-body total/offset/limit semantics.",
                },
            },
        },
        "note": _copy(_ITEM_NOTES, ctx["locale"], "document_access"),
        # 0064 T-B1e (P0006 §2.9 ③): what each ``content_state`` of a document read means, and the unread-body
        # rules -- the Flow node AI reads them here through the help index (the same constant as the Q&A mention).
        "content_state": dict(_DOCUMENT_CONTENT_STATES),
        "rules": list(_body_unreadable_rules()),
    }
    if ctx.get("action_scope") == "chat":
        payload["conversation_history"] = {
            "forward": {
                "method": "GET",
                "url": f"{base}/conversation/{doc_id}/turns?after_seq=<seq>&include_head=1",
                "continue_with": "next_after_seq",
            },
            "backward": {
                "method": "GET",
                "url": f"{base}/conversation/{doc_id}/turns?before_seq=<seq>",
                "continue_with": "prev_before_seq",
            },
            "boundary": (
                "after_seq and before_seq are mutually exclusive. If an invocation starts at "
                "after_seq=<start>, older context begins with before_seq=<start+1> because the "
                "backward query returns seq < before_seq."
            ),
            "token": "Backward conversation reads do not consume the token.",
            "cursor": "Backward conversation reads do not advance the forward participant last_read cursor.",
        }
    return payload


def _attachment_permission_table() -> dict:
    """Which operations each kind may call, derived from the one judge.

    This used to be a literal table here -- a second copy of the read/read_write rule the
    worker routes enforce, free to drift from them. It is now read out of
    tool_registry.ATTACHMENT_KINDS, the same table /help/tools and the routes consult.
    """
    return {
        kind: [name.replace("attachment_", "", 1) for name in tool_registry.attachment_names(kind)]
        for kind in ("read", "read_write", "none")
    }


def _content_document_attachments(ctx: dict) -> dict:
    """Reuses the same tool_registry kind ctx already carries
    (resolve_context calls tool_registry.resolve_registry once per request), so this
    item can never advertise a permission the worker route itself would refuse.
    """
    base = ctx["base_url"]
    doc_id = ctx.get("doc_id") or "{doc_id}"
    kind = ctx.get("tool_kind") or "none"
    table = _attachment_permission_table()
    view = tool_registry.attachment_view(kind, ctx["locale"], base)
    content = {
        "list": {"method": "GET", "url": f"{base}/document/{{doc_id}}/attachments",
                 "example": f"{base}/document/{doc_id}/attachments"},
        "read": {"method": "GET", "url": f"{base}/document/{{doc_id}}/attachments/{{name}}/read"},
        "copy": {
            "method": "POST",
            "url": f"{base}/document/{{doc_id}}/attachments/{{name}}/copy",
            "headers": {"Authorization": "Bearer <YOUR_TOKEN>", "Content-Type": "application/json"},
            # Destination is always the caller's own group worktree --
            # there is no group_id field to steer it elsewhere, unlike the Console contract.
            "body": {
                "target_path": "<path inside the source tree, e.g. assets/schema.json>",
            },
        },
        # Hand a produced file to a person for approval as an
        # attachment of this worker's own card. Answers at once; never wait for the decision.
        "upload_request": {
            "method": "POST",
            "url": f"{base}/attachment-requests",
            "headers": {"Authorization": "Bearer <YOUR_TOKEN>", "Content-Type": "multipart/form-data"},
            "form": {"file": "@<file you produced>", "meta": '{"filename": "report.md", "note": "what it is"}'},
            "returns": "status=pending at once; a person approves or rejects later (maybe after this run ends)",
        },
        "permission": {
            "kind": kind,
            "allowed": table.get(kind, []),
            "by_kind": table,
        },
        # What a work token CANNOT do with an attachment, said out loud rather than left
        # to be inferred from the three keys above (the same rule applied to the capability
        # list itself): uploading and deleting are Console-screen actions.
        "absent": view["absent"],
        "note": _copy(_ITEM_NOTES, ctx["locale"], "document_attachments"),
    }

    if ctx.get("action_scope") == "chat":
        from modules.flow_gate.services import prompt_blocks
        content["chat"] = {"sections": [prompt_blocks.detail("attachments.upload_request", base, "<YOUR_CHAT_TOKEN>")]}
    return content


_CONTENT_SUPPLIERS = {
    "document_access": _content_document_access,
    "document_attachments": _content_document_attachments,
}

_CONTENT_SUPPLIERS.update({name: (lambda ctx, name=name: chat_content.content(name, ctx)) for name in ["chat_turn","chat_documents"]})
