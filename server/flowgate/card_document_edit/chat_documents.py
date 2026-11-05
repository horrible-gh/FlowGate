"""Chat document tools by doc_id (flowgate.v02.0127 CH0028).

The user and the model name a document only by its ``doc_id``. The server decides the rest:

* ``read_document_by_id``   – consistent body/revision read of an existing document.
* ``revise_document_by_id`` – full-body save of an EXISTING document (keeps its doc_id, revision +1).
  The server classifies the document (``target.classify_card_slot``):
    CARD        -> the server finds the one card line in ``graph_node_documents`` and calls
                   the common ``revise()`` (same checks as ``revise_card_document``);
    NON_CARD    -> ``legacy_store`` with the client's ``expected_revision`` and idempotency key;
    UNAVAILABLE -> refused (fail-closed), nothing is written.
* ``create_new_document`` – registers a NEW workflow-root document (R/B, new group) through the
  same service as the console (``process_service.create_workflow_root``). It never edits an
  existing document, and ``revise_document_by_id`` never creates one (missing doc_id -> 404).

Identity, run life, edit mode (``read_write``), the in-boundary ``chat_recheck``, user actor
attribution and API/CLI call identity are the ones of ``chat_adapter``.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from modules.flow_gate.db import documents
from . import call_ledger, chat_authz, chat_errors, chat_gate, common_checks, contacts, guards, read, target, type_rules
from .chat_adapter import (ALTERNATIVE_PATHS, CHAT_EDIT_REASON, _identity, _object, cli_key, serialized_length,
                           success_body, tool_result_limit)
from .contracts import CONTENT_MAX_CHARS, PROJECTION_FAILED, Actor, Origin, ReviseRequest
from .errors import GraphApiError, internal_error, invalid
from .validation import KEY_RE, required_text

_log = logging.getLogger(__name__)

READ_DOC_TOOL = "read_document_by_id"
REVISE_DOC_TOOL = "revise_document_by_id"
CREATE_DOC_TOOL = "create_new_document"

READ_FIELDS = frozenset({"doc_id"})
REVISE_FIELDS = frozenset({"doc_id", "expected_revision", "content"})
CREATE_FIELDS = frozenset({"doc_type", "title", "content", "module", "priority"})
CREATE_TYPES = ("R", "B")
CREATED_KEY = "_chat_created_documents"
_created_lock = threading.Lock()


# ── input ────────────────────────────────────────────────────────────────────

def _unknown(inp, allowed):
    unknown = sorted(set(inp) - set(allowed))
    if unknown:
        raise GraphApiError(422, "invalid_request", {"errors": [
            {"field": k, "reason": "unknown_field"} for k in unknown]})


@contextlib.contextmanager
def _input(via, run, tool, allowed, request_body, path_fields, ctx):
    """API: the frozen call record (after authentication). CLI: the HTTP body + path fields."""
    if via == chat_authz.API:
        with call_ledger.dispatching(run, ctx) as record:
            if record.tool_name != tool:
                raise chat_errors.call_identity_unavailable()
            call_ledger.require_tool_call_id(record)
            inp = _object(record.input_copy())
            _unknown(inp, allowed)
            yield inp, record
    else:
        body = {} if request_body is None else _object(request_body)
        _unknown(body, (set(allowed) - set(path_fields)) | ({"idempotency_key"} if tool != READ_DOC_TOOL else set()))
        yield {**body, **path_fields}, None


def _idempotency_key(run, inp, record):
    if record is not None:
        return call_ledger.api_key(record)
    key = inp.get("idempotency_key")
    required_text(key, "idempotency_key", "invalid_request")
    if not KEY_RE.fullmatch(key):
        raise invalid("invalid_request", "idempotency_key", "invalid_format")
    return cli_key(run["run_id"], key)


def _expected_revision(value):
    if value is None:
        raise invalid("expected_revision_missing", "expected_revision", "required")
    if type(value) is not int:
        raise invalid("expected_revision_missing", "expected_revision", "invalid_type")
    if value < 0:
        raise invalid("expected_revision_missing", "expected_revision", "invalid_format")
    return value


def _require_write_mode(run, token):
    kind = chat_authz.effective_write_kind(token)
    if kind != chat_authz.CHAT_WRITE_KIND:
        raise chat_errors.edit_mode_denied(run["run_id"], kind)


def _document(run, doc_id):
    required_text(doc_id, "doc_id", "invalid_request")
    doc = documents.get_by_id(doc_id.strip())
    if doc is None:
        raise GraphApiError(404, "document_not_found", {"doc_id": doc_id})
    if str(doc.get("project_id")) != str(run.get("project_id")):
        raise chat_errors.forbidden()
    return doc


def _edit_path(classification):
    return {target.CARD: "card", target.NON_CARD: "non_card"}.get(classification, "unavailable")


def _not_editable(doc, reason, **extra):
    return GraphApiError(422, "not_editable", {"doc_id": doc["doc_id"], "revision_no": doc["revision_no"],
                                              "reason": reason, **extra})


# ── API size limit (same rule as the card tools) ─────────────────────────────

def _read_payload(doc, body, classification):
    payload = {**{k: doc.get(k) for k in read.VIEW_KEYS}, "type_code": doc.get("type_code"),
               "group_id": doc.get("group_id"), "body_state": body.state,
               "content": body.text if body.state == "readable" else None,
               "edit_path": _edit_path(classification)}
    return payload


def _check_size(body):
    size, limit = serialized_length(body), tool_result_limit()
    if size > limit:
        raise GraphApiError(413, "result_too_large", {
            "doc_id": body.get("doc_id"), "revision_no": body.get("revision_no"), "size_chars": size,
            "limit_chars": limit, "limit_scope": "api_chat_transport",
            "alternative_paths": list(ALTERNATIVE_PATHS)})


def _size_guard(classification):
    def guard(current, body, line=None):
        _check_size(success_body(_read_payload(current, body, classification)))
    return guard


# ── read ─────────────────────────────────────────────────────────────────────

def chat_read_document(via, *, token_raw=None, run_hint=None, doc_id=None, ctx=None):
    run, token = chat_authz.authorize_chat_run(via, token_raw, run_hint)
    path = {} if doc_id is None else {"doc_id": doc_id}
    with _input(via, run, READ_DOC_TOOL, READ_FIELDS, None, path, ctx) as (inp, record):
        doc = _document(run, inp.get("doc_id"))
        actor, _origin = _identity(run, token, record)
        guards.authorize(actor, doc["project_id"], "perm_document_read", flow_id=doc.get("group_id"))
        fresh, body = read._snapshot_parts(doc["doc_id"])
        result = success_body(_read_payload(fresh, body, target.classify_card_slot(fresh)))
        if via == chat_authz.API:
            _check_size(result)
        return result


# ── revise (existing documents only) ─────────────────────────────────────────

def _card_line(doc):
    """The one card line (node_key, slot) bound to this document, decided by the server."""
    try:
        rows = target.related_rows(doc)
    except Exception:
        _log.warning("chat document card line unresolved doc=%s", doc.get("doc_id"), exc_info=True)
        raise invalid("card_binding_missing", "card_binding", "invalid_format",
                      validation={"reason": "card_edit_unavailable"})
    lines = sorted({(row["node_key"], row["slot"]) for row in rows})
    if len(lines) != 1:
        raise invalid("card_binding_missing", "card_binding", "invalid_format",
                      validation={"reason": "ambiguous_or_inconsistent_binding"})
    return lines[0]


@dataclass(frozen=True)
class ChatDocumentRequest:
    """Non-card chat save request for ``legacy_store`` (DedicatedRequest-compatible)."""
    doc_id: str
    expected_revision: int
    actor: Actor
    rule_call: object
    origin: Origin
    content: str
    idempotency_key: Optional[str] = None
    edit_reason: Optional[str] = CHAT_EDIT_REASON
    linked_doc_id: Optional[str] = None
    transport_extras: dict = field(default_factory=dict)
    event_reason: Optional[str] = None
    new_document: bool = False
    locale: str = "ko"
    structured: Optional[object] = None


class NonCardRule:
    """``rule_call`` of the non-card chat save: the card path's checks without card coordinates."""
    needs_current = False

    def __init__(self, request_box, recheck, size_guard, locale):
        self.box, self.recheck, self.size_guard, self.locale = request_box, recheck, size_guard, locale

    def precheck(self, fresh):
        from modules.flow_gate.documents import document_service
        if fresh is None:
            raise GraphApiError(404, "document_not_found", {"doc_id": self.box[0].doc_id})
        req = self.box[0]
        if fresh.get("group_id"):
            guards.check_writable(fresh["group_id"], guards.principal_of(req, fresh["group_id"]),
                                 guards.operation_of(req))
        if self.recheck is not None:
            self.recheck()
        if fresh.get("status") in {"cancelled", "archived"}:
            raise _not_editable(fresh, "terminal_status", status=fresh["status"])
        final = document_service.is_final_approved(fresh)
        if final:
            raise _not_editable(fresh, "final_approved")
        if not document_service.is_document_editable(fresh, final_approved=final):
            raise _not_editable(fresh, "status_not_editable", status=fresh.get("status"))

    def conflict(self, fresh):
        return GraphApiError(409, "revision_conflict", {"doc_id": fresh["doc_id"], "revision_no": fresh["revision_no"],
                                                        "expected_revision": self.box[0].expected_revision})

    def build(self, fresh, body):
        req = self.box[0]
        rule = type_rules.rule_for(fresh, req)
        if rule is None:
            raise _not_editable(fresh, "unsupported_type", type_code=fresh.get("type_code"))
        if body.state != "readable":
            raise _not_editable(fresh, "body_unreadable", body_state=body.state)
        if self.size_guard is not None:
            self.size_guard(fresh, body)
        text = req.content
        if len(text) > CONTENT_MAX_CHARS:
            raise invalid("invalid_content", "content", "invalid_format", fresh,
                          validation={"reason": "too_long", "max": CONTENT_MAX_CHARS})
        if hasattr(rule, "prepare"):
            text = rule.prepare(text, fresh, body)
        common_checks.identity_frontmatter(text, fresh, rule, body)
        common_checks.encoding_guard(text, fresh, None, self.locale)
        return rule.validate_and_build(text, fresh, body)


