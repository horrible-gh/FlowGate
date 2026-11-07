"""P#2 ordered Agent validation and post-commit response composition."""
import json
import logging
from dataclasses import replace
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.services import graph_source_hooks, register_binding
from . import target, contacts, agent_binding, agent_errors
from .contracts import Actor, Origin, ReviseRequest, PROJECTION_FAILED
from .errors import GraphApiError, invalid, internal_error
from .revise import revise

_log = logging.getLogger(__name__)
MODEL_FIELDS = ("content", "expected_revision", "edit_reason", "rejection_response", "rejection_id",
                "rejection_review_id", "node_title")
CLI_FIELDS = frozenset(MODEL_FIELDS) | {"action", "project", "module", "group_name", "group", "doc_id",
    "doc_path", "linked_doc_id", "body_sha256", "body_chars", "force_encoding_reason", "dry_run", "review_id"}


class BoundaryRejected(Exception):
    def __init__(self, status, value, *, source=None):
        self.status, self.value = agent_errors.normalize(status, value, source=source)


def normalize(status, value, **kwargs):
    return agent_errors.normalize(status, value, **kwargs)


def edit_schema(run, schema):
    import copy
    value = copy.deepcopy(schema)
    doc_id = (run.get("register_context") or {}).get("doc") or run.get("doc_ref")
    doc = documents.get_by_id(doc_id) if doc_id else None
    # Unavailable stays card-shaped; dispatch will fail closed rather than advertise legacy CAS.
    if doc is not None and target.classify_card_slot(doc) == target.NON_CARD:
        value["properties"].pop("expected_revision", None)
        value["required"] = [k for k in value["required"] if k != "expected_revision"]
    return value


def _record_run_rejection(run, record):
    """Keep a token <-> run-context (2nd stage) rejection on the run it was judged against.

    flowgate.v02.0136 T0004 T#3 (NR0003 rev2 §2.1/§6.2-4). The 1st stage
    (inbox._check_context_binding) already lands in run["register_errors"] through
    register_binding.record_for_run; this stage only logged, so its 403s never reached the run
    diagnostics or, at finalize, register_context_failures. `run` is the live record the binder
    just read (the token's ai_run_id lookup, or the API provider loop's own run), and
    worker._bind_register_context has already written the structured log line -- so this only
    appends, once: a record already present (same object or correlation id, e.g. appended by
    worker._inbox_register) is not stacked again. Nothing secret is added; the record carries
    hashes and fingerprints only.
    """
    errors = run.get("register_errors")
    if not isinstance(errors, list):
        errors = run["register_errors"] = []
    correlation_id = record.get("correlation_id")
    for item in errors:
        if item is record or (correlation_id and isinstance(item, dict)
                              and item.get("correlation_id") == correlation_id):
            return
    errors.append(record)


def check_boundary(raw, body, *, run=None, model=False):
    from modules.flow_gate.api import inbox_routes as inbox
    try:
        token = inbox.token_service.verify(raw)
    except HTTPException as exc:
        raise BoundaryRejected(exc.status_code, {"error_message": str(exc.detail)}) from exc
    if isinstance(body, dict):
        project, module = body.get("project"), body.get("module")
        group, doc_id = body.get("group_name") or body.get("group"), body.get("doc_id")
        try:
            group = inbox._normalize_group_name(str(project), str(module), str(group)) if group else None
            doc_id = inbox._normalize_doc_id(group, str(doc_id)) if doc_id and group else doc_id
        except (TypeError, ValueError):
            pass  # The binding boundary reports routing faults, before payload validation.
        # First retain the existing axis diagnostics; then compare the supplied group against DB ownership.
        failed = inbox._check_context_binding(action_handler=body.get("action") or "edit", project=project,
            doc=doc_id, token_rec=token, target_doc_id=doc_id, check_graph=False)
        if failed is None and group != register_binding.group_of_doc(doc_id):
            failed = inbox._check_context_binding(action_handler=body.get("action") or "edit", project=project,
                group=group, doc=doc_id, token_rec=token, target_doc_id=doc_id, check_graph=False)
        actual_doc = documents.get_by_id(doc_id) if doc_id else None
        if failed is None and actual_doc and actual_doc.get("project_id") not in {None, project}:
            failed = inbox._check_context_binding(action_handler=body.get("action") or "edit",
                project=actual_doc["project_id"], doc=doc_id, token_rec=token, target_doc_id=doc_id, check_graph=False)
        if failed is not None:
            raise BoundaryRejected(failed.status_code, json.loads(failed.body))
    if run is None and token.get("ai_run_id"):
        from modules.flow_gate.services import ai_invoke_service
        run = ai_invoke_service.get_run_record(token["ai_run_id"])
        if run is None:
            raise BoundaryRejected(403, {"error_message": register_binding.BINDING_MESSAGE,
                "error": {"code": "forbidden", "details": {"reason": "token_not_current"}}})
    if run is not None:
        from modules.flow_gate.services.ai_invoke import worker
        try:
            worker._bind_register_context(run, raw)
        except worker._RegisterBindingRejected as exc:
            if exc.record is not None:
                _record_run_rejection(run, exc.record)
            raise BoundaryRejected(exc.response.get("http_status", 403), exc.response, source=exc.source) from exc
        if token.get("ai_run_id") and run.get("run_id") != token["ai_run_id"]:
            raise BoundaryRejected(403, {"error_message": register_binding.BINDING_MESSAGE,
                "error": {"code": "forbidden", "details": {"reason": "token_not_current"}}})
    try:
        graph_source_hooks.require_writable(token, "inbox_edit")
    except graph_source_hooks.GraphSourceError as exc:
        raise BoundaryRejected(409, {"error": {"code": exc.code, "message": exc.message,
            "params": {}, "details": exc.details}}) from exc
    if not isinstance(body, dict):
        raise invalid("invalid_request", "body", "invalid_format")
    return token


