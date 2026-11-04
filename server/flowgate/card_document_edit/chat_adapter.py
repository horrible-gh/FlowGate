"""L#3 chat read/edit delegation; identity and mode are decided before target access.

Only read_card/revise touch documents. Token and one-shot claim state stay read-only.
"""
import contextlib
import hashlib
import json

from flowgate.card_documents import legacy_adapter as legacy
from modules.flow_gate.graph_workflow.api import envelope
from . import call_ledger, chat_authz, chat_errors, chat_gate, contacts, read
from .contracts import Actor, Origin, ReviseRequest, PROJECTION_FAILED
from .errors import GraphApiError, invalid, internal_error
from .revise import revise
from .validation import check_input_complete, parse_body, required_text, BODY_FIELDS

CHAT_ACTOR_KIND = "person_delegated"
CHAT_ORIGIN = "chat"
CHAT_EDIT_REASON = "user_comment"
COORD_FIELDS = frozenset({"flow_id", "node_key", "slot"})
ALTERNATIVE_PATHS = ["cli_chat:/api/v2/chat", "designer", "agent"]


def cli_key(run_id, client_key):
    text = json.dumps([run_id, client_key], separators=(",", ":"), ensure_ascii=False)
    return "chat-cli:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_result_limit():
    # The worker's actual execution-time value, including overrides. Never copy 16,000.
    from modules.flow_gate.services.ai_invoke import worker
    return worker.API_TOOL_RESULT_MAX_CHARS


def success_body(payload):
    # Include the complete Graph envelope in the size calculation. The outer transport
    # preserves these ok/server_ts fields, so the worker receives exactly this body.
    return json.loads(envelope.ok(payload).body)


def serialized_length(body):
    return len(json.dumps(body, ensure_ascii=False))


def check_size(coords, body):
    size, limit = serialized_length(body), tool_result_limit()
    if size > limit:
        raise GraphApiError(413, "result_too_large", {
            **{k: coords[k] for k in COORD_FIELDS}, "doc_id": body["doc_id"],
            "revision_no": body["revision_no"], "size_chars": size, "limit_chars": limit,
            "limit_scope": "api_chat_transport", "alternative_paths": list(ALTERNATIVE_PATHS)})


def chat_size_guard(coords):
    def guard(current, body, line):
        check_size(coords, success_body(read.compose_get_body(current, body, line)))
    return guard


def _object(value):
    if isinstance(value, bytes):
        try:
            value = json.loads(value)
        except (ValueError, UnicodeError):
            raise invalid("invalid_request", "body", "invalid_format")
    if not isinstance(value, dict):
        raise invalid("invalid_request", "body", "invalid_format")
    return value


@contextlib.contextmanager
def _input(via, run, coords, request_body, ctx, editing):
    if via == chat_authz.API:
        # Called only AFTER authentication. Never accepts fresh model input here.
        with call_ledger.dispatching(run, ctx) as record:
            expected_tool = "revise_card_document" if editing else "read_card_document"
            if record.tool_name != expected_tool:
                raise chat_errors.call_identity_unavailable()
            call_ledger.require_tool_call_id(record)
            inp = _object(record.input_copy())
            allowed = COORD_FIELDS | (BODY_FIELDS - {"idempotency_key"} if editing else set())
            unknown = sorted(set(inp) - allowed)
            if unknown:
                raise GraphApiError(422, "invalid_request", {"errors": [
                    {"field": k, "reason": "unknown_field"} for k in unknown]})
            yield inp, record
    else:
        body = parse_body(_object(request_body)) if editing else {}
        yield {**body, **coords}, None


def _project(run, coords):
    flow = legacy.load_flow(coords["flow_id"])
    if str(flow["project_id"]) != str(run["project_id"]):
        raise chat_errors.forbidden()


def _identity(run, token, record=None):
    return (Actor(token["issued_to"], CHAT_ACTOR_KIND),
            Origin(CHAT_ORIGIN, run_id=run["run_id"], token_id=token["token_id"],
                   server_call_id=record.server_call_id if record else None))


def chat_read(via, *, token_raw=None, run_hint=None, coords=None, ctx=None):
    run, token = chat_authz.authorize_chat_run(via, token_raw, run_hint)
    with _input(via, run, coords, None, ctx, False) as (inp, record):
        for key in ("flow_id", "node_key", "slot"):
            required_text(inp.get(key), key, "invalid_request")
        _project(run, inp)
        actor, origin = _identity(run, token, record)
        body = success_body(read.read_card(inp["flow_id"], inp["node_key"], inp["slot"], actor, origin))
        if via == chat_authz.API:
            check_size(inp, body)
        return body


def chat_edit(via, *, token_raw=None, run_hint=None, coords=None, request_body=None, ctx=None, locale="ko"):
    run, token = chat_authz.authorize_chat_run(via, token_raw, run_hint)
    with _input(via, run, coords, request_body, ctx, True) as (inp, record):
        actor, origin = _identity(run, token, record)
        key = call_ledger.api_key(record) if record else inp.get("idempotency_key")
        holder = chat_gate.TicketHolder()
        req = ReviseRequest(inp.get("flow_id"), inp.get("node_key"), inp.get("slot"),
                            inp.get("expected_doc_id"), inp.get("expected_revision"), inp.get("content"),
                            actor, origin, idempotency_key=key, edit_reason=CHAT_EDIT_REASON,
                            recheck=lambda: chat_authz.chat_recheck(run, token, holder),
                            size_guard=chat_size_guard(inp) if via == chat_authz.API else None, locale=locale)
        check_input_complete(req)  # includes CLI client-key grammar, BEFORE mode/project
        if record is None:
            from dataclasses import replace
            req = replace(req, idempotency_key=cli_key(run["run_id"], key))
        kind = chat_authz.effective_write_kind(token)
        if kind != chat_authz.CHAT_WRITE_KIND:
            raise chat_errors.edit_mode_denied(run["run_id"], kind)
        _project(run, inp)
        try:
            outcome = revise(req)
        finally:
            # revise has committed or rolled back before the edit gate can drain.
            chat_gate.release(holder.ticket)
        if outcome.kind == "SAVED":
            contacts.run(outcome.plan)
        elif outcome.kind == "REPLAYED":
            contacts.run_replay(outcome.plan)
        else:
            raise internal_error()
        if outcome.line is PROJECTION_FAILED:
            raise internal_error()
        body = success_body({**outcome.result, "line": outcome.line})
        if via == chat_authz.API:
            assert serialized_length(body) <= tool_result_limit(), "chat edit result exceeds API transport limit"
        return body


def read_card_document_call_handler(run, ctx):
    return chat_errors.tool_result(lambda: chat_read(chat_authz.API, run_hint=run, ctx=ctx))


def revise_card_document_call_handler(run, ctx):
    return chat_errors.tool_result(lambda: chat_edit(chat_authz.API, run_hint=run, ctx=ctx))
