"""0126 T#1: Graph document decisions, independent of execution/final approval.

GET returns a context to echo unchanged. POST uses a card-wide version and a durable
request key. The Flow guard serializes decisions, document saves, and Graph commands;
all cascaded badges, history, requests and audit rows commit together. T#2 consumes
revision-bound human evidence; T#3 owns recovery before rejection of completed work.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from modules.flow_gate.db import documents as db_documents, events as db_events
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.graph_workflow.services import canvas_events
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard, with_read_snapshot
from modules.flow_gate.api.v1.events.publisher import FlowEvent
from flowgate.card_completion import gates, outputs, own_effects
from flowgate.card_settings.report_baseline import fingerprint
from . import decision_context as context, decision_repository as repository

BODY_KEYS = {"action", "doc_id", "expected_revision", "expected_decision_version", "expected_context", "request_key"}
REJECTION_REASON = "graph_document_rejected"
APPROVED = ("approved", "wf_done")


def parse_body(body):
    errors = []
    if not isinstance(body, dict):
        return [{"field": "body", "reason": "not_an_object"}]
    errors.extend({"field": k, "reason": "unknown_field"} for k in sorted(set(body) - BODY_KEYS))
    if body.get("action") not in ("approve", "reject"):
        errors.append({"field": "action", "reason": "invalid_action"})
    for k in ("expected_revision", "expected_decision_version"):
        if type(body.get(k)) is not int or body[k] < 0:
            errors.append({"field": k, "reason": "required_version"})
    if not isinstance(body.get("doc_id"), str) or not body["doc_id"].strip():
        errors.append({"field": "doc_id", "reason": "required"})
    if not isinstance(body.get("expected_context"), dict):
        errors.append({"field": "expected_context", "reason": "required"})
    if not isinstance(body.get("request_key"), str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", body["request_key"]):
        errors.append({"field": "request_key", "reason": "invalid_key"})
    return errors


def _view(group, node_key, user_id):
    flow_id = group["group_id"]
    node, row, ctx, docs = context.current(flow_id, node_key)
    lock = None
    try:
        context.require_editable(flow_id, node_key, docs)
    except GraphApiError as exc:
        lock = {"code": exc.code, "params": exc.params}
    shown = {}
    for slot, doc in docs.items():
        mark = ctx["documents"][slot]
        shown[slot] = None if not doc else {
            "doc_id": doc["doc_id"], "doc_type": doc.get("type_code"), "revision_no": mark["revision_no"],
            "doc_review_status": doc.get("doc_review_status"),
            **repository.projection(flow_id, node_key, slot, doc,
                                    {k: v for k, v in mark.items() if k != "doc_review_status"}),
        }
    result = {"flow_id": flow_id, "node_key": node_key, "context": ctx,
              "decision_version": repository.version(flow_id, node_key), "documents": shown,
              "lock": lock, "capabilities": {}}
    for slot in docs:
        result["capabilities"][slot] = {}
        for action in ("approve", "reject"):
            try:
                context.authorize(user_id, group, action)
                if lock:
                    raise GraphApiError(409, lock["code"], lock["params"])
                _targets(flow_id, node_key, node, docs, slot, action)
                reason = None
            except GraphApiError as exc:
                reason = {"code": exc.code, "params": exc.params}
            result["capabilities"][slot][action] = {"allowed": reason is None, "reason": reason}
    return result


def read(group, node_key, user_id):
    return with_read_snapshot(lambda: _view(group, node_key, user_id))


def _targets(flow_id, node_key, node, docs, slot, action):
    doc = docs.get(slot)
    if not doc:
        context.refuse("document_slot_missing", slot=slot)
    wanted = outputs.instruction_type(flow_id, node)
    separate = bool(wanted) and not outputs._one_stage(flow_id, node, (docs.get("report") or {}).get("doc_id"), wanted)
    if slot == "instruction" and not wanted:
        context.refuse("document_slot_not_required", slot=slot)
    targets = [(slot, doc, None)]
    if slot == "report" and action == "approve" and separate:
        instruction = docs.get("instruction")
        if not instruction or instruction.get("type_code") != wanted:
            context.refuse("document_required_output_missing", slot="instruction")
        targets.insert(0, ("instruction", instruction, doc["doc_id"]))
    if slot == "instruction" and action == "reject":
        report = docs.get("report")
        if report and report.get("doc_review_status") in APPROVED:
            targets.append(("report", report, doc["doc_id"]))
    stale = outputs.legacy.stale_documents(flow_id, node_key)
    for target_slot, target, _cause in targets:
        if target["doc_id"] in stale:
            context.refuse("document_decision_stale_document", slot=target_slot)
        try:
            gates.require_body(target)
            if action == "approve":
                gates.require_test_gate(target)
        except gates.GateRefused as exc:
            context.refuse("document_decision_gate_refused", slot=target_slot, detail=str(exc))
        # TR2 applies source outside this transaction. T#2 must connect the strong finalize
        # hook; saving an approved badge without it would bypass the apply gate.
        if action == "approve" and target.get("type_code") == "TR2":
            if not own_effects._tr2_succeeded(target["doc_id"], int(target.get("revision_no") or 0)):
                context.refuse("document_decision_effect_required", slot=target_slot, continuation="T#2")
    if action == "reject":
        context.require_rejection_safe(flow_id, node_key, docs)
    return targets


def _record(group, node_key, slot, doc, action, cascade, user_id, request_id, version, request_key, ctx):
    flow_id = group["group_id"]
    decision_id = "gdd_" + uuid.uuid4().hex
    mark = fingerprint(doc)
    before = doc.get("doc_review_status")
    after = "approved" if action == "approve" else "rejected"
    now = now_iso()
    reason = REJECTION_REASON if action == "reject" else None
    cause = "human_approved" if action == "approve" else "human_rejected"
    data = {"decision_id": decision_id, "request_id": request_id, "group_id": flow_id,
            "node_key": node_key, "slot": slot, "doc_id": doc["doc_id"],
            "revision_no": mark["revision_no"], "document_mark_json": json.dumps(mark),
            "decision_version": version, "action": action, "before_state": before, "after_state": after,
            "actor_kind": "user", "actor_id": user_id, "cause": cause, "reason": reason,
            "request_key": request_key, "cascade_from": cascade, "created_at": now}
    repository.append(data)
    fields = {"doc_review_status": after, "updated_at": now}
    if doc.get("status") == "draft":
        from modules.flow_gate.workflow.transition_rules import get_doc_rule
        rule = get_doc_rule("draft", "submit")
        if rule:
            fields["status"] = rule.next_state
    if action == "reject":
        history = doc.get("rejection_history") or []
        if isinstance(history, str):
            history = json.loads(history)
        if not isinstance(history, list):
            context.refuse("document_rejection_history_invalid", doc_id=doc["doc_id"])
        fields["rejection_reason"] = reason
        fields["rejection_history"] = json.dumps([*history, {
            "rejection_id": decision_id, "reason": reason, "rejected_at": now, "rejected_by": user_id,
            "source": "graph_document_decision", "decision_id": decision_id, "revision_no": mark["revision_no"],
        }], ensure_ascii=False)
    else:
        fields["rejection_reason"] = None
    if not db_documents.update(doc["doc_id"], fields):
        context.refuse("document_decision_context_changed", doc_id=doc["doc_id"])
    fresh = db_documents.get_by_id(doc["doc_id"])
    if fingerprint(fresh) != mark:
        context.refuse("document_decision_context_changed", doc_id=doc["doc_id"])
    db_events.insert_event(doc["doc_id"], "graph_document_decision", note=json.dumps(data, ensure_ascii=False))
    payload = {"flow_id": flow_id, "node_key": node_key, "doc_id": doc["doc_id"], "slot": slot,
               "doc_review_status": after, "previous_status": before, "revision_no": mark["revision_no"],
               "decision_version": version, "decision_id": decision_id, "request_id": request_id,
               "actor_kind": "user", "actor_id": user_id, "cascade_from": cascade,
               "run_id": ctx["run_id"], "attempt_id": ctx["attempt_id"]}
    canvas_events._queue(FlowEvent(event_type="doc_review_status_changed", payload=payload,
                                  audience="*", project=group.get("project_id"), group_id=flow_id, doc_id=doc["doc_id"]))


def write(group, node_key, slot, body, user_id):
    """All preconditions are re-read under the same durable Flow guard as the write."""
    flow_id = group["group_id"]
    context.authorize(user_id, group, body["action"])
    canonical = json.dumps({"slot": slot, "actor_id": user_id, **body}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()

    def decide():
        context.authorize(user_id, group, body["action"])
        if not repository.available():
            context.refuse("document_decisions_unavailable")
        receipt = repository.request(flow_id, node_key, body["request_key"])
        if receipt:
            if receipt["request_hash"] != digest:
                context.refuse("document_decision_request_conflict", request_key=body["request_key"])
            result = json.loads(receipt["result_json"])
            return {**result, "replayed": True, "current": _view(group, node_key, user_id)}
        node, row, ctx, docs = context.current(flow_id, node_key)
        context.require_editable(flow_id, node_key, docs)
        doc = docs.get(slot)
        if not doc or doc["doc_id"] != body["doc_id"]:
            context.refuse("document_slot_mismatch", slot=slot)
        if body["expected_revision"] != int(doc.get("revision_no") or 0) or body["expected_context"] != ctx \
                or body["expected_decision_version"] != repository.version(flow_id, node_key):
            context.refuse("document_decision_context_changed", current=_view(group, node_key, user_id))
        targets = _targets(flow_id, node_key, node, docs, slot, body["action"])
        version = repository.version(flow_id, node_key) + 1
        request_id = "gdr_" + uuid.uuid4().hex

        def persist():
            repository.insert_request({"request_id": request_id, "group_id": flow_id, "node_key": node_key,
                "request_key": body["request_key"], "request_hash": digest, "decision_version": version,
                "actor_id": user_id, "context_json": json.dumps(ctx), "result_json": "{}", "created_at": now_iso()})
            for target_slot, target, cascade in targets:
                context.authorize(user_id, group, body["action"])
                _record(group, node_key, target_slot, target, body["action"], cascade,
                        user_id, request_id, version, body["request_key"], ctx)
            result = {**_view(group, node_key, user_id), "request_id": request_id, "replayed": False,
                      "changed_slots": [s for s, _d, _c in targets]}
            repository.finish_request(request_id, result)
            canvas_events.publish_canvas_changed(project_id=group.get("project_id"), flow_id=flow_id,
                reason="documents", actor_user_id=user_id, origin_client_id=None, node_keys=[node_key])
            return result

        ts = next((d for _s, d, _c in targets if d.get("type_code") == "TS"), None)
        if body["action"] == "approve" and ts:
            try:
                result = own_effects.run(ts, flow_id=flow_id, issuer=user_id, permissions=context.permissions(user_id),
                                         revision=int(ts.get("revision_no") or 0), write=persist)
                # Admission may bind a newly created TSR to the card. Return and replay
                # the state after that effect, still inside the same transaction.
                result = {**result, **_view(group, node_key, user_id)}
                repository.finish_request(request_id, result)
                return result
            except (own_effects.EffectRefused, own_effects.EffectRevisionMoved) as exc:
                context.refuse("document_decision_effect_refused", detail=str(exc))
        return persist()

    return with_flow_guard(flow_id, decide)