def read_content(body, token):
    from modules.flow_gate.api import inbox_routes as inbox
    keys = [k for k in ("content", "doc_path") if k in body]
    if len(keys) != 1:
        raise invalid("invalid_content", "content", "required" if not keys else "invalid_format")
    field = keys[0]
    value = body[field]
    if value is None:
        raise invalid("invalid_content", field, "required")
    if not isinstance(value, str):
        raise invalid("invalid_content", field, "invalid_type")
    if field == "doc_path":
        try:
            scratch = token.get("scratch_dir") or inbox._token_scratch_dir(token["project"], token["token_id"])
            path, root = Path(value), Path(scratch).resolve()
            if not value or not path.is_absolute() or not inbox.validate_doc_path(value, str(root)) or not path.resolve().is_relative_to(root):
                raise ValueError("jail")
            return inbox._submission_text(str(path), None)
        except (OSError, ValueError, UnicodeError):
            raise invalid("invalid_content", "doc_path", "invalid_format") from None
    return value


_CLI = object()

def prepare(raw, body, *, model_input=_CLI, run=None):
    token = check_boundary(raw, body, run=run)
    source = body if model_input is _CLI else model_input
    if not isinstance(source, dict):
        raise invalid("invalid_request", "body", "invalid_format")
    doc = documents.get_by_id(token.get("doc_ref"))
    if doc is None:
        raise agent_binding.missing()
    kind = agent_binding.classification(doc)
    if kind == target.NON_CARD:
        from modules.flow_gate.graph_workflow.store import run_store
        if not run_store.available():
            raise agent_binding.missing("card_edit_unavailable")
        if run_store.node_run_by_token(token["token_id"]):
            kind = target.CARD  # A mapped card token never falls through to legacy on missing rows.
    if not isinstance(source, dict):
        raise invalid("invalid_request", "body", "invalid_format")
    unknown = sorted(set(source) - (set(MODEL_FIELDS) if model_input is not _CLI else CLI_FIELDS)) if kind == target.CARD else (["expected_revision"] if "expected_revision" in source else [])
    if unknown:
        raise GraphApiError(422, "invalid_request", {"errors": [{"field": k, "reason": "unknown_field"} for k in unknown]})
    binding = agent_binding.bind(token, doc) if kind == target.CARD else None
    expected = source.get("expected_revision")
    if binding:
        if expected is None:
            raise invalid("expected_revision_missing", "expected_revision", "required")
        if type(expected) is not int:
            raise invalid("expected_revision_missing", "expected_revision", "invalid_type")
        if expected < 0:
            raise invalid("expected_revision_missing", "expected_revision", "invalid_format")
    from modules.flow_gate.graph_workflow.services import result_slot_binding
    result_slot_binding.validate(token, expected)
    if kind == target.NON_CARD:
        from .legacy_edit import read_submission
        submission = read_submission(source, token, body)
        reason = source.get("edit_reason")
    else:
        submission = read_content(source, token)
        reason = source.get("edit_reason")
        if reason is None:
            raise invalid("invalid_content", "edit_reason", "required")
        if not isinstance(reason, str):
            raise invalid("invalid_content", "edit_reason", "invalid_type")
        if reason not in {"rejected", "qna_followup", "user_comment", "worker_self"}:
            raise invalid("invalid_content", "edit_reason", "invalid_format")
        if reason == "rejected":
            if not isinstance(source.get("rejection_response"), str) or not source["rejection_response"].strip():
                raise invalid("invalid_content", "rejection_response", "required")
            from modules.flow_gate.workflow.pipeline_service import is_review_row_id
            for field in ("rejection_id", "rejection_review_id", "review_id"):
                if field in source:
                    value = source[field]
                    valid = bool(isinstance(value, str) and value.strip()) if field == "rejection_id" else is_review_row_id(value)
                    if not valid:
                        raise invalid("invalid_content", field, "invalid_format")
    from modules.flow_gate.api import inbox_routes as inbox
    locale = token.get("continuation_locale") or (run or {}).get("locale") or "ko"
    req = ReviseRequest(doc.get("group_id"), (binding or {}).get("node_key", ""), (binding or {}).get("slot", ""),
        token["doc_ref"], expected if binding else 0, submission, Actor(token["issued_to"], "worker"),
        Origin("agent", token.get("ai_run_id"), token["token_id"], token=token), token["token_id"], reason,
        source.get("linked_doc_id"), {"submission": dict(source), "encoding": {**{k: source.get(k) for k in ("body_sha256", "body_chars", "force_encoding_reason")},
            "fields": {"edit_reason": reason, "rejection_response": source.get("rejection_response")}}},
        inbox._truthy(source.get("dry_run")), locale=inbox.template_provision.normalize_locale(locale))
    def recheck():
        check_boundary(raw, body, run=run)
        result_slot_binding.validate(token, expected)
        if binding and agent_binding.bind(token, doc) != binding:
            raise agent_binding.missing()
    return replace(req, recheck=recheck), token, binding