def _split_frontmatter(text):
    """(front matter block including both fences and the newline after them, rest) or (None, text)."""
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[:index + 1]), "".join(lines[index + 1:])
    return None, text


def with_current_frontmatter(content, current_text):
    """A body sent without front matter keeps the document's current front matter (identity)."""
    if _split_frontmatter(content)[0] is not None:
        return content
    header, _ = _split_frontmatter(current_text or "")
    if header is None:
        return content
    if not header.endswith("\n"):
        header += "\n"
    return header + ("" if content.startswith("\n") else "\n") + content


def _revise_non_card(doc, expected, content, actor, origin, key, recheck, size_guard, locale):
    from . import legacy_store
    box = []
    rule = NonCardRule(box, recheck, size_guard, locale)
    req = ChatDocumentRequest(doc["doc_id"], expected, actor, rule, origin, content, idempotency_key=key, locale=locale)
    box.append(req)
    try:
        result, plan = legacy_store.legacy_store(req, expected_revision=expected)
    except legacy_store.LegacyReplay as done:
        return "REPLAYED", done.result, done.plan, None
    return "SAVED", {**result, "replayed": False}, plan, None


def chat_revise_document(via, *, token_raw=None, run_hint=None, doc_id=None, request_body=None, ctx=None,
                         locale="ko"):
    run, token = chat_authz.authorize_chat_run(via, token_raw, run_hint)
    path = {} if doc_id is None else {"doc_id": doc_id}
    with _input(via, run, REVISE_DOC_TOOL, REVISE_FIELDS, request_body, path, ctx) as (inp, record):
        actor, origin = _identity(run, token, record)
        required_text(inp.get("doc_id"), "doc_id", "invalid_request")
        expected = _expected_revision(inp.get("expected_revision"))
        content = inp.get("content")
        required_text(content, "content", "invalid_content", nonempty=False)
        key = _idempotency_key(run, inp, record)
        _require_write_mode(run, token)
        doc = _document(run, inp["doc_id"])
        guards.authorize(actor, doc["project_id"], "perm_document_update", flow_id=doc.get("group_id"))
        if _split_frontmatter(content)[0] is None:
            _current, current_body = read._snapshot_parts(doc["doc_id"])
            content = with_current_frontmatter(content, current_body.text)
        classification = target.classify_card_slot(doc)
        holder = chat_gate.TicketHolder()
        recheck = lambda: chat_authz.chat_recheck(run, token, holder)
        size_guard = _size_guard(classification) if via == chat_authz.API else None
        try:
            if classification == target.CARD:
                from .revise import revise
                node_key, slot = _card_line(doc)
                req = ReviseRequest(doc["group_id"], node_key, slot, doc["doc_id"], expected, content, actor, origin,
                                    idempotency_key=key, edit_reason=CHAT_EDIT_REASON, recheck=recheck,
                                    size_guard=size_guard, locale=locale)
                outcome = revise(req)
                kind, result, plan, line = outcome.kind, outcome.result, outcome.plan, outcome.line
            elif classification == target.NON_CARD:
                kind, result, plan, line = _revise_non_card(doc, expected, content, actor, origin, key, recheck,
                                                            size_guard, locale)
            else:
                raise invalid("card_binding_missing", "card_binding", "invalid_format",
                              validation={"reason": "card_edit_unavailable"})
        finally:
            chat_gate.release(holder.ticket)
        if kind == "SAVED":
            contacts.run(plan)
        elif kind == "REPLAYED":
            contacts.run_replay(plan)
        else:
            raise internal_error()
        payload = {**result, "edit_path": _edit_path(classification)}
        if line is not None and line is not PROJECTION_FAILED:
            payload["line"] = line
        body = success_body(payload)
        if via == chat_authz.API:
            assert serialized_length(body) <= tool_result_limit(), "chat edit result exceeds API transport limit"
        return body


