"""Chat-only card question (Q) list and answer (flowgate.v02gui.0010, NR0003 §8, Q#600 / Q#601).

A chat AI answers the questions a card's AI left on a card document of the chat's own Flow, on the user's behalf.
The general Q routes (``/api/v1/q/...``) keep their binding rule (a token answers only on its own ``doc_ref``); this
is a separate entry, so registering questions stays closed to chat runs.

* ``list_card_questions``   – the Flow's card questions (open ones by default), per document, with ``item_id``/``seq``.
* ``answer_card_question``  – one answer to one question, stored through ``q_service.register_answer``.

Both transports share one service: the API chat tool (``call_handler``) and the CLI chat HTTP routes
(``chat_routes``). Who may answer is decided only by the chat's identity and the user's permission:

* identity: ``chat_authz.authorize_chat_run`` (the chat token's run, project and group — never the token's own
  document), then ``chat_recheck_alive`` inside the chat run's gate, so an answer never lands after its run's end;
* scope: the target document is in the chat's project AND Flow (group), is not a conversation, and the item is that
  document's;
* permission (Q#600): ``perm_document_create`` of the issued user on the document's project — no edit mode, no command
  access, no approval step; archived / disposed Flows (``graph_service.require_writable`` under the document's guard)
  and the Graph attempt fence refuse as on the general route;
* one answer: a question that already has an answer is refused (409 ``question_already_answered``); the same chat
  run's own earlier answer is answered as a replay (``replayed: true``), never stored twice.

The answer is ``author_kind=ai`` with the chat run as ``author_ai_run_id``. The card that asked goes on at the chat
run's end (``card_settings.question_wait.follow``), not at once — the chat run may hold the Flow's lease. The answer's
``card_resume`` says what to expect (``question_wait.chat_answer_outlook``).
"""
from __future__ import annotations

import contextlib
import json
import logging
from typing import Optional

from fastapi import HTTPException

from modules.flow_gate.db import documents
from . import call_ledger, chat_authz, chat_errors, chat_gate, target
from .chat_adapter import _object, serialized_length, success_body, tool_result_limit
from .errors import GraphApiError, invalid, internal_error

_log = logging.getLogger(__name__)

LIST_TOOL = "list_card_questions"
ANSWER_TOOL = "answer_card_question"

LIST_FIELDS = frozenset({"doc_id", "include_answered"})
ANSWER_FIELDS = frozenset({"doc_id", "item_id", "body", "selected_option_ids"})
QUESTION_BODY_MAX = 4000
ANSWER_BODY_PREVIEW = 1000
CONVERSATION_TYPE = "CH"

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_id": {"type": "string", "minLength": 1},
        "include_answered": {"type": "boolean"},
    },
    "additionalProperties": False,
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_id": {"type": "string", "minLength": 1},
        "item_id": {"type": "integer", "minimum": 1},
        "body": {"type": "string"},
        "selected_option_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 1},
    },
    "required": ["doc_id", "item_id"],
    "additionalProperties": False,
}

_RULES = (
    "Answer before you send your reply: the reply ends this chat run. When the user's instruction does not name one "
    "question unambiguously (which card, which Q), ask the user instead of guessing. Never write the answer only in "
    "your reply and leave the question unanswered."
)
LIST_DESCRIPTION = (
    "List the questions (Q) that card AIs left on the documents of this chat's Flow, per document: item_id, seq (the "
    "Q number the user sees, Q1 = seq 1), title, body, options, answered and the card the document belongs to. "
    "Without doc_id: the documents with open questions; with doc_id: that document's questions. include_answered "
    "adds answered ones with their latest answer. Use it to turn \"card X's Q1\" into doc_id + item_id."
)
ANSWER_DESCRIPTION = (
    "Answer one card question on the user's behalf: doc_id and item_id from list_card_questions, and body (free "
    "text) and/or selected_option_ids (one option id of that question). Reading mode may answer; the user needs "
    "document create permission on the project. A question that already has an answer is refused (409 "
    "question_already_answered). card_resume tells whether the card that asked goes on after this chat run ends "
    "(resumes_after_chat_run), still waits for other questions, or cannot go on now because the Flow's run is "
    "paused or stopped (run_not_resumable): tell the user that the answer was saved and what happens to the card. "
    "Refusals and errors are not answers: report them as they are. " + _RULES
)
PROMPT_HINT = (
    "Card questions (Q): use list_card_questions / answer_card_question when the user asks you to answer a card's "
    "question. " + _RULES
)