def worker_preflight(run, raw, model_input, context):
    from modules.flow_gate.services.ai_invoke import worker
    # Server routing only; raw input is retained separately through classification/unknown checks.
    body = worker._register_envelope(context, run, {})
    try:
        with graph_source_hooks.write_session():
            prepare(raw, body, model_input=model_input, run=run)
        return None
    except BoundaryRejected as exc:
        return exc.status, exc.value
    except GraphApiError as exc:
        return exc.status, agent_errors.failure(exc)
    except Exception:
        _log.exception("Agent worker preflight failed")
        exc = internal_error()
        return exc.status, agent_errors.failure(exc)


def finish(outcome, req, token, binding):
    from modules.flow_gate.api import inbox_routes as inbox
    from modules.flow_gate.services import node_title_hooks
    if outcome.kind == "VALIDATED":
        return inbox._maybe_dry_run(req.transport_extras["submission"], token, outcome.verdict, req.locale)
    if outcome.kind not in {"SAVED", "REPLAYED"}:
        raise internal_error()
    transitioned = resubmit(outcome, req) if req.edit_reason == "rejected" else False
    if binding and req.edit_reason == "rejected":
        try:
            node, spec = target.check_card({"group_id": req.flow_id}, req.node_key, req.slot)
            outcome.line = target.line_projection(req.flow_id, node, spec)
        except Exception:
            outcome.line = PROJECTION_FAILED
    (contacts.run_replay if outcome.kind == "REPLAYED" else contacts.run)(outcome.plan)
    if outcome.line is PROJECTION_FAILED:
        raise internal_error()
    # Claim and its token_consumed event commit atomically; duplicate consumption is never success.
    with get_store().transaction():
        if not inbox.token_service.consume(token_id=token["token_id"], project_id=token["project"],
                                           doc_id=req.expected_doc_id, require_claim=True):
            raise internal_error()
    try:
        node_title_hooks.after_registration(req.flow_id, req.expected_doc_id, token["token_id"],
            req.transport_extras["submission"].get("node_title"), "edit")
    except Exception:
        _log.warning("Agent node title hint failed", exc_info=True)
    publish_unique(outcome, req, transitioned)
    result = {"ok": True, "server_ts": now_iso(), **outcome.result,
              "message": f"{req.expected_doc_id} registered. You may end the session."}
    if binding:
        result.update(card_binding=binding, line=outcome.line)
    for key in ("_agent_review_status_before", "_agent_checks", "_agent_tr_scope"):
        result.pop(key, None)
    result.setdefault("change_summary", {"changed": None, "error": "summary unavailable"})
    if outcome.kind == "SAVED":
        inbox._record_change_summary(req.expected_doc_id, result["revision_no"], result["change_summary"], req.actor.user_id)
    return JSONResponse(content=result)


