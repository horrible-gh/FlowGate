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

from modules.flow_gate.services import chat_help_content
from modules.flow_gate.services.prompt_blocks.blocks import documents as _documents_block

_logger = logging.getLogger(__name__)
from .locale import (FALLBACK_LOCALE, NOTICE_LINES, QUESTION_HELP_COPY, _AUTHORING_GUIDE_TITLES, _ITEM_NOTES, _TR_AUTHORING_GUIDE, _copy, normalize_locale)
from .tree import (GROUP_DOCUMENTS_LIMIT, GROUP_DOCUMENTS_MORE_LIMIT, HelpSupplierError, INVESTIGATION_TYPES, MUTATING_TYPES)

def _content_notices(ctx: dict) -> dict:
    keys: list[str] = []
    if ctx["continuous"]:
        keys += ["continuous_unattended", "continuous_no_stop", "continuous_autonomous"]
    else:
        keys.append("interactive_query_without_choice")
    if ctx.get("action_scope") == "review":
        keys.append("review_no_modify")
    if ctx.get("doc_type") in INVESTIGATION_TYPES:
        keys.append("investigation_only")
    if ctx.get("doc_type") in MUTATING_TYPES:
        keys.append("assigned_scope_only")
    # A key requested by two conditions still prints once.
    ordered = list(dict.fromkeys(keys))
    lines = [_copy(NOTICE_LINES, ctx["locale"], key) for key in ordered]
    if ctx.get("doc_type") == "TR":
        lines.append({
            "ko": "TR에서는 test code/fixture/mock을 작성하고 접근 가능한 환경에서 self-check할 수 있습니다. 공식 시험 PASS와 gate는 TS/TSR 단계가 담당하며 공식 시험을 위해 승인형 Snapshot을 요청하지 않습니다.",
            "en": "TR may create test code, fixtures, and mocks and run available self-checks. Formal PASS and the test gate belong to TS/TSR; do not request an approval-based Snapshot for formal testing.",
            "ja": "TRではテストコード・fixture・mockを作成し、利用可能な環境でself-checkできます。正式なPASSとgateはTS/TSRが担当し、正式試験のために承認型Snapshotを要求しません。",
        }.get(ctx["locale"], ""))
    from modules.flow_gate.services import question_policy
    token = ctx.get("token_rec") or {}
    policy = question_policy.block(question_policy.text(
        token.get("issued_to"), ctx.get("group_id"), card=ctx.get("action_scope") != "chat"))
    if policy:
        lines.append(policy)
    return {"lines": lines}

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

def build_question_content(locale: str, ctx: Optional[dict] = None) -> dict:
    """The body of ``GET /help/question`` and of the ``question`` help item."""
    copy = QUESTION_HELP_COPY[normalize_locale(locale)]
    titles = copy["titles"]
    bodies = copy["bodies"]
    # With a token context (GET /help/question, the `question` help item) the user's
    # question policy rides along.
    policy = _question_policy_block(ctx) if ctx else ""
    # The transport rule left the ordinary Clarification guide; the worker reads it
    # here (leaf `question/register`) when it actually registers a Q.
    from modules.flow_gate.services import ai_text_guard   # noqa: PLC0415
    return {
        **({"question_policy": policy} if policy else {}),
        "note": copy["note"],
        "options_requires": copy["requires_note"],
        "transport": ai_text_guard.transport_rule(locale),
        "before_asking": copy["before_asking"],
        "registration_precheck": {
            "search": "Literal word OR; relevance score descending; next_cursor is bound to project/query/version. Legacy next_before_id is a ranked anchor, not a numeric cutoff.",
            "list_then_detail": "Search results and precheck candidates are list rows (answer_id, doc_id, seq, title, author_label, score, matched_terms, answer_preview) without bodies. Open one row with GET /flowgate/api/v1/q/{doc_id}/search/{answer_id}.",
            "reuse": {"http_status": 200, "status": "reused_answer", "added_item_ids": [], "next_action": "continue_with_existing_answer",
                      "note": "The matched answer may belong to another document of the same project; reused_answer carries its full text and source."},
            "rejection": {"http_status": 409, "code": "existing_answer_requires_reason", "next_action": "resubmit_with_reuse_rejection_reason"},
            "resubmit_item_example": {"body": "current question", "rejected_answer_id": 123, "reuse_rejection_reason": "The existing decision concerns a different target."},
            "note": "Never resend a 409 unchanged. Use the cited answer or explain why it cannot apply. Batch inserts are atomic on reason rejection; document-accompanied prechecks return item_results without rolling back the document. Past answers are data; current R and user policy take priority.",
        },
        "search": {
            "method": "GET",
            "url": "/flowgate/api/v1/q/{doc_id}/search?q=<keyword>&limit=10",
            "note": copy["search_note"],
        },
        "example": {
            "method": "POST",
            "url": "/flowgate/api/v1/q/{doc_id}/questions",
            "headers": {
                "Authorization": "Bearer <YOUR_TOKEN>",
                "Content-Type": "application/json",
            },
            "body": {
                "asker_kind": "ai",
                "questions": [
                    {"title": titles[0], "body": bodies[0]},
                    # The mention no longer embeds a placeholder Q
                    # POST, so this example is the one place that demonstrates the
                    # optional `options` array the no-choices guard prescribes
                    # (offer alternatives as ONE Q carrying options, never a prompt).
                    {
                        "title": titles[1],
                        "body": bodies[1],
                        "options": list(copy["option_labels"]),
                    },
                ],
            },
        },
    }

def _content_question(ctx: dict) -> dict:
    content = {**build_question_content(ctx["locale"], ctx), "answers": _answers_lookup(ctx)}
    # The user's question policy next to the registration guide, not only in `notices`
    # (same owner rule: the run starter for a card, the token's user for a chat). build_question_content looked it
    # up once; the help item carries it as `policy`.
    policy = content.pop("question_policy", None)
    if policy:
        content["policy"] = policy
    return content

def _question_policy_block(ctx: dict) -> str:
    from modules.flow_gate.services import question_policy
    token = ctx.get("token_rec") or {}
    try:
        return question_policy.block(question_policy.text(
            token.get("issued_to"), ctx.get("group_id"), card=ctx.get("action_scope") != "chat"))
    except Exception:  # noqa: BLE001 — the policy is guidance; a lookup failure never breaks the help answer
        return ""

def _prev_doc_id(ctx: dict) -> Optional[str]:
    """The document the submission binds to — the chain target, not the last step."""
    doc_id = ctx.get("doc_id")
    if not doc_id:
        return None
    try:
        doc = db_documents.get_by_id(doc_id)
    except Exception:
        return doc_id
    if not doc:
        return doc_id
    return doc.get("target_id") or doc_id

def _module_and_group(ctx: dict) -> tuple[str, str]:
    parts = (ctx.get("group_id") or "").split(".", 2)
    if len(parts) == 3:
        return parts[1], ctx["group_id"]
    return "none", ctx.get("group_id") or ""

_ENCODING_PROBE_RULE = (
    "Make `line` the LAST line of the body file (content or doc_path), exactly as given. "
    "FlowGate checks it and removes it before saving. Intact = the path carried every "
    "Unicode character, so no corruption check runs on the body: any text in any language "
    "is fine, '??' included. Damaged = refused with the cause, even with "
    "force_encoding_reason; fix the transport, not the text: write the body to a UTF-8 file "
    "and post it with a script that reads the file and sends ASCII-only JSON (e.g. Python "
    "json.dumps defaults). Never put the body on a command line or through a console pipe, "
    "redirect, Set-Content or Out-File. If you send body_chars/body_sha256, compute them "
    "from that same file, probe line included."
)

def _encoding_probe_for(ctx: dict) -> Optional[str]:
    from modules.flow_gate.services import encoding_probe

    token_rec = ctx.get("token_rec") or {}
    if ctx.get("principal_kind") != "worker" or not token_rec.get("token_id"):
        return None
    return encoding_probe.probe_for(str(token_rec["token_id"]))

