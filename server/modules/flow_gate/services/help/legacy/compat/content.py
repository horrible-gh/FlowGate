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

def _content_group_documents(ctx: dict) -> dict:
    group_id = ctx.get("group_id")
    if not group_id:
        raise HelpSupplierError("work token has no group_id")
    try:
        rows = db_documents.get_documents_by_group_id(group_id)
    except Exception as exc:  # storage failure, not a permission failure
        raise HelpSupplierError(f"group document lookup failed: {group_id}") from exc

    def sort_key(row: dict):
        try:
            seq = int(row.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        return (seq, str(row.get("doc_id") or ""))

    rows = sorted(rows, key=sort_key)
    total = len(rows)
    shown = rows[-GROUP_DOCUMENTS_LIMIT:] if total > GROUP_DOCUMENTS_LIMIT else rows

    more_url = None
    if total > GROUP_DOCUMENTS_LIMIT and shown:
        oldest = shown[0].get("doc_id")
        more_url = (
            f"{ctx['base_url']}/list/groups/{group_id}/documents"
            f"?before={oldest}&limit={GROUP_DOCUMENTS_MORE_LIMIT}"
        )

    return {
        "group_id": group_id,
        "total": total,
        "limit": GROUP_DOCUMENTS_LIMIT,
        "more_url": more_url,
        "documents": [
            {
                "doc_id": row.get("doc_id"),
                "type": row.get("type_code") or row.get("type"),
                "title": row.get("title"),
                "status": row.get("status"),
            }
            for row in shown
        ],
    }


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


def _content_doc_type(ctx: dict) -> dict:
    try:
        rows = db_templates.list_document_types(project_id=None, locale=ctx["locale"])
    except Exception as exc:
        raise HelpSupplierError("document type lookup failed") from exc
    return {
        "types": [
            {
                "type_code": row["type_code"],
                "name": row["type_name"],
                "series": row["series"],
                "description": row.get("description"),
            }
            for row in rows
            if row.get("is_active", 1)
        ]
    }


def _child_design_template(child: str, ctx: dict) -> dict:
    locale = ctx["locale"]
    project = ctx.get("project")
    if str(child or "").upper() == WORK_PLAN_TYPE:
        # A work plan body is JSON, so it has no Markdown skeleton to resolve, no
        # project override and no template row — the canonical shape comes from the
        # same service that validates it, which is why it cannot drift from the rules.
        content = work_plan_service.template_payload(locale, project)
        return {
            "name": "design_template",
            "child": WORK_PLAN_TYPE,
            "form": "content",
            "content": content,
        }
    try:
        resolved = template_provision.resolve_active_template(project, child, locale)
        meta = template_provision.resolve_active_meta(project, child, locale)
        rendered = template_provision.render_provision_block(child, locale, resolved)
    except template_provision.UnknownDesignType:
        raise
    except Exception as exc:
        raise HelpSupplierError(f"template resolution failed for {child}") from exc

    # render_provision_block emits `heading, *provenance badges, "", body`; split at
    # the blank line so the caller gets the badge text without re-deriving the copy.
    head_lines: list[str] = []
    for line in rendered.split("\n"):
        if line == "":
            break
        head_lines.append(line)

    notes = [_copy(_ITEM_NOTES, locale, "design_template_body")]
    if meta["resolved_locale"] and meta["resolved_locale"] != meta["requested_locale"]:
        notes.append(_copy(_ITEM_NOTES, locale, "design_template_fallback"))

    return {
        "name": "design_template",
        "child": child,
        "form": "content",
        "content": {
            "type_code": child,
            # P0009 §6: every template used to be Markdown, so its format never had to be
            # stated. WP is JSON, so the format is now said out loud on BOTH branches —
            # a worker must not have to infer it from the body it happens to receive.
            "body_format": "markdown",
            "requested_locale": meta["requested_locale"],
            "resolved_locale": meta["resolved_locale"],
            "resolution": meta["resolution"],
            "scope": meta["scope"],
            "available_locales": meta["available_locales"],
            "bytes": meta["bytes"],
            "template_id": resolved["resolved_template_id"],
            "heading": head_lines[0] if head_lines else "",
            "provenance": "\n".join(head_lines[1:]) or None,
            "body": resolved["content"],
            "rendered": rendered,
        },
        "notes": notes,
    }


_CONTENT_SUPPLIERS = {
    "group_documents": _content_group_documents,
    "document_access": _content_document_access,
    "doc_type": _content_doc_type,
}

_CONTENT_SUPPLIERS.update({name: (lambda ctx, name=name: chat_content.content(name, ctx)) for name in []})