_MESSAGES = {
    "document_not_found": "The document does not exist.",
    "question_outside_flow": "The document is not in this chat's Flow; only that Flow's card questions can be answered here.",
    "question_target_invalid": "A conversation has no card questions.",
    "question_not_found": "The question does not exist on this document.",
    "question_already_answered": "This question already has an answer; nothing was stored.",
    "permission_denied": "The user lacks the permission this needs.",
    "self_answer_forbidden": "An AI run may not answer the question it registered itself.",
    "invalid_answer": "The answer was refused as malformed.",
}


def _err(status, code, params, message=None):
    return GraphApiError(status, code, params, message=message or _MESSAGES.get(code))


# ── input ────────────────────────────────────────────────────────────────────

def _unknown(inp, allowed):
    unknown = sorted(set(inp) - set(allowed))
    if unknown:
        raise GraphApiError(422, "invalid_request", {"errors": [
            {"field": k, "reason": "unknown_field"} for k in unknown]})


@contextlib.contextmanager
def _input(via, run, tool, allowed, request_fields, path_fields, ctx):
    """API: the frozen call record (after authentication). CLI: the HTTP body/query + path fields."""
    if via == chat_authz.API:
        with call_ledger.dispatching(run, ctx) as record:
            if record.tool_name != tool:
                raise chat_errors.call_identity_unavailable()
            call_ledger.require_tool_call_id(record)
            inp = _object(record.input_copy())
            _unknown(inp, allowed)
            yield inp
    else:
        body = {} if request_fields is None else _object(request_fields)
        _unknown(body, set(allowed) - set(path_fields))
        yield {**body, **path_fields}


def _text(value, field, *, required=False):
    if value is None:
        if required:
            raise invalid("invalid_request", field, "required")
        return None
    if not isinstance(value, str):
        raise invalid("invalid_request", field, "invalid_type")
    value = value.strip()
    if required and not value:
        raise invalid("invalid_request", field, "required")
    return value or None


def _flag(value, field):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "1", "false", "0"):
        return value.lower() in ("true", "1")
    raise invalid("invalid_request", field, "invalid_type")


def _item_id(value):
    if value is None:
        raise invalid("invalid_request", "item_id", "required")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if type(value) is not int or value < 1:
        raise invalid("invalid_request", "item_id", "invalid_type")
    return value


def _option_ids(value):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise invalid("invalid_request", "selected_option_ids", "invalid_type")
    if len(value) > 1:
        raise invalid("invalid_request", "selected_option_ids", "invalid_format", max=1)
    return list(value)


# ── scope ────────────────────────────────────────────────────────────────────

def _document_in_flow(run, doc_id):
    """The target document: in the chat's project and Flow, not a conversation. Facts of another project are
    never told (403 ``chat_document_forbidden`` as the document tools)."""
    doc = documents.get_by_id(doc_id)
    if doc is None:
        raise _err(404, "document_not_found", {"doc_id": doc_id})
    if str(doc.get("project_id")) != str(run.get("project_id")):
        raise chat_errors.forbidden()
    if str(doc.get("group_id") or "") != str(run.get("group_id") or ""):
        raise _err(403, "question_outside_flow", {"doc_id": doc_id, "flow_id": run.get("group_id")})
    if (doc.get("type_code") or "") == CONVERSATION_TYPE:
        raise _err(422, "question_target_invalid", {"doc_id": doc_id, "reason": "conversation"})
    return doc


def _authorize(token, project_id, perm, flow_id):
    from modules.flow_gate.rbac.permission_service import has_permission
    if not has_permission(token["issued_to"], project_id, perm):
        raise _err(403, "permission_denied", {"flow_id": flow_id, "required": perm})