def _content_submit(ctx: dict) -> dict:
    base = ctx["base_url"]
    scope = ctx.get("action_scope")
    doc_id = ctx.get("doc_id")
    headers = {"Authorization": "Bearer <YOUR_TOKEN>", "Content-Type": "application/json"}
    dry_run = _copy(_ITEM_NOTES, ctx["locale"], "submit_dry_run")

    encoding_guard = _copy(_ITEM_NOTES, ctx["locale"], "submit_encoding_guard")

    if scope == "review":
        review_dry_run = _copy(_ITEM_NOTES, ctx["locale"], "submit_review_dry_run")
        return {
            "action_scope": scope,
            "method": "POST",
            "url": f"{base}/inbox",
            "headers": headers,
            "body": {
                "action": "review",
                "project": ctx.get("project"),
                "doc_id": doc_id,
                "verdict": "pass | issues | hold",
                "findings": [{"locus": "<where in the doc>", "note": "<what is wrong / to improve>"}],
                "comment": "<optional overall comment>",
                "body_sha256": "<optional: sha256 hex of comment, UTF-8 bytes>",
                "body_chars": "<optional: character count of comment>",
                "force_encoding_reason": "<optional: only if a genuinely-flagged comment must go through anyway>",
            },
            # A long Korean overall comment survives a file far better
            # than a command line, so the verdict may travel the same way a document does.
            "source_choice": (
                "verdict/findings/comment may travel inline (as above) or in a file. For the "
                "file form send `doc_path` instead — an absolute path inside this token's "
                "scratch directory holding a JSON object with those same keys. Sending both "
                "`doc_path` and `content` is rejected."
            ),
            "verdict_guide": {
                "pass": "meets requirements, no blocking issues",
                "issues": "defects found; list each one in findings (locus + note)",
                "hold": "cannot decide yet (missing context / blocked)",
            },
            "dry_run": review_dry_run,
            "encoding_guard": encoding_guard,
        }

    if scope == "workflow_decide":
        return {
            "action_scope": scope,
            "method": "POST",
            "url": f"{base}/workflow/decide",
            "headers": headers,
            "body": {
                "doc_id": doc_id,
                "doc_class": "R",
                "sequence": [{"id": 1, "type": "<TYPE_CODE>", "label": "<STEP_LABEL>"}],
                # A corrupted step label is rejected here now (it used
                # to be silently replaced by the type name), so this path needs the same
                # escape hatch as the others.
                "force_encoding_reason": "<optional: only if a genuinely-flagged label must go through anyway>",
            },
            "encoding_guard": encoding_guard,
        }

    if scope == "workflow_sequence_edit":
        return {
            "action_scope": scope,
            "method": "PATCH",
            "url": f"{base}/workflow/sequence",
            "headers": headers,
            "body": {
                "doc_id": doc_id,
                "items": [{
                    # An existing row sent back keeps its item_id; a
                    # new row has none. Protected rows are recognised by this id only.
                    "item_id": "<EXISTING_ROW_ITEM_ID_OR_OMIT_FOR_NEW_ROW>",
                    "type": "<TYPE_CODE>",
                    "label": "<STEP_LABEL>",
                    "note": "<UNCHANGED_NOTE_OR_EMPTY>",
                    "source_doc_id": None,
                    "source_revision_no": None,
                    # Omitting both provider keys keeps whatever is
                    # stored on the row; an explicit null is what clears it.
                    "provider_id": "<UNCHANGED_PROVIDER_ID_OR_NULL>",
                    "provider_display_name": None,
                }],
                "force_encoding_reason": "<optional: only if a genuinely-flagged label must go through anyway>",
            },
            "encoding_guard": encoding_guard,
            "guidance": _copy(
                _ITEM_NOTES, ctx["locale"], "submit_workflow_sequence_edit"
            ),
        }

    module, group_name = _module_and_group(ctx)
    doc_type = ctx.get("doc_type") or "<Sequence undecided>"
    if scope == "edit":
        body = {
            "action": "edit",
            "project": ctx.get("project"),
            "doc_id": doc_id,
            "edit_reason": "rejected | user_comment",
            "content": "<Complete revised document content>",
            "body_sha256": "<optional: sha256 hex of content, UTF-8 bytes>",
            "body_chars": "<optional: character count of content>",
            "force_encoding_reason": "<optional: only if a genuinely-flagged content must go through anyway>",
        }
        if str(doc_type).upper() == "TR":
            body["commit_message"] = "<TR edit: English (ASCII) single line, max 200 characters; type(scope): summary recommended. Omit only to retain a valid saved message; otherwise 422 invalid_commit_message.>"
    else:
        body = {
            "action": "new",
            "project": ctx.get("project"),
            "module": module,
            "group_name": group_name,
            "doc_type": doc_type,
            "prev_doc_id": _prev_doc_id(ctx),
            "title": "<Fill this in>",
            "content": "<Fill this in>",
            "body_sha256": "<optional: sha256 hex of content, UTF-8 bytes>",
            "body_chars": "<optional: character count of content>",
            "force_encoding_reason": "<optional: only if a genuinely-flagged content must go through anyway>",
        }
        if str(doc_type).upper() in tool_registry.MUTATING_STEP_TYPES:
            body["content"] = "<Fill this in>\n\n" + tr_scope_service.tr_section_placeholder(ctx["locale"])
            if str(doc_type).upper() == "TR":
                body["commit_message"] = (
                    "<This TR's own approval commit subject, used verbatim. Conventional "
                    "commit format: type(scope): summary — e.g. "
                    "'fix(git): preserve finalized commit subject' or "
                    "'feat(workflow): add TR commit point'. Must be one line of plain English (ASCII) "
                    "(at most 200 characters). Recommended format: type(scope): summary; "
                    "any non-empty ASCII single line is accepted. Missing, empty, non-ASCII, "
                    "multi-line or overlong messages are rejected with 422 before registration.>"
                )

    is_work_plan = str(doc_type).upper() == WORK_PLAN_TYPE
    if is_work_plan:
        body["content"] = "<Canonical work-plan JSON>"

    payload = {
        "action_scope": scope or "new",
        "method": "POST",
        "url": f"{base}/inbox",
        "headers": headers,
        "body": body,
        "source_choice": (
            "Send exactly one of `content` and `doc_path`. `doc_path` must be an absolute "
            "path inside this token's scratch directory."
        ),
        "dry_run": dry_run,
        "encoding_guard": encoding_guard,
    }
    probe = _encoding_probe_for(ctx)
    if probe and not is_work_plan:
        payload["encoding_probe"] = {"line": probe, "rule": _ENCODING_PROBE_RULE}
    if is_work_plan:
        payload["retirement"] = {
            "new_cards": False, "deployment": False,
            "guidance": "WP is retained for existing card attempts and historical documents only. Place new work cards through chat.",
        }
        payload["content_format"] = {
            "format": "canonical_json",
            "template_url": f"{base}/help/items/design_template/{WORK_PLAN_TYPE}",
            "guidance": _copy(_ITEM_NOTES, ctx["locale"], "submit_work_plan"),
        }
    if str(doc_type).upper() in tool_registry.MUTATING_STEP_TYPES and scope != "edit":
        payload["changed_files_required"] = True
    return payload

def _content_test_commands(ctx: dict) -> dict:
    project = ctx.get("project") or ""
    try:
        block = test_command_service.build_verified_commands_block(project) if project else ""
        # The engine-recipe
        # guidance the TS mention used to inline now rides in
        # this item, so the first help call still teaches the registry (L §2-7).
        engine_recipes = engine_recipe_service.build_engine_recipes_block(ctx["base_url"])
    except Exception as exc:
        raise HelpSupplierError("verified test command lookup failed") from exc
    return {
        "project": project,
        "host_os": test_command_service.current_os(),
        "shell": test_command_service.current_shell(),
        "has_commands": bool(block),
        "commands_block": block,
        "engine_recipes": engine_recipes,
    }

def _content_changed_files_format(ctx: dict) -> dict:
    locale = ctx["locale"]
    guide = tr_scope_service.tr_section_guide(locale)
    placeholder = tr_scope_service.tr_section_placeholder(locale)
    heading = placeholder.split("\n", 1)[0]
    return {
        "required": True,
        "heading": heading,
        "guide": guide,
        "placeholder": placeholder,
        "rule": _copy(_ITEM_NOTES, locale, "changed_files"),
        "example": (
            f"{heading}\n\n"
            "- server/modules/flow_gate/services/help_catalog.py\n"
            "- server/modules/flow_gate/api/v1/help_routes.py\n"
        ),
        "empty_case": f"{heading}\n\n{tr_scope_service._spelling_none(locale)}\n",
    }

SELF_CHECK_REQUEST_FIELDS: tuple[str, ...] = ("program", "args", "cwd", "timeout_seconds", "parallel_share")

SELF_CHECK_TIMEOUT_DEFAULT = 300

SELF_CHECK_TIMEOUT_MAX = 1800

SELF_CHECK_SHARE_DEFAULT = 1

SELF_CHECK_EXAMPLE: dict = {
    "program": "pytest",
    "args": ["-q", "server/tests/test_x.py"],
    "cwd": ".",
    "timeout_seconds": SELF_CHECK_TIMEOUT_DEFAULT,
}

SELF_CHECK_INVALID_EXAMPLE: dict = {"program": "pytest -q server/tests/test_x.py"}

SELF_CHECK_STATUSES = ("queued", "pending", "running", "completed", "failed", "cancelled")

SELF_CHECK_RESULT_FIELDS = (
    "exit_code", "timed_out", "stdout_tail", "stderr_tail",
    "source_changed_during_run", "worktree_state_changed", "error_code",
    # Evidence read from a normally completed run's result block (advisory).
    "result_summary", "environment_evidence",
)

SELF_CHECK_HTTP_BASE = "/flowgate/api/v1"

SELF_CHECK_HTTP_OPERATIONS: tuple[tuple[str, str, str, int], ...] = (
    ("run", "POST", "/documents/{tr_doc_id}/self-check/runs", 202),
    ("read", "GET", "/documents/{tr_doc_id}/self-check/runs/{run_id}", 200),
    ("cancel", "POST", "/documents/{tr_doc_id}/self-check/runs/{run_id}/cancel", 200),
    ("list", "GET", "/documents/{tr_doc_id}/self-check/runs", 200),
)

SELF_CHECK_DRAFT_HTTP_OPERATIONS: tuple[tuple[str, str, str, int], ...] = (
    ("run", "POST", "/self-check/draft/runs", 202),
    ("read", "GET", "/self-check/draft/runs/{run_id}", 200),
    ("cancel", "POST", "/self-check/draft/runs/{run_id}/cancel", 200),
    ("list", "GET", "/self-check/draft/runs", 200),
)

SELF_CHECK_GROUP_HTTP_OPERATIONS: tuple[tuple[str, str, str, int], ...] = (
    ("group_read", "GET", "/self-check/group/runs/{run_id}", 200),
    ("group_list", "GET", "/self-check/group/runs", 200),
)

_LOCALE_INDEX = {"ko": 1, "en": 2, "ja": 3}