def resubmit(outcome, req):
    from modules.flow_gate.db import workflow_sequences, workflow_item_results
    from modules.flow_gate.workflow import pipeline_service as pipeline
    doc = documents.get_by_id(req.expected_doc_id)
    if doc.get("doc_review_status") == "revised":
        # Events publish only after consumption. A prior failed consume leaves this delivery pending.
        return outcome.result.get("_agent_review_status_before") == "rejected"
    own = workflow_sequences.get_item_by_result_doc_id(req.expected_doc_id)
    if own is not None:
        latest = workflow_item_results.get_latest_result_by_item(own["id"])
        registered = (latest is not None and latest.get("registered_doc_id") == req.expected_doc_id
                      and latest.get("status") == "pending_approval"
                      and latest.get("registered_at") == outcome.result["updated_at"])
        if not registered:
            pipeline.register_workflow_result(item_id=own["id"], registered_path=outcome.plan.doc["file_path"],
                registered_doc_id=req.expected_doc_id, registered_at=outcome.result["updated_at"], actor_user_id=req.actor.user_id)
    pipeline.transition_document_review(doc_id=req.expected_doc_id, action="submit", actor_user_id=req.actor.user_id,
                                        user_permissions={"document.update"})
    return doc.get("doc_review_status") == "rejected"


def publish_unique(outcome, req, transitioned):
    from modules.flow_gate.api.v1.events.publisher import publish_event_threadsafe, broadcast_event_threadsafe, FlowEvent
    from modules.flow_gate.api.v1.events.event_types import EventType
    from modules.flow_gate.workflow.pipeline_service import enrich_rejection_history_provenance, parse_rejection_history
    doc = outcome.plan.doc
    base = dict(project=doc["project_id"], group_id=req.flow_id, doc_id=doc["doc_id"], audience=req.actor.user_id)
    items = [(EventType.FILE_EXPLORER_REFRESH, {"operation": "updated", "stored_path": outcome.result.get("stored_path")})]
    if doc["type_code"] != "WP" or outcome.kind == "REPLAYED":
        items.append((EventType.GROUP_VIEW_REFRESH, {"group_id": req.flow_id, "reason": "document_updated"}))
    items.append((EventType.EDIT_MARKER_ADDED, {"doc_id": doc["doc_id"], "revision_no": doc["revision_no"],
                 "edit_reason": req.edit_reason, "linked_doc_id": req.linked_doc_id}))
    for event_type, payload in items:
        try:
            publish_event_threadsafe(FlowEvent(event_type=event_type, payload=payload, **base))
        except Exception:
            _log.warning("Agent event failed type=%s", event_type, exc_info=True)
    if transitioned:
        try:
            fresh = documents.get_by_id(doc["doc_id"])
            broadcast_event_threadsafe(FlowEvent(event_type=EventType.DOC_REVIEW_STATUS_CHANGED,
                payload={"doc_id": doc["doc_id"], "prev_status": "rejected", "next_status": "revised", "rejection_reason": None,
                    "rejection_history": enrich_rejection_history_provenance(parse_rejection_history(fresh.get("rejection_history")))},
                **{**base, "audience": "*"}))
        except Exception:
            _log.warning("Agent review event failed", exc_info=True)
    if outcome.plan.legacy_non_card and doc["type_code"] == "CH" and doc.get("owner_id") != req.actor.user_id:
        try:
            publish_event_threadsafe(FlowEvent(event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
                payload={"operation": "updated", "doc_id": doc["doc_id"], "type": doc["type_code"], "title": doc.get("title"),
                         "status": doc.get("status"), "revision_no": doc["revision_no"]}, **{**base, "audience": doc.get("owner_id")}))
        except Exception:
            _log.warning("Agent CH owner event failed", exc_info=True)


def handle(request, raw, body):
    try:
        # Locale remains a server/request setting; never an actor/binding input.
        req, token, binding = prepare(raw, body)
        if not token.get("continuation_locale"):
            from modules.flow_gate import template_provision
            req = replace(req, locale=template_provision.normalize_locale(request.headers.get("x-locale") or "ko"))
        if binding:
            outcome = revise(req)
        else:
            from .legacy_edit import edit
            outcome = edit(req)
        return finish(outcome, req, token, binding)
    except BoundaryRejected as exc:
        return JSONResponse(status_code=exc.status, content=exc.value)
    except GraphApiError as exc:
        return agent_errors.response(exc)
    except Exception:
        _log.exception("Agent edit failed")
        return agent_errors.response(internal_error())