def _cards(doc):
    """The card lines (node_key, slot) this document is bound to; [] when none or unreadable."""
    try:
        rows = target.related_rows(doc)
    except Exception:  # noqa: BLE001 — the card is a hint for the model, never a gate
        return []
    return [{"node_key": k, "slot": s} for k, s in sorted({(r["node_key"], r["slot"]) for r in rows or []})]


# ── read ─────────────────────────────────────────────────────────────────────

def _options(item):
    raw = item.get("options")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        return []
    return [{"id": o.get("id"), "label": o.get("label", "")} for o in parsed if isinstance(o, dict)]


def _cut(text, limit):
    text = text or ""
    return (text, False) if len(text) <= limit else (text[:limit], True)


def _latest_answer(item_id):
    from modules.flow_gate.db import answers as db_answers
    rows = db_answers.list_by_question_item(item_id) or []
    return rows[-1] if rows else None


def _item_view(item, include_answer):
    body, cut = _cut(item.get("body"), QUESTION_BODY_MAX)
    answered = int(item.get("answer_count") or 0) > 0
    view = {"item_id": item["id"], "seq": item.get("seq"), "title": item.get("title"), "body": body,
            "options": _options(item), "asker_kind": item.get("asker_kind"), "answered": answered,
            "answer_count": int(item.get("answer_count") or 0), "responder_state": item.get("responder_state")}
    if cut:
        view["body_truncated"] = True
    if include_answer and answered:
        latest = _latest_answer(item["id"])
        if latest is not None:
            text, answer_cut = _cut(latest.get("body"), ANSWER_BODY_PREVIEW)
            view["latest_answer"] = {"body": text, "author_kind": latest.get("author_kind"),
                                     "created_at": latest.get("created_at")}
            if answer_cut:
                view["latest_answer"]["body_truncated"] = True
    return view


def _document_view(doc, include_answered):
    from modules.flow_gate.db import question_items as db_question_items
    items = db_question_items.list_by_doc(doc["doc_id"]) or []
    shown = [_item_view(i, include_answered) for i in items
             if include_answered or int(i.get("answer_count") or 0) == 0]
    return {"doc_id": doc["doc_id"], "type_code": doc.get("type_code"), "title": doc.get("title"),
            "status": doc.get("status"), "cards": _cards(doc),
            "open_count": sum(1 for i in items if int(i.get("answer_count") or 0) == 0), "questions": shown}


def _fit(payload):
    """API transport: drop trailing documents / questions until the result fits, and say so (``truncated``)."""
    limit = tool_result_limit()
    omitted = 0
    while serialized_length(success_body(payload)) > limit:
        docs = payload["documents"]
        if not docs:
            break
        if docs[-1]["questions"]:
            docs[-1]["questions"].pop()
        else:
            docs.pop()
        omitted += 1
        payload["truncated"] = True
    if omitted:
        payload["truncation_note"] = ("The API chat size limit cut the list: read one document with doc_id, or use the "
                                      "CLI chat.")
    return payload


def chat_list_questions(via, *, token_raw=None, run_hint=None, query=None, ctx=None):
    run, token = chat_authz.authorize_chat_run(via, token_raw, run_hint)
    with _input(via, run, LIST_TOOL, LIST_FIELDS, query, {}, ctx) as inp:
        doc_id = _text(inp.get("doc_id"), "doc_id")
        include_answered = _flag(inp.get("include_answered"), "include_answered")
        flow_id = run.get("group_id")
        _authorize(token, run["project_id"], "perm_document_read", flow_id)
        if doc_id is not None:
            docs = [_document_in_flow(run, doc_id)]
        else:
            from modules.flow_gate.db import questions as db_questions
            docs = [d for d in (documents.get_by_id(i) for i in db_questions.list_open_doc_ids_by_group(flow_id) or [])
                    if d is not None and (d.get("type_code") or "") != CONVERSATION_TYPE]
        payload = {"flow_id": flow_id, "include_answered": include_answered,
                   "documents": [_document_view(d, include_answered) for d in docs], "truncated": False}
        if via == chat_authz.API:
            payload = _fit(payload)
        return success_body(payload)


# ── answer ───────────────────────────────────────────────────────────────────

_REFUSALS = {400: (422, "invalid_answer"), 403: (403, "self_answer_forbidden"), 404: (404, "question_not_found"),
             409: (409, "question_already_answered")}