_SELF_CHECK_ERRORS: tuple[tuple[str, str, str, str], ...] = (
    ("selfcheck_quota_exhausted", "검증 횟수를 모두 썼다. 마지막 실행 결과로 판정한다.",
     "The verification allowance is exhausted. Judge the last run's result.",
     "検証回数を使い切った。最後の実行結果で判定する。"),
    ("selfcheck_invalid_request",
     "요청이 객체가 아니거나 program/args/cwd/timeout_seconds/parallel_share 이외의 필드가 있다 → 이 다섯 필드만 보낸다.",
     "The request is not an object or carries a field other than program/args/cwd/timeout_seconds/parallel_share -> send only those five.",
     "リクエストがオブジェクトでない、または program/args/cwd/timeout_seconds/parallel_share 以外のフィールドがある → この5つのフィールドだけ送る。"),
    ("selfcheck_invalid_share",
     "parallel_share 가 1 이상의 정수가 아니다(상한 64) → 명령이 쓸 worker 수를 정수로 보내거나 생략한다(생략하면 1).",
     "parallel_share is not an integer of at least 1 (at most 64) -> send the worker count your command uses as an integer, or omit it (default 1).",
     "parallel_share が 1 以上の整数ではない(上限 64) → コマンドが使う worker 数を整数で送るか省略する(省略時は 1)。"),
    ("selfcheck_cpu_limit_violation",
     "failed run 의 error_code 다(POSIX 호스트). run 의 프로세스가 받은 처리기 밖으로 처리기 집합을 넓혀 트리를 끝냈다. 결과를 검증 근거로 쓰지 않는다 → 명령이 처리기 설정을 바꾸지 않게 하고 다시 실행한다.",
     "The error_code of a failed run (POSIX host): a process of the run widened its processor set beyond the granted processors and the tree was ended. Do not use its result as evidence -> keep the command from changing processor affinity and run it again.",
     "failed run の error_code(POSIX ホスト)。run のプロセスが割り当てられた処理器の外へ処理器集合を広げたためツリーを終了した。結果を検証の根拠に使わない → コマンドが処理器設定を変えないようにして再実行する。"),
    ("selfcheck_invalid_program",
     "program 이 실행 파일 이름 하나가 아니다(경로·구분자·제어문자 포함) → program 에는 실행 파일 이름만 넣고 옵션과 대상은 args 로 분리한다.",
     "program is not a single executable name (path, separator or control character) -> pass only the executable name in program and move options/targets to args.",
     "program が実行ファイル名1つではない(パス・区切り・制御文字を含む) → program には実行ファイル名だけを入れ、オプションと対象は args に分ける。"),
    ("selfcheck_invalid_args",
     "args 가 문자열 배열이 아니거나 개수(256)·길이(8192) 제한을 넘는다 → 문자열 배열로 줄여서 다시 보낸다.",
     "args is not an array of strings or exceeds the count (256) / length (8192) limit -> resend a shorter array of strings.",
     "args が文字列配列でない、または個数(256)・長さ(8192)の上限を超えている → 文字列配列に直して再送する。"),
    ("selfcheck_invalid_timeout",
     "timeout_seconds 는 양의 정수여야 한다. 생략하면 카드 한도를 쓴다.",
     "timeout_seconds must be a positive integer; omit for the card limit.",
     "timeout_seconds は正の整数。省略時はカードの上限を使う。"),
    ("selfcheck_shell_operator",
     "args 안에 |, &&, ;, >, < 같은 셸 연산자가 있다 → 명령을 하나씩 나누어 각각 별도 run 으로 실행한다.",
     "args contains a shell operator such as |, &&, ;, > or < -> split the work into separate runs, one command each.",
     "args に |, &&, ;, >, < などのシェル演算子がある → コマンドを1つずつ分け、別々の run で実行する。"),
    ("selfcheck_inline_execution",
     "python -c, node -e 같은 인라인 코드 실행이다 → 테스트 파일이나 스크립트 경로를 args 로 준다.",
     "Inline code execution such as python -c or node -e -> pass a test file or script path in args.",
     "python -c や node -e のようなインラインコード実行 → テストファイルやスクリプトのパスを args で渡す。"),
    ("selfcheck_program_denied",
     "정책이 금지한 실행 파일이다(셸, git, 네트워크·삭제·시스템 도구 등) → 테스트/린트/빌드용 실행 파일로 바꾼다. 우회를 시도하지 않는다.",
     "The executable is denied by policy (shells, git, network/delete/system tools, ...) -> use a test/lint/build executable instead; do not try to work around it.",
     "ポリシーで禁止された実行ファイル(シェル、git、ネットワーク・削除・システムツール等) → テスト/リント/ビルド用の実行ファイルに変える。回避は試みない。"),
    ("selfcheck_executable_not_found",
     "program 을 찾을 수 없다(설치되지 않았거나 'pytest -q ...' 처럼 명령 전체를 program 에 넣었다) → 이름만 정확히 쓰고 나머지는 args 로 옮긴다. 설치되지 않았다면 그 사실을 보고한다.",
     "program cannot be found (not installed, or the whole command such as 'pytest -q ...' was put in program) -> write the bare name and move the rest to args; if it is not installed, report that.",
     "program が見つからない(未インストール、または 'pytest -q ...' のようにコマンド全体を program に入れた) → 名前だけを正しく書き残りは args に移す。未インストールならその事実を報告する。"),
    ("selfcheck_invalid_cwd",
     "cwd 가 관리 워크트리 안의 존재하는 상대 디렉터리가 아니다(절대경로, 드라이브, 범위 밖) → 워크트리 기준 상대 경로를 쓰거나 '.' 로 둔다.",
     "cwd is not an existing relative directory inside the managed worktree (absolute path, drive, outside) -> use a worktree-relative path or '.'.",
     "cwd が管理ワークツリー内に存在する相対ディレクトリではない(絶対パス、ドライブ、範囲外) → ワークツリー基準の相対パスを使うか '.' のままにする。"),
    ("selfcheck_disabled",
     "이 프로젝트는 Self-check 가 꺼져 있다 → 반환된 사유를 TR 에 보고하고 다른 실행 수단을 찾지 않는다.",
     "Self-check is disabled for this project -> report the returned reason in the TR and do not look for another execution path.",
     "このプロジェクトでは Self-check が無効 → 返された理由を TR に報告し、他の実行手段を探さない。"),
    ("selfcheck_already_running",
     "이 Group 에 이미 실행 중인 run 이 있다. 실행 슬롯은 TR 마다가 아니라 Group 에 하나이므로 다른 TR 이나 TR 등록 전(draft) run 일 수도 있다. 응답 details 의 self_check_run_id 와 run 요약(tr_doc_id·draft·status·created_at·same_owner)을 본다 → same_owner 가 true 이면 read 로 조회해 끝나기를 기다리거나 cancel 한 뒤 다시 실행한다. false 이면(details.reason_code = selfcheck_foreign_run) 그 run 은 이 토큰으로 취소할 수 없으므로 group_read 경로로 상태만 조회하며 끝나기를 기다리고, 오래 남으면 그 사실과 run 요약을 TR 에 보고한다.",
     "A run is already active in this Group. The run slot is one per Group, not per TR, so it may belong to another TR or to a draft run (before its TR exists). Read details.self_check_run_id and the run summary (tr_doc_id, draft, status, created_at, same_owner) -> if same_owner is true, read it and wait for it to finish, or cancel it, then run again. If false (details.reason_code = selfcheck_foreign_run), this token cannot cancel it: follow its status through the group_read path and wait; if it stays, report that and the run summary in the TR.",
     "この Group には実行中の run がある。実行スロットは TR ごとではなく Group に1つなので、別の TR や TR 登録前(draft)の run の場合もある。応答 details の self_check_run_id と run 要約(tr_doc_id・draft・status・created_at・same_owner)を見る → same_owner が true なら read で確認して完了を待つか cancel してから再実行する。false なら(details.reason_code = selfcheck_foreign_run)このトークンでは取消できないので group_read の経路で状態だけを確認して完了を待ち、長く残る場合はその事実と run 要約を TR に報告する。"),
    ("selfcheck_owner_scope_closed",
     "이 토큰의 관리형 provider/hop 소유 범위가 끝났거나 이 서버에 활성 binding이 없다. 독립 실행으로 전환하지 않는다 → 이전 요청을 재시도하지 말고 현재 실행의 새 토큰과 상태를 확인한다.",
     "This token belongs to an ended managed provider/hop scope, or has no active binding on this server. It cannot become an independent run -> do not retry the old request; check the current execution and its new token.",
     "このトークンの管理対象 provider/hop 所有範囲は終了したか、このサーバーに有効な binding がない。独立実行には変換しない → 古い要求を再試行せず、現在の実行と新しいトークンを確認する。"),
    ("selfcheck_source_busy",
     "다른 소스 변경 작업이 프로젝트 소스 잠금을 잡고 있다 → 그 작업이 끝난 뒤 잠시 후 다시 시도한다.",
     "Another source operation holds the project source lock -> retry after that operation finishes.",
     "別のソース操作がプロジェクトのソースロックを保持している → その操作の終了後にしばらくして再試行する。"),
    ("selfcheck_cancel_unconfirmed",
     "취소 요청은 기록됐지만(details.cancel.accepted) 실행이 멈췄음을 확인하지 못했다. 이 서버에 그 run 의 실행 제어가 없고, 소유 서버의 종료나 프로세스 트리 전체의 부재가 증명되지 않았다(details.cancel.reason, 사유별 코드는 details.reason_code). run 상태와 보호는 그대로 유지된다 → 취소를 반복하지 말고 read 로 상태를 지켜보며, 계속 남으면 run 요약과 사유를 TR 에 보고한다.",
     "The cancel was recorded (details.cancel.accepted) but the run is not confirmed stopped: no control for it exists in this server, and neither the owning server's end nor the absence of the whole process tree is proved (details.cancel.reason; the reason-specific code is details.reason_code). The run keeps its state and protection -> do not repeat the cancel; follow its status with read, and if it stays, report the run summary and the reason in the TR.",
     "取消要求は記録された(details.cancel.accepted)が、実行の停止は確認できていない。このサーバーにその run の実行制御がなく、所有サーバーの終了もプロセスツリー全体の不在も証明されていない(details.cancel.reason、理由別コードは details.reason_code)。run の状態と保護はそのまま維持される → 取消を繰り返さず read で状態を見守り、残り続ける場合は run 要約と理由を TR に報告する。"),
    ("selfcheck_foreign_run",
     "selfcheck_already_running 의 details.reason_code 다. 슬롯을 잡은 run 이 다른 TR 이나 다른 draft 토큰의 것이라 이 토큰의 read/cancel 경로로는 보이지 않는다(selfcheck_run_not_found) → details.run 의 group_read_path 로 상태만 조회하며 끝나기를 기다리고, 오래 남으면 run 요약을 TR 에 보고한다. 취소를 시도하지 않는다.",
     "The details.reason_code of selfcheck_already_running: the run holding the slot belongs to another TR or another draft token, so this token's read/cancel paths do not see it (selfcheck_run_not_found) -> follow its status through details.run.group_read_path and wait; if it stays, report the run summary in the TR. Do not try to cancel it.",
     "selfcheck_already_running の details.reason_code。スロットを持つ run が別の TR または別の draft トークンのもので、このトークンの read/cancel 経路からは見えない(selfcheck_run_not_found) → details.run の group_read_path で状態だけを確認して完了を待ち、長く残る場合は run 要約を TR に報告する。取消は試みない。"),
    ("selfcheck_ownership_unknown",
     "그 run 을 소유한 서버를 알 수 없거나(기록 없음) 그 서버의 종료를 증명하지 못했다(조회 실패·다른 노드·인스턴스 행 없음). cancel_unconfirmed 의 details.reason_code 이거나 재검증 거절 코드다. run 상태와 보호는 그대로다 → 반복 요청하지 말고 run 요약과 사유를 TR 에 보고한다. 증명 없는 강제 해제는 없다.",
     "The server owning the run is unknown (nothing recorded) or its end is not proved (lookup failure, another node, no instance row). It is the details.reason_code of cancel_unconfirmed or the code of a refused re-verification. The run keeps its state and protection -> do not repeat the request; report the run summary and the reason in the TR. There is no forced release without proof.",
     "その run を所有するサーバーが不明(記録なし)、またはそのサーバーの終了を証明できない(照会失敗・別ノード・インスタンス行なし)。cancel_unconfirmed の details.reason_code、または再検証の拒否コード。run の状態と保護はそのまま → 要求を繰り返さず、run 要約と理由を TR に報告する。証明なしの強制解除はない。"),
    ("selfcheck_tree_unproven",
     "소유 서버의 종료는 증명됐지만 run 의 프로세스 트리 전체(target·supervisor·그룹/Job·run 마커)가 비었음을 증명하지 못했다(구성원 잔존 또는 조회 불가). cancel_unconfirmed 의 details.reason_code 이거나 재검증 거절 코드다. run 은 recovery_incomplete 로 남고 Group 의 G 는 보호된다 → 반복하지 말고 사유를 보고한다. 서버의 주기 reaper 가 증명이 성립하면 정리한다.",
     "The owning server's end is proved but the run's whole process tree (target, supervisor, group / Job, run marker) is not proved empty (a member remains, or it cannot be seen). It is the details.reason_code of cancel_unconfirmed or the code of a refused re-verification. The run stays recovery_incomplete and the Group's G protected -> do not repeat; report the reason. The server's periodic reaper ends it once the proof holds.",
     "所有サーバーの終了は証明されたが、run のプロセスツリー全体(target・supervisor・グループ/Job・run マーカー)が空であることを証明できない(構成員が残存、または照会不可)。cancel_unconfirmed の details.reason_code、または再検証の拒否コード。run は recovery_incomplete のまま Group の G は保護される → 繰り返さず理由を報告する。証明が成立すればサーバーの定期 reaper が整理する。"),
    ("selfcheck_owner_alive",
     "재검증 거절: 그 run 을 소유한 서버(또는 이 서버의 워커)가 살아 있다. 소유자가 직접 끝내므로 서버가 대신 해제하지 않는다 → read 로 상태를 지켜본다.",
     "Re-verification refused: the server owning the run (or this server's worker) is alive. Its owner ends it, so the server does not release it instead -> follow its status with read.",
     "再検証の拒否: その run を所有するサーバー(またはこのサーバーのワーカー)が生存している。所有者が自ら終了するため、サーバーが代わりに解除しない → read で状態を見守る。"),
    ("selfcheck_reverify_not_applicable",
     "재검증 대상이 아니다. 소유권 재검증은 recovery_incomplete 인 active run 에만 한다(콘솔 사용자, project.document.update) → 끝난 run 이나 보호되지 않은 run 은 read 로 상태를 본다.",
     "Not a re-verification target. Ownership re-verification applies only to an active run in recovery_incomplete (console user, project.document.update) -> read the status of an ended or unprotected run instead.",
     "再検証の対象ではない。所有権の再検証は recovery_incomplete の active run にだけ行う(コンソールユーザー、project.document.update) → 終了した run や保護されていない run は read で状態を見る。"),
    ("selfcheck_state_conflict",
     "판정하는 사이 run 상태가 바뀌었다(다른 복구·취소·워커가 먼저 기록함) → read 로 현재 상태를 다시 본 뒤 필요하면 한 번 더 요청한다.",
     "The run's state changed while it was being judged (another recovery, cancel or the worker wrote first) -> read its current state again, then ask once more if still needed.",
     "判定の間に run の状態が変わった(別の復旧・取消・ワーカーが先に記録した) → read で現在の状態を確認し、必要ならもう一度要求する。"),
    ("selfcheck_lock_lost",
     "failed run 의 error_code 다. 실행 중 Group 잠금(G)이 사라져 다른 작업이 같은 워크트리에 들어왔을 수 있으므로 run 을 멈췄다. 결과(exit_code·tail)를 검증 근거로 쓰지 않는다 → 다시 실행해 결과를 얻는다.",
     "The error_code of a failed run: the Group lock (G) disappeared during the run, so another operation may have entered the same worktree, and the run was stopped. Do not use its result (exit_code, tails) as evidence -> run it again.",
     "failed run の error_code。実行中に Group ロック(G)が消え、別の作業が同じワークツリーに入った可能性があるため run を停止した。その結果(exit_code・tail)を検証の根拠に使わない → 再実行して結果を得る。"),
    ("selfcheck_worktree_unavailable",
     "이 TR 의 관리 워크트리를 쓸 수 없다 → 반환된 사유를 보고한다. 다른 경로나 복사본으로 우회하지 않는다.",
     "The managed worktree of this TR is unavailable -> report the returned reason; do not route around it with another path or a copy.",
     "このTRの管理ワークツリーを利用できない → 返された理由を報告する。別経路やコピーで回避しない。"),
)