# ── create (new documents only) ──────────────────────────────────────────────

def _default_module(run):
    from modules.flow_gate.db import groups
    try:
        group = groups.get_by_id(run.get("group_id")) if run.get("group_id") else None
    except Exception:
        group = None
    module = (group or {}).get("module")
    return module if isinstance(module, str) and module.strip() else "none"


def _optional_text(inp, name):
    value = inp.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid("invalid_request", name, "invalid_type")
    return value.strip() or None


def _created_memo(run):
    with _created_lock:
        memo = run.get(CREATED_KEY)
        if not isinstance(memo, dict):
            memo = run[CREATED_KEY] = {"lock": threading.Lock(), "results": {}}
        return memo


def _create_errors(result):
    errors = []
    for item in result.get("errors") or []:
        errors.append(item if isinstance(item, dict) else {"message": str(item)})
    return errors


def _announce_created(doc):
    try:
        from modules.flow_gate.api.v1.events.publisher import broadcast_event_threadsafe, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType
        broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
            payload={"operation": "created", "doc_id": doc["doc_id"], "type": doc.get("type_code"),
                     "title": doc.get("title"), "status": doc.get("status"), "revision_no": doc.get("revision_no")},
            project=doc["project_id"], group_id=doc.get("group_id"), doc_id=doc["doc_id"], audience="*"))
    except Exception:
        _log.warning("chat document create notification failed", exc_info=True)