def _refusal(exc: HTTPException, doc_id, item_id):
    status, code = _REFUSALS.get(exc.status_code, (exc.status_code, "question_answer_refused"))
    detail = exc.detail if isinstance(exc.detail, str) else None
    return _err(status, code, {"doc_id": doc_id, "item_id": item_id}, message=detail)


def _provenance(run_id):
    """The chat run's provider snapshot; at least its id (``answers.author_ai_run_id``) — the run-end follow-up
    finds the chat's answers by it."""
    from modules.flow_gate.services.ai_invoke.provenance import (
        resolve_persisted_run_provenance, resolve_run_provenance)
    snapshot = {}
    try:
        snapshot = resolve_run_provenance(run_id, doc_id=None, allowed_action_scopes=None) or {}
        if not snapshot:
            snapshot = resolve_persisted_run_provenance(run_id) or {}
    except Exception:  # noqa: BLE001 — the provider half stays unconfirmed, never guessed
        _log.warning("chat answer provenance unreadable run=%s", run_id, exc_info=True)
        snapshot = {}
    return {**snapshot, "ai_run_id": run_id}


def _item_of(doc_id, item_id):
    from modules.flow_gate.db import question_items as db_question_items
    from modules.flow_gate.db import questions as db_questions
    container = db_questions.get_container_by_doc(doc_id)
    item = db_question_items.get_by_pk(item_id)
    if container is None or item is None or item.get("question_id") != container.get("id"):
        raise _err(404, "question_not_found", {"doc_id": doc_id, "item_id": item_id})
    return item


def _store_answer(run, token, doc, item_id, body, option_ids, locale):
    """Inside the document's guard (the same one the general answer route takes): the one-answer check and the
    save are one step against every other guarded answer."""
    from modules.flow_gate.graph_workflow.services.flow_guard import run_in_document_guard
    from modules.flow_gate.graph_workflow.services.graph_service import require_writable
    from modules.flow_gate.services import q_service

    doc_id, run_id = doc["doc_id"], run["run_id"]

    def write():
        require_writable(doc["group_id"])
        item = _item_of(doc_id, item_id)
        if int(item.get("answer_count") or 0) > 0:
            latest = _latest_answer(item_id)
            if latest is not None and str(latest.get("author_ai_run_id") or "") == str(run_id):
                return item, {"doc_id": doc_id, "item_id": item_id, "answer_id": latest.get("id"),
                              "author_kind": latest.get("author_kind"), "replayed": True}
            raise _err(409, "question_already_answered", {"doc_id": doc_id, "item_id": item_id})
        try:
            result = q_service.register_answer(
                doc_id=doc_id, item_id=item_id, body=body or "", author_kind="ai", author_id=None,
                selected_option_ids=option_ids, notify_audience=token["issued_to"],
                author_provenance=_provenance(run_id),
                auto_resume_api_base_url=run.get("api_base_url") or None, auto_resume_locale=locale,
                writer_ai_run_id=run_id)
        except HTTPException as exc:
            raise _refusal(exc, doc_id, item_id) from exc
        return item, {**result, "replayed": False}

    return run_in_document_guard(doc_id, write)