_SELF_CHECK_COPY: dict[str, dict] = {
    "ko": {
        "role": "TR 작성(new)·수정(edit) 단계에서 테스트·검증을 실행하는 공식 경로는 Self-check 하나다. 도구는 run_self_check / read_self_check / cancel_self_check 이다.",
        "no_fallback": [
            "Self-check 를 쓸 수 없으면 반환된 reason/error_code 를 TR 에 보고하고, 다른 실행 수단을 찾거나 시도하지 않는다.",
            "실행할 명령은 TR 에 적힌 검증 명령 → T 에 적힌 검증 명령 → 변경에서 분명한 최소 검사 순으로 정한다. 정할 수 없으면 test_command_missing 으로 멈춘다.",
        ],
        "field_desc": {
            "program": "실행 파일 이름 하나. 예: pytest. 명령 문자열 전체 금지.",
            "args": "옵션과 대상 경로의 문자열 배열. 기본 [].",
            "cwd": "관리 워크트리 기준 상대 경로. 기본 \".\".",
            "timeout_seconds": "실행 제한 시간(초), 양의 정수. 생략하면 카드 한도이며, 큰 값은 카드 한도로 줄인다.",
            "parallel_share": f"이 실행이 쓸 처리기(worker) 수 희망, 1 이상 정수. 기본 {SELF_CHECK_SHARE_DEFAULT}. 실행은 받은 예약 수만큼의 처리기에서만 돈다.",
        },
        "http_note": "tr_doc_id 는 지금 수정 중인 TR 문서 id(토큰에 바인딩된 문서)이고 run_id 는 run 응답의 self_check_run_id 이다. 헤더는 Authorization: Bearer <작업 토큰> 을 그대로 쓴다. 성공 응답은 {ok:true, ...run 필드}, 라우트가 만드는 실패 응답은 {ok:false, error:{code, message, details}} 이며 code 는 아래 오류 표의 selfcheck_* 또는 forbidden 이다. 다른 문서의 토큰이나 edit 가 아닌 토큰은 403 이고 error.code 는 forbidden 이다(message·details 포함). 토큰 자체가 검증에 실패하면(Authorization 헤더 없음 401, 만료·무효 토큰, 권한 없음 403) 라우트가 아니라 인증 계층이 {ok:false, http_status, error_message, help_url} 을 돌려주며 error 객체가 없다. 응답에서 확인할 것: run 은 202 와 self_check_run_id·status, read/cancel 은 200 과 status(아래 statuses 중 하나).",
        "draft_http_note": "TR 작성(new) 단계에서는 TR 문서가 아직 없으므로 문서 id 대신 작업 토큰이 실행의 소유자다. 경로에 문서 id 를 넣지 않고 Authorization: Bearer <작업 토큰> 만 보낸다. run_id 는 run 응답의 self_check_run_id 이다. 목록·조회·취소는 이 토큰이 시작한 실행만 대상으로 한다. 이 토큰으로 TR 을 등록하면 그 실행들은 등록된 TR 에 연결되고(linked_at), 이후에는 TR 문서 경로(/documents/{tr_doc_id}/self-check/runs)로 조회한다. 워크플로 head 가 TR 이 아닌 new 토큰, 다른 그룹·프로젝트의 토큰은 403 이고 error.code 는 forbidden 이다. 성공·실패 응답 형식과 인증 계층 오류 형식은 edit 단계와 같다.",
        "group_note": "실행 슬롯은 Group 에 하나다. 이 Group 의 작업 토큰(new·edit·review)은 group_read / group_list 로 그 슬롯을 잡은 run 의 요약을 읽을 수 있다(다른 Group 의 run 은 404). 읽기 전용이며 취소는 그 run 을 소유한 TR 의 edit 토큰이나 draft 토큰만 한다. cancel 응답의 cancel 필드는 접수(accepted)와 중단 확인(stopped)을 구분한다.",
        "invalid_note": "위 형식(명령 전체를 program 에 넣음)은 program/args 계약 위반이다. 'pytest' 와 ['-q', 'server/tests/test_x.py'] 로 나누어 보낸다.",
        "lifecycle": "run_self_check → self_check_run_id 수신 → 작업을 계속하고 필요할 때 read_self_check로 결과 조회. 필요하면 cancel_self_check.",
        "non_zero": "exit_code 가 0 이 아닌 completed 는 전송 실패가 아니라 실제 검증 실패일 수 있다. stdout_tail/stderr_tail 을 읽고 → 범위 안에서 코드를 고치고 → 새 run_self_check 를 실행하는 것이 정상 절차다.",
        "failed_note": "status=failed 는 실행 자체가 실패한 것이다. error_code 를 아래 오류 표로 해석한다.",
        "changed_note": "source_changed_during_run / worktree_state_changed 가 true 이면 실행 중 소스나 워크트리 상태가 바뀐 것이므로 결과를 그대로 믿지 말고 안정된 뒤 다시 실행한다.",
    },
    "en": {
        "role": "Self-check is the one official way to run tests/verification in a TR new or edit step. The tools are run_self_check / read_self_check / cancel_self_check.",
        "no_fallback": [
            "If Self-check is unavailable, report the returned reason/error_code in the TR and do not look for or try another execution backend.",
            "Pick the command from the verification command named in the TR, else in the T, else the minimal check obvious from your change. If none can be determined, stop with test_command_missing.",
        ],
        "field_desc": {
            "program": "One executable name, e.g. pytest. Never the whole command string.",
            "args": "Array of strings: options and target paths. Default [].",
            "cwd": "Path relative to the managed worktree. Default \".\".",
            "timeout_seconds": "Positive integer seconds. Omit for the card limit; larger requests are capped to that limit.",
            "parallel_share": f"Processors (workers) this run wishes to use, integer of at least 1. Default {SELF_CHECK_SHARE_DEFAULT}. The run executes on its granted processors only.",
        },
        "http_note": "tr_doc_id is the TR document id you are editing (the document bound to the token) and run_id is the self_check_run_id from the run response. Send the same Authorization: Bearer <work token> header. Success is {ok:true, ...run fields}; route failures are {ok:false, error:{code, message, details}} where code is a selfcheck_* value from the error table below or forbidden. A token for another document or a non-edit token gets 403 with error.code forbidden (message and details included). If the token itself fails verification (missing Authorization header 401, expired or invalid token, no permission 403), the authentication layer answers instead of the route with {ok:false, http_status, error_message, help_url} and no error object. Verify in the response: run returns 202 with self_check_run_id and status; read/cancel return 200 with a status from the statuses list below.",
        "draft_http_note": "In a TR new step the TR document does not exist yet, so the work token, not a document id, owns the run. Put no document id in the path; send only Authorization: Bearer <work token>. run_id is the self_check_run_id from the run response. list/read/cancel only see the runs this token started. When this token registers the TR, those runs are linked to it (linked_at) and are read from then on through the TR document paths (/documents/{tr_doc_id}/self-check/runs). A new token whose workflow head is not a TR, or a token of another group/project, gets 403 with error.code forbidden. Success/failure and authentication-layer error shapes are the same as in the edit step.",
        "group_note": "The run slot is one per Group. Any work token of this Group (new, edit, review) reads the summary of the run holding it through group_read / group_list (another Group's run answers 404). Read only: only the owning TR's edit token or the owning draft token cancels it. A cancel response's cancel field tells the request being accepted apart from the run being confirmed stopped.",
        "invalid_note": "The form above (whole command in program) violates the program/args contract. Send 'pytest' and ['-q', 'server/tests/test_x.py'] separately.",
        "lifecycle": "run_self_check -> receive self_check_run_id -> continue your work and read_self_check when you need the result. Use cancel_self_check if needed.",
        "non_zero": "A completed run with a non-zero exit_code is not a transport failure; it may be a real verification failure. Read stdout_tail/stderr_tail, fix the code within scope, then start a new run_self_check. That is the normal procedure.",
        "failed_note": "status=failed means the run itself failed. Interpret error_code with the error table below.",
        "changed_note": "If source_changed_during_run / worktree_state_changed is true, the source or worktree state moved during the run; do not trust the result blindly and rerun once it is stable.",
    },
    "ja": {
        "role": "TR作成(new)・修正(edit)段階でテスト・検証を実行する公式経路は Self-check ただ1つです。ツールは run_self_check / read_self_check / cancel_self_check です。",
        "no_fallback": [
            "Self-check が使えない場合は、返された reason/error_code を TR に報告し、他の実行手段を探したり試したりしないでください。",
            "実行コマンドは、TRに記載の検証コマンド → Tに記載の検証コマンド → 変更から明らかな最小の検査、の順で決めます。決められない場合は test_command_missing で停止します。",
        ],
        "field_desc": {
            "program": "実行ファイル名1つ。例: pytest。コマンド文字列全体は不可。",
            "args": "オプションと対象パスの文字列配列。既定 []。",
            "cwd": "管理ワークツリー基準の相対パス。既定 \".\"。",
            "timeout_seconds": "実行制限時間(秒)、正の整数。省略時はカードの上限で、それを超える値は上限に短縮する。",
            "parallel_share": f"この実行が使いたい処理器(worker)数、1 以上の整数。既定 {SELF_CHECK_SHARE_DEFAULT}。実行は割り当てられた予約数の処理器だけで動く。",
        },
        "http_note": "tr_doc_id は今修正中の TR 文書 id(トークンに紐づく文書)、run_id は run 応答の self_check_run_id です。ヘッダーは Authorization: Bearer <作業トークン> をそのまま使います。成功は {ok:true, ...run フィールド}、ルートが返す失敗は {ok:false, error:{code, message, details}} で、code は下のエラー表の selfcheck_* または forbidden です。他文書のトークンや edit 以外のトークンは 403 で error.code は forbidden です(message と details を含む)。トークン自体の検証に失敗した場合(Authorization ヘッダーなし 401、期限切れ・無効トークン、権限なし 403)は、ルートではなく認証層が {ok:false, http_status, error_message, help_url} を返し、error オブジェクトはありません。応答で確認すること: run は 202 と self_check_run_id・status、read/cancel は 200 と status(下の statuses のいずれか)。",
        "draft_http_note": "TR作成(new)段階では TR 文書がまだ無いため、文書 id ではなく作業トークンが実行の所有者です。パスに文書 id を入れず、Authorization: Bearer <作業トークン> だけを送ります。run_id は run 応答の self_check_run_id です。一覧・取得・取消はこのトークンが開始した実行だけが対象です。このトークンで TR を登録すると、それらの実行は登録された TR に連結され(linked_at)、以後は TR 文書のパス(/documents/{tr_doc_id}/self-check/runs)で取得します。ワークフロー head が TR でない new トークンや他グループ・他プロジェクトのトークンは 403 で error.code は forbidden です。成功・失敗の応答形式と認証層エラーの形式は edit 段階と同じです。",
        "group_note": "実行スロットは Group に1つです。この Group の作業トークン(new・edit・review)は group_read / group_list でそのスロットを持つ run の要約を読めます(他 Group の run は 404)。読み取り専用で、取消はその run を所有する TR の edit トークンか draft トークンだけが行います。cancel 応答の cancel フィールドは受付(accepted)と停止確認(stopped)を区別します。",
        "invalid_note": "上の形式(コマンド全体を program に入れる)は program/args 契約違反です。'pytest' と ['-q', 'server/tests/test_x.py'] に分けて送ってください。",
        "lifecycle": "run_self_check → self_check_run_id を受け取る → 作業を続け、必要なときに read_self_check で結果を確認。必要なら cancel_self_check。",
        "non_zero": "exit_code が 0 以外の completed は通信失敗ではなく、実際の検証失敗の可能性があります。stdout_tail/stderr_tail を読み、範囲内でコードを直し、新しい run_self_check を実行するのが正常な手順です。",
        "failed_note": "status=failed は実行自体の失敗です。error_code を下のエラー表で解釈してください。",
        "changed_note": "source_changed_during_run / worktree_state_changed が true なら実行中にソースやワークツリーの状態が変わっています。結果を鵜呑みにせず、安定してから再実行してください。",
    },
}