def chat_create_document(via, *, token_raw=None, run_hint=None, request_body=None, ctx=None, locale="ko"):
    from modules.flow_gate import process_service
    from modules.flow_gate.rbac.permission_service import has_permission
    run, token = chat_authz.authorize_chat_run(via, token_raw, run_hint)
    with _input(via, run, CREATE_DOC_TOOL, CREATE_FIELDS, request_body, {}, ctx) as (inp, record):
        actor, _origin = _identity(run, token, record)
        doc_type = (_optional_text(inp, "doc_type") or "R").upper()
        if doc_type not in CREATE_TYPES:
            raise invalid("invalid_request", "doc_type", "invalid_format", allowed=list(CREATE_TYPES))
        title = inp.get("title")
        required_text(title, "title", "invalid_request")
        content = inp.get("content", "")
        if content is None:
            content = ""
        required_text(content, "content", "invalid_content", nonempty=False)
        if _split_frontmatter(content)[0] is not None:
            # The server writes the identity front matter of a new document itself.
            raise invalid("invalid_content", "content", "invalid_format", validation={"reason": "frontmatter_not_allowed"})
        if len(content) > CONTENT_MAX_CHARS:
            raise invalid("invalid_content", "content", "invalid_format",
                          validation={"reason": "too_long", "max": CONTENT_MAX_CHARS})
        module = _optional_text(inp, "module") or _default_module(run)
        priority = _optional_text(inp, "priority") or "medium"
        key = _idempotency_key(run, inp, record)
        _require_write_mode(run, token)
        if not has_permission(actor.user_id, run["project_id"], "perm_document_create"):
            raise GraphApiError(403, "permission_denied", {"required": "perm_document_create"})
        memo = _created_memo(run)
        with memo["lock"]:
            done = memo["results"].get(key)
            if done is not None:
                return success_body({**done, "replayed": True})
            holder = chat_gate.TicketHolder()
            try:
                chat_authz.chat_recheck(run, token, holder)
                if not has_permission(actor.user_id, run["project_id"], "perm_document_create"):
                    raise GraphApiError(403, "permission_denied", {"required": "perm_document_create"})
                result = process_service.create_workflow_root(
                    project=str(run["project_id"]), module=module, title=title.strip(), slug="",
                    priority=priority, body=content, owner=actor.user_id, doc_type=doc_type, locale=locale)
            finally:
                chat_gate.release(holder.ticket)
            if result.get("status") != "success" or not result.get("doc_id"):
                raise GraphApiError(422, "document_create_rejected", {"errors": _create_errors(result)})
            doc = documents.get_by_id(result["doc_id"])
            if doc is None:
                raise internal_error()
            payload = {"doc_id": doc["doc_id"], "revision_no": doc.get("revision_no"), "type_code": doc.get("type_code"),
                       "title": doc.get("title"), "status": doc.get("status"), "group_id": doc.get("group_id"),
                       "updated_at": doc.get("updated_at"), "created": True, "replayed": False}
            memo["results"][key] = payload
        _announce_created(doc)
        return success_body(payload)


# ── API call handlers ────────────────────────────────────────────────────────

def read_document_call_handler(run, ctx):
    return chat_errors.tool_result(lambda: chat_read_document(chat_authz.API, run_hint=run, ctx=ctx))


def revise_document_call_handler(run, ctx):
    return chat_errors.tool_result(lambda: chat_revise_document(chat_authz.API, run_hint=run, ctx=ctx))


def create_document_call_handler(run, ctx):
    return chat_errors.tool_result(lambda: chat_create_document(chat_authz.API, run_hint=run, ctx=ctx))