def chat_answer_question(via, *, token_raw=None, run_hint=None, doc_id=None, item_id=None, request_body=None,
                         ctx=None, locale="ko"):
    from modules.flow_gate.services import graph_source_hooks
    run, token = chat_authz.authorize_chat_run(via, token_raw, run_hint)
    path = {} if doc_id is None else {"doc_id": doc_id, "item_id": item_id}
    with _input(via, run, ANSWER_TOOL, ANSWER_FIELDS, request_body, path, ctx) as inp:
        target_doc_id = _text(inp.get("doc_id"), "doc_id", required=True)
        target_item = _item_id(inp.get("item_id"))
        body = inp.get("body")
        if body is not None and not isinstance(body, str):
            raise invalid("invalid_request", "body", "invalid_type")
        option_ids = _option_ids(inp.get("selected_option_ids"))
        if not (body or "").strip() and not option_ids:
            raise invalid("invalid_answer", "body", "required")
        doc = _document_in_flow(run, target_doc_id)
        _authorize(token, doc["project_id"], "perm_document_create", doc.get("group_id"))
        holder = chat_gate.TicketHolder()
        try:
            chat_authz.chat_recheck_alive(run, token, holder)
            with graph_source_hooks.write_session():
                try:
                    graph_source_hooks.require_writable(token, "q_answer")
                except graph_source_hooks.GraphSourceError as exc:
                    raise GraphApiError(409, exc.code, dict(exc.details or {}), message=exc.message) from exc
                item, result = _store_answer(run, token, doc, target_item, body, option_ids,
                                             locale or run.get("locale") or "ko")
        finally:
            chat_gate.release(holder.ticket)
        if not isinstance(result, dict):
            raise internal_error()
        from flowgate.card_settings import question_wait
        asker = item.get("asker_ai_run_id") if (item.get("asker_kind") or "") == "ai" else None
        payload = {"doc_id": target_doc_id, "item_id": target_item, "answer_id": result.get("answer_id"),
                   "author_kind": result.get("author_kind") or "ai", "replayed": bool(result.get("replayed")),
                   "card_resume": question_wait.chat_answer_outlook(doc.get("group_id"), asker)}
        if "status" in result:
            payload["question_status"] = result["status"]
        return success_body(payload)


# ── API call handlers and descriptors ────────────────────────────────────────

def list_questions_call_handler(run, ctx):
    return chat_errors.tool_result(lambda: chat_list_questions(chat_authz.API, run_hint=run, ctx=ctx))


def answer_question_call_handler(run, ctx):
    return chat_errors.tool_result(lambda: chat_answer_question(
        chat_authz.API, run_hint=run, ctx=ctx, locale=(run or {}).get("locale") or "ko"))


def _stub_handler(run, tool_input):
    """The old ``(run, input)`` signature: authenticate, then refuse (the call handler is the only way in)."""
    def refuse():
        chat_authz.authorize_chat_run(chat_authz.API, None, run)
        raise chat_errors.call_identity_unavailable()
    return chat_errors.tool_result(refuse)


def _chat_base(api_base: str) -> str:
    base = str(api_base or "").rstrip("/")
    if base.endswith("/api/v1"):
        return base[:-len("/api/v1")] + "/api/v2/chat"
    return base + "/api/v2/chat"


def cli_mention_section(api_base: str, raw_token: str) -> str:
    base = _chat_base(api_base)
    return (
        "[Card questions (Q): list and answer]\n"
        f"GET {base}/questions?doc_id=<optional>&include_answered=<true|false>\n"
        f"POST {base}/questions/{{doc_id}}/items/{{item_id}}/answers\n"
        f"Authorization: Bearer {raw_token}\n"
        "GET lists the questions card AIs left on this chat's Flow documents: item_id, seq (Q1 = seq 1), body, "
        "options, answered and the card of each document. POST answers one of them on the user's behalf with "
        '{"body": "<answer>"} and/or {"selected_option_ids": ["<one option id>"]}. Reading mode may answer; the '
        "user needs document create permission. A question that already has an answer is 409 "
        "question_already_answered. card_resume in the answer tells whether the card goes on after this chat run "
        "(resumes_after_chat_run), still waits for other questions, or cannot go on now because the Flow's run is "
        "paused or stopped (run_not_resumable): tell the user. Answer BEFORE you submit your turn — submitting "
        "consumes this token. If the instruction does not name one question unambiguously, ask the user instead of "
        "guessing; never write the answer only in your turn and leave the question unanswered. Report refusals and "
        "errors as they are."
    )


def descriptors() -> list:
    return [
        {"name": LIST_TOOL, "description": LIST_DESCRIPTION, "schema": LIST_SCHEMA, "handler": _stub_handler,
         "call_handler": list_questions_call_handler, "own_validation": True, "completion": False,
         "cli_mention": cli_mention_section, "prompt_hint": PROMPT_HINT},
        {"name": ANSWER_TOOL, "description": ANSWER_DESCRIPTION, "schema": ANSWER_SCHEMA, "handler": _stub_handler,
         "call_handler": answer_question_call_handler, "own_validation": True, "completion": False},
    ]