_SELF_CHECK_ATTEMPT_COPY: dict[str, dict] = {
    "ko": {
        "note": "이 토큰은 Graph attempt 에 매핑되어 있다. Self-check 는 그룹 워크트리가 아니라 이 attempt 의 전용 워크트리(원격 소스 도구가 읽고 쓰는 트리)에서 실행되고, cwd 는 그 트리 기준 상대 경로다. run 응답과 조회 결과의 attempt_id·input_commit 이 실행 기준(attempt 와 그 트리의 시작 커밋)이며 source_head_before/after 는 attempt 트리의 HEAD 다. 실행 중에는 이 attempt 트리에 대한 소스 쓰기가 잠긴다. 실행이 끝났어도 그 프로세스 트리가 비었음이 증명되지 않으면(recovery_incomplete) 이 잠금은 보호된 채 남고, 증명된 뒤에 풀린다. 실행 슬롯은 attempt 가 아니라 Group 에 하나다.",
        "errors": {
            "graph_attempt_inactive": "실행이 정지 중이거나 attempt 가 무효화되어 이 attempt 에서는 Self-check 를 시작할 수 없다 → 반복 호출하지 말고 details(reason·attempt_id)를 TR 에 보고한다. 그룹 워크트리나 로컬 실행으로 우회하지 않는다.",
            "graph_worktree_unavailable": "attempt 의 전용 워크트리를 확인할 수 없다(준비 전, 트리 없음, marker 불일치 등 details.reason) → 사유를 TR 에 보고한다. 그룹 워크트리로 대신 실행하지 않는다.",
        },
    },
    "en": {
        "note": "This token is mapped to a Graph attempt. Self-check runs in that attempt's own worktree (the tree the remote source tools read and write), not in the group worktree, and cwd is relative to that tree. attempt_id and input_commit in the run and read responses are the run's basis (the attempt and the commit its tree started from); source_head_before/after are the attempt tree's HEAD. Source writes to this attempt tree are locked while the run executes; when the run's process tree is not proved empty at its end (recovery_incomplete), the lock stays protected until that is proved. The run slot is one per Group, not per attempt.",
        "errors": {
            "graph_attempt_inactive": "The run is stopping or the attempt was invalidated, so no Self-check can start for this attempt -> do not call again; report details (reason, attempt_id) in the TR. Do not route around it through the group worktree or a local run.",
            "graph_worktree_unavailable": "The attempt's own worktree cannot be confirmed (not prepared, tree missing, marker mismatch, ... see details.reason) -> report the reason in the TR. The group worktree is never used in its place.",
        },
    },
    "ja": {
        "note": "このトークンは Graph attempt にマッピングされています。Self-check はグループのワークツリーではなく、この attempt 専用のワークツリー(リモートソースツールが読み書きするツリー)で実行され、cwd はそのツリー基準の相対パスです。run 応答と取得結果の attempt_id・input_commit が実行の基準(attempt とそのツリーの開始コミット)で、source_head_before/after は attempt ツリーの HEAD です。実行中はこの attempt ツリーへのソース書き込みがロックされます。終了時にそのプロセスツリーが空だと証明できない場合(recovery_incomplete)、ロックは保護されたまま残り、証明された後に解除されます。実行スロットは attempt ではなく Group に1つです。",
        "errors": {
            "graph_attempt_inactive": "実行の停止中、または attempt が無効化されたため、この attempt では Self-check を開始できない → 繰り返し呼ばず、details(reason・attempt_id)を TR に報告する。グループのワークツリーやローカル実行で回避しない。",
            "graph_worktree_unavailable": "attempt 専用のワークツリーを確認できない(準備前、ツリーなし、marker 不一致など details.reason) → 理由を TR に報告する。グループのワークツリーで代わりに実行しない。",
        },
    },
}

_SELF_CHECK_BASELINE_COPY: dict[str, dict] = {
    "ko": {
        "summary": "pytest 실행이 정상 종료되면 출력 맨 끝의 결과 블록이 result_summary(건수·실패 목록·항목별 테스트 정의 증거)와 environment_evidence(도구 버전·호스트 능력·환경 값·설치 의존성·적용 worker)로 저장된다. 판정은 지금처럼 status·exit_code·timed_out·소스 변동이며, 요약은 보강 증거일 뿐 통과의 근거가 아니다.",
        "status": {
            "none": "블록이 없다(구조화 결과 없음). 아무것도 추정하지 않는다.",
            "incomplete": "블록이 깨졌거나 잘렸거나 둘 이상이거나 수집·완료 건수가 다르거나, 블록의 종료 상태가 비정상(0·1 이외)이거나 건수끼리 모순된다. 믿지 않는다.",
            "mismatch": "실제 종료 코드와 블록의 종료 상태 또는 실패 목록이 어긋난다. 어느 쪽도 성공으로 올리지 않는다.",
            "complete": "수집 = 완료이고 잘림·불일치가 없다. 기준선 비교에 쓸 수 있다.",
        },
        "how": "변경 후 실행을 read_self_check 할 때 baseline_run_id 에 제품 파일을 고치기 전에 돌린 기준선 실행 id 를 함께 주면 baseline_comparison 이 붙는다. reusable=false 이면 reasons 를 TR 에 적고 비교 근거로 쓰지 않는다. 기준선과 변경 후 실행은 같은 명령·같은 호스트·같은 worker 조건으로 돌린다.",
        "conditions": "재사용 조건: 같은 Group, 기준선이 정상 종료(시간 초과·취소·실패·복구 아님), 요약 complete, 실행 중 소스 변동 없음, 기준선의 실행 전·후 HEAD가 작업 시작 때 기록된 변경 전 기준점과 같고 작업 트리가 깨끗함(기준점은 작업 단위의 첫 발급임이 입증될 때만 기록하며, 앞선 발급이 기준점을 남기지 못한 작업은 계속 없음. 기준점이 없거나 현재 HEAD의 조상이 아니면 불가), 명령(program·args·cwd·정책 버전) 동일, 환경 증거 전 범주(범주마다 필수 하위 항목 전부, env 는 HOST_KEYS 전부)와 호스트 식별자 동일(없거나 빠지면 불가), 실행 수준 테스트 정의(pytest 설정·conftest·세션/자동 fixture·테스트 트리 플러그인과 그것들이 읽는 데이터) 동일.",
        "comparison": {
            "new_failures": "변경 후에만 실패한 항목(새 테스트 포함). 이번 변경의 결함 후보다.",
            "existing_failures": "양쪽 모두 실패했고 테스트 정의 증거가 같은 항목만. 성공으로 바꾸지 않고 따로 표시한다.",
            "definition_changed_failures": "양쪽 모두 실패했지만 테스트 본문·fixture·모듈 전역 값·보조 함수·데이터가 바뀌었거나 확인할 수 없는 항목. 기존 실패가 아니며 신규 실패 후보와 같은 수준으로 TR에 변경 사유를 적는다.",
            "resolved_or_gone": "기준선에서만 실패한 항목. 해소인지 삭제·이름 변경인지는 구분하지 않는다.",
        },
        "advisory": "비교 결과는 참고 증거다. TR·검수 통과 기준, required gate, 반려 규칙은 바뀌지 않는다. 기존 실패를 xfail 처리하거나 목록에서 빼지 않는다.",
    },
    "en": {
        "summary": "When a pytest run ends normally, the result block at the very end of its output is stored as result_summary (counts, failures, per-item test definition evidence) and environment_evidence (tool versions, host capabilities, environment values, installed dependencies, applied workers). The verdict stays status, exit_code, timed_out and source change; the summary is supporting evidence only, never a reason to pass.",
        "status": {
            "none": "No block (no structured result). Nothing is guessed.",
            "incomplete": "The block is broken, cut, truncated, repeated, collected and completed counts differ, its exit status is abnormal (not 0 or 1), or its counts contradict each other. Not trusted.",
            "mismatch": "The actual exit code and the block's exit status or failure list disagree. Neither side is promoted to success.",
            "complete": "Collected = completed, nothing truncated or mismatched. Usable for a baseline comparison.",
        },
        "how": "When you read_self_check an after-change run, pass baseline_run_id with the id of the baseline run made before your first product file change; the answer gains baseline_comparison. On reusable=false write the reasons in the TR and do not use that comparison as evidence. Run the baseline and the after-change run with the same command, on the same host, with the same worker setting.",
        "conditions": "Reuse needs: the same Group, a baseline that ended normally (not timed out, cancelled, failed or recovered), a complete summary, no source change during the run, the baseline's HEAD before and after equal to the pre-change anchor recorded when the work started with a clean tree (it is recorded only when the issue is proven to be the work unit's first; a unit whose earlier issue left no anchor stays without one. No anchor, or an anchor that is not an ancestor of the current HEAD, means no reuse), the same command (program, args, cwd, policy version), every environment evidence category (with all its required sub-items, env holding every HOST_KEYS variable) and the host identifier equal (missing or partial means no reuse), and the same run-level test definition (pytest settings, conftest, session/autouse fixtures, test-tree plugins and the data they read).",
        "comparison": {
            "new_failures": "Failing only after the change (new tests included): candidates for defects of this change.",
            "existing_failures": "Failing on both sides with equal test definition evidence only. Shown apart, never turned into a pass.",
            "definition_changed_failures": "Failing on both sides, but the test body, fixtures, module-level values, helpers or data changed or cannot be confirmed. Not existing failures: report the reason for the change in the TR, at the level of a new failure.",
            "resolved_or_gone": "Failing only in the baseline. Whether resolved, deleted or renamed is not told apart.",
        },
        "advisory": "The comparison is advisory evidence. TR / review pass criteria, required gates and rejection rules are unchanged. Do not mark existing failures xfail or drop them from the list.",
    },
    "ja": {
        "summary": "pytest 実行が正常終了すると、出力末尾の結果ブロックが result_summary(件数・失敗一覧・項目別のテスト定義証拠)と environment_evidence(ツールバージョン・ホスト能力・環境値・インストール依存・適用 worker)として保存される。判定は従来どおり status・exit_code・timed_out・ソース変動であり、要約は補強証拠にすぎず合格の根拠にはならない。",
        "status": {
            "none": "ブロックがない(構造化結果なし)。何も推定しない。",
            "incomplete": "ブロックが壊れている・切れている・複数ある、収集件数と完了件数が異なる、ブロックの終了ステータスが異常(0・1 以外)、または件数どうしが矛盾する。信頼しない。",
            "mismatch": "実際の終了コードとブロックの終了ステータスまたは失敗一覧が食い違う。どちらも成功に格上げしない。",
            "complete": "収集 = 完了で、切り詰め・不一致がない。基準線比較に使える。",
        },
        "how": "変更後の実行を read_self_check するとき、baseline_run_id に製品ファイルを変更する前に実行した基準線の実行 id を渡すと baseline_comparison が付く。reusable=false なら reasons を TR に記載し、その比較を根拠にしない。基準線と変更後の実行は同じコマンド・同じホスト・同じ worker 条件で実行する。",
        "conditions": "再利用の条件: 同じ Group、基準線が正常終了(タイムアウト・取消・失敗・復旧ではない)、要約が complete、実行中のソース変動なし、基準線の実行前後の HEAD が作業開始時に記録された変更前基準点と同じで作業ツリーがクリーン(基準点は作業単位の最初の発行と証明できるときだけ記録し、先の発行が基準点を残せなかった作業はずっとなし。基準点がない、または現在の HEAD の祖先でなければ不可)、コマンド(program・args・cwd・ポリシーバージョン)が同じ、環境証拠の全カテゴリ(カテゴリごとの必須下位項目すべて、env は HOST_KEYS すべて)とホスト識別子が同じ(欠ければ不可)、実行レベルのテスト定義(pytest 設定・conftest・セッション/自動 fixture・テストツリーのプラグインとそれらが読むデータ)が同じ。",
        "comparison": {
            "new_failures": "変更後にのみ失敗した項目(新しいテストを含む)。今回の変更の欠陥候補。",
            "existing_failures": "両方で失敗し、テスト定義証拠が同じ項目のみ。成功に変えず別に表示する。",
            "definition_changed_failures": "両方で失敗したが、テスト本体・fixture・モジュール全域の値・補助関数・データが変わった、または確認できない項目。既存失敗ではなく、新規失敗候補と同じ水準で TR に変更理由を記載する。",
            "resolved_or_gone": "基準線でのみ失敗した項目。解消か削除・名前変更かは区別しない。",
        },
        "advisory": "比較結果は参考証拠である。TR・検収の合格基準、required gate、差し戻し規則は変わらない。既存失敗を xfail にしたり一覧から外したりしない。",
    },
}

_SELF_CHECK_PARALLEL_COPY: dict[str, dict] = {
    "ko": {
        "reservation": "호스트의 모든 Self-check 실행(직렬·pytest 가 아닌 명령 포함, TS 실행 제외)은 처리기(논리 프로세서)를 예약한다. 희망은 parallel_share(기본 1)이고, 받은 예약 R 은 응답의 cpu_reservation 이다. 예약 합은 호스트 상한을 넘지 않으며, 1을 넘는 몫은 직렬 예비분을 남기는 범위에서만 준다.",
        "guarantee": "실행의 프로세스 트리 전체는 운영체제가 R개 처리기에만 묶는다(Windows Job 처리기 한정, POSIX supervisor 처리기 집합). 도구의 협조와 무관하게 동시에 실행되는 worker 는 R 을 넘지 못한다. R 보다 많은 worker 를 만드는 도구는 실패하지 않고 느려진다.",
        "pytest": "pytest 병렬은 큰 선택에서만 -n <몫> --dist loadfile 로 쓰고 parallel_share 를 같은 수로 준다. server/tests 의 pytest 는 실행 하나의 worker 슬롯 풀(크기 R)을 나눠 써서 실제 worker 를 빈 슬롯 수로 낮추고, 중첩 pytest 는 남은 슬롯만 쓰며 없으면 슬롯 없이 직렬로 돈다. 안에서 pytest 를 여러 번 띄우는 스크립트보다 pytest 직접 실행을 쓴다.",
        "serial_only": "고정 포트 연결 통합 테스트, 같은 소스에 산출물을 쓰는 빌드·타입 검사, 단일·소수 파일 선택은 병렬을 쓰지 않는다.",
        "queue": "요청은 거절되지 않고 큐에 들어간다(즉시 status queued 로 응답). 그 순간의 워크트리 사본에서 돌며 Group 잠금을 잡지 않는다. 처리기 예약 R 은 큐가 실행을 시작할 때 정해지고(cpu_reservation) 자리가 없으면 기다린다. 결과는 read_self_check 로 조회한다. 같은 카드의 새 요청은 아직 시작하지 않은 이전 요청을 취소한다.",
    },
    "en": {
        "reservation": "Every Self-check run of a host (serial runs and non-pytest commands included, TS runs excluded) reserves logical processors. The wish is parallel_share (default 1); the granted reservation R is the response's cpu_reservation. Reservations never sum above the host cap, and a share above 1 is granted only while the serial reserve stays free.",
        "guarantee": "The operating system limits the run's whole process tree to R processors (Windows Job affinity, POSIX supervisor processor set). Whatever the tool does, no more than R workers execute at once. A tool that starts more workers than R does not fail; it runs slower.",
        "pytest": "Use pytest parallelism only for large selections, as -n <share> --dist loadfile with parallel_share set to the same number. server/tests pytest shares one worker slot pool of size R per run and lowers its workers to the free slots; a nested pytest uses only the slots left and runs serially without a slot when none is free. Prefer running pytest directly over a script that starts pytest several times.",
        "serial_only": "Keep fixed-port connection integration tests, builds or type checks writing into the same source, and single- or few-file selections serial.",
        "queue": "A request is never refused for capacity: it is queued and answered at once (status queued). It runs in a copy of the worktree taken at that moment and takes no Group lock. Its reservation R is decided when the queue starts it (cpu_reservation); until a processor is free it waits. Read the result with read_self_check. A new request of the same card cancels its earlier requests that have not started.",
    },
    "ja": {
        "reservation": "ホストのすべての Self-check 実行(直列・pytest 以外のコマンドを含み、TS 実行は除く)は処理器(論理プロセッサ)を予約する。希望は parallel_share(既定 1)で、割り当てられた予約 R は応答の cpu_reservation である。予約の合計はホスト上限を超えず、1 を超える分は直列予備分を残せる範囲でだけ与える。",
        "guarantee": "実行のプロセスツリー全体を OS が R 個の処理器だけに限定する(Windows Job の処理器限定、POSIX supervisor の処理器集合)。ツールの協力に関係なく同時に実行される worker は R を超えない。R より多くの worker を作るツールは失敗せず遅くなる。",
        "pytest": "pytest の並列は大きな選択でだけ -n <分> --dist loadfile で使い、parallel_share を同じ数にする。server/tests の pytest は実行ごとの worker スロットプール(サイズ R)を分け合い、実際の worker を空きスロット数に下げる。入れ子の pytest は残りのスロットだけを使い、なければスロットなしで直列に動く。内部で pytest を何度も起動するスクリプトより pytest の直接実行を使う。",
        "serial_only": "固定ポートの接続統合テスト、同じソースに成果物を書くビルド・型検査、単一・少数ファイルの選択は並列にしない。",
        "queue": "要求は容量で拒否されずキューに入り、すぐに status queued で応答される。その時点のワークツリーの写しで実行され、Group ロックを取らない。処理器の予約 R はキューが実行を始めるときに決まり(cpu_reservation)、空きがなければ待つ。結果は read_self_check で確認する。同じカードの新しい要求は、まだ始まっていない以前の要求を取り消す。",
    },
}

def _self_check_attempt(ctx: dict) -> Optional[str]:
    """The Graph attempt the caller's worker token is mapped to, else None."""
    token_rec = ctx.get("token_rec")
    if ctx.get("principal_kind") != "worker" or not token_rec:
        return None
    from modules.flow_gate.services import graph_source_hooks
    try:
        return graph_source_hooks.binding(token_rec)
    except Exception:
        _logger.warning("help: Graph attempt mapping lookup failed", exc_info=True)
        return None

def _content_tr_self_check(ctx: dict) -> dict:
    locale = ctx["locale"]
    from modules.flow_gate.services import tr_self_check_quota as quota
    token = ctx.get("token_rec")
    limits = quota.resolve(token)
    verification = (quota.allowance(limits, locale) if token else
                    {"notice": quota.notice(limits, limits["max_runs"], locale)})
    copy = _SELF_CHECK_COPY.get(locale, _SELF_CHECK_COPY[FALLBACK_LOCALE])
    idx = _LOCALE_INDEX.get(locale, 1)
    types = {"program": "string", "args": "array<string>", "cwd": "string", "timeout_seconds": "integer",
             "parallel_share": "integer"}
    defaults = {"program": None, "args": [], "cwd": ".", "timeout_seconds": limits["run_timeout_sec"],
                "parallel_share": SELF_CHECK_SHARE_DEFAULT}
    # A TR(new) worker has no TR id yet; its runs go through the token-owned draft routes.
    draft = ctx.get("action_scope") == "new"
    operations = SELF_CHECK_DRAFT_HTTP_OPERATIONS if draft else SELF_CHECK_HTTP_OPERATIONS
    content = {
        "role": copy["role"],
        "verification": verification["notice"],
        "stage": "new" if draft else "edit",
        "no_fallback": list(copy["no_fallback"]),
        "tools": {"run": "run_self_check", "read": "read_self_check", "cancel": "cancel_self_check"},
        "http": {
            "base_url": SELF_CHECK_HTTP_BASE,
            "note": copy["draft_http_note"] if draft else copy["http_note"],
            "operations": [
                {"name": name, "method": method, "path": path,
                 "url": SELF_CHECK_HTTP_BASE + path, "success_status": status}
                for name, method, path, status in operations
            ],
            "group_note": copy["group_note"],
            "group_operations": [
                {"name": name, "method": method, "path": path,
                 "url": SELF_CHECK_HTTP_BASE + path, "success_status": status}
                for name, method, path, status in SELF_CHECK_GROUP_HTTP_OPERATIONS
            ],
            "run_request_body": "request.example",
            "response_ok_fields": ["ok", "self_check_run_id", "status", *SELF_CHECK_RESULT_FIELDS],
            "error_shape": {"ok": False, "error": {"code": "selfcheck_*|forbidden", "message": "<code>", "details": {}}},
            "auth_error_shape": {"ok": False, "http_status": 401, "error_message": "<reason>", "help_url": "<url>"},
        },
        "request": {
            "fields": [
                {
                    "name": name,
                    "type": types[name],
                    "required": name == "program",
                    "default": defaults[name],
                    "description": copy["field_desc"][name],
                }
                for name in SELF_CHECK_REQUEST_FIELDS
            ],
            "example": dict(SELF_CHECK_EXAMPLE, args=list(SELF_CHECK_EXAMPLE["args"]), timeout_seconds=limits["run_timeout_sec"]),
            "invalid_example": dict(SELF_CHECK_INVALID_EXAMPLE),
            "invalid_example_note": copy["invalid_note"],
        },
        "lifecycle": {
            "flow": copy["lifecycle"],
            "statuses": list(SELF_CHECK_STATUSES),
            "result_fields": list(SELF_CHECK_RESULT_FIELDS),
            "non_zero_exit": copy["non_zero"],
            "failed": copy["failed_note"],
            "source_changed": copy["changed_note"],
        },
        "errors": [{"code": row[0], "guidance": row[idx]} for row in _SELF_CHECK_ERRORS],
    }
    baseline_copy = _SELF_CHECK_BASELINE_COPY.get(locale, _SELF_CHECK_BASELINE_COPY[FALLBACK_LOCALE])
    content["baseline"] = {
        "query_param": "baseline_run_id",
        "tool_field": "read_self_check.baseline_run_id",
        "response_field": "baseline_comparison",
        "summary": baseline_copy["summary"],
        "summary_status": dict(baseline_copy["status"]),
        "how": baseline_copy["how"],
        "conditions": baseline_copy["conditions"],
        "comparison": dict(baseline_copy["comparison"]),
        "advisory": baseline_copy["advisory"],
    }
    parallel_copy = _SELF_CHECK_PARALLEL_COPY.get(locale, _SELF_CHECK_PARALLEL_COPY[FALLBACK_LOCALE])
    content["parallel"] = {
        "request_field": "parallel_share",
        "response_fields": ["cpu_share_requested", "cpu_reservation", "enqueued_at", "queue"],
        **{key: parallel_copy[key] for key in ("reservation", "guarantee", "pytest", "serial_only",
                                               "queue")},
    }
    attempt_id = _self_check_attempt(ctx)
    if attempt_id:
        attempt_copy = _SELF_CHECK_ATTEMPT_COPY.get(locale, _SELF_CHECK_ATTEMPT_COPY[FALLBACK_LOCALE])
        content["graph_attempt"] = {
            "attempt_id": attempt_id,
            "execution_root": "graph_attempt_worktree",
            "note": attempt_copy["note"],
            "run_fields": ["attempt_id", "input_commit", "source_head_before", "source_head_after"],
            "errors": [{"code": code, "guidance": text} for code, text in attempt_copy["errors"].items()],
        }
    return content

_CONTENT_SUPPLIERS = {
    "notices": _content_notices,
    "group_documents": _content_group_documents,
    "document_access": _content_document_access,
    "document_attachments": _content_document_attachments,
    "doc_type": _content_doc_type,
    "question": _content_question,
    "submit": _content_submit,
    "test_commands": _content_test_commands,
    "tr_self_check": _content_tr_self_check,
    "changed_files_format": _content_changed_files_format,
}

_CONTENT_SUPPLIERS.update({
    name: (lambda ctx, name=name: chat_help_content.content(name, ctx))
    for name in chat_help_content.ITEMS
})

def _child_source_tool(child: str, ctx: dict) -> dict:
    base = ctx["base_url"]
    registry = ctx["registry"] or {"kind": "none"}
    return {
        "name": "source_tools",
        "child": child,
        "form": "content",
        "kind": registry["kind"],
        "content": tool_registry.build_tool_detail(child, ctx["locale"], base),
        "notes": tool_registry.detail_notes(child, ctx["locale"]),
    }

_WORK_PLAN_TEMPLATE_TITLE: dict[str, dict[str, str]] = {
    "ko": {"title": "작업계획", "summary": "작업계획(WP) 정본 JSON 서식."},
    "en": {"title": "Work Plan", "summary": "Canonical JSON body of a work plan (WP)."},
    "ja": {"title": "作業計画", "summary": "作業計画(WP)の正本 JSON 書式。"},
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

def _authoring_guide_body(type_code: str, locale: str) -> str:
    # Imported lazily: the mention builders own this copy, and importing them at
    # module scope would drag the whole mention assembly into every help request.
    from modules.flow_gate.services import mention_service

    if type_code == "T2":
        return {
            "ko": "TR2가 edit-spec으로 표현할 수 있는 소스 변경을 지시하는 Markdown 지시서를 작성합니다.",
            "en": "Write a Markdown instruction for source changes expressible as a TR2 edit-spec.",
            "ja": "TR2 の edit-spec で表現できるソース変更を Markdown で指示します.",
        }.get(locale, "TR2가 edit-spec으로 표현할 수 있는 소스 변경을 지시하는 Markdown 지시서를 작성합니다.")
    if type_code == "TR2":
        return {
            "ko": "본문은 Markdown이 아닌 canonical JSON(document.json)입니다. tr2_version=1, source_t2_doc_id, edit_spec의 termination/edits/deferred/gate를 제출하세요. source는 읽기 전용입니다. 직접 수정하지 말고 edit/create_file로 제안하세요. baseline_fingerprint는 서버가 계산합니다. ready_to_apply에는 edit가 한 건 이상 있어야 하고 gate.apply는 false입니다.",
            "en": "Submit canonical JSON (document.json), not Markdown: tr2_version=1, source_t2_doc_id, edit_spec with termination, edits, deferred, gate. Source is read-only; propose edit/create_file entries. The server computes baseline_fingerprint. ready_to_apply needs at least one edit; gate.apply must be false.",
            "ja": "本文は Markdown ではなく canonical JSON(document.json) です。tr2_version=1、source_t2_doc_id、termination/edits/deferred/gate を含む edit_spec を提出します。ソースは読み取り専用です。変更は edit/create_file で提案します。baseline_fingerprint はサーバーが計算します。ready_to_apply には一件以上の edit が必要で、gate.apply は false です。",
        }.get(locale, "TR2 canonical JSON(document.json): tr2_version=1, source_t2_doc_id, edit_spec; source read-only; gate.apply=false.")
    if type_code == "TS":
        return mention_service._ts_authoring_section(locale)
    if type_code in {"N", "T"}:
        return mention_service._nt_authoring_section(type_code, locale)
    if type_code == "TR":
        # TR owns implementation and test assets; the formal gate belongs to TS/TSR.
        return (_TR_AUTHORING_GUIDE.get(locale, _TR_AUTHORING_GUIDE[FALLBACK_LOCALE])
                + "\n" + {
                    "ko": "시험 코드·fixture·mock은 작성하고 가능한 self-check 결과를 보고합니다. TR 완료에 공식 시험 PASS나 승인형 Snapshot은 필요하지 않습니다. 공식 gate는 TS/TSR에서 확인합니다.",
                    "en": "Create test code, fixtures and mocks and report available self-checks. TR completion requires no formal PASS or approval-based Snapshot; TS/TSR owns the formal gate.",
                    "ja": "テストコード・fixture・mockを作成し、実施可能なself-checkを報告します。TR完了に正式PASSや承認型Snapshotは不要で、正式gateはTS/TSRで確認します。",
                }.get(locale, ""))
    raise ValueError(f"Unknown authoring guide type: {type_code}")

def _child_authoring_guide(child: str, ctx: dict) -> dict:
    locale = ctx["locale"]
    try:
        body = _authoring_guide_body(child, locale)
    except Exception as exc:
        raise HelpSupplierError(f"authoring guide build failed for {child}") from exc
    return {
        "name": "authoring_guide",
        "child": child,
        "form": "content",
        "content": {
            "type_code": child,
            "title": _copy(_AUTHORING_GUIDE_TITLES, locale, child),
            "body": body,
        },
    }
