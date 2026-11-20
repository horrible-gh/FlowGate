"""L-3 §2.1: one read-only adapter for automatic Graph question responders."""
from flowgate.ai_roles import record_store, resolve, role_store
from flowgate.ai_roles.account import valid_value
from flowgate.card_settings import fields, legacy_adapter as legacy, repository


def candidates(flow_id, node, asker, asker_role=None):
    """Who answers a card question: (picks, failure reason, default_tail, card class).

    flowgate.v02test.0051 T0005 (A1, R 0051.0001 "when the worker asks, the reviewer answers"): every card — legacy or new role
    card — asks its review AI first; when the card has no review AI, or it is the asker's own provider (the actual AI
    that asked, never the requested one), an independent enabled default AI answers. Every pick differs from the
    asker's provider and is enabled; unknown asker provenance picks nothing (a person answers). ``default_tail`` is
    True for both, so the answer service walks the same bounded independent startup chain
    (``review.question_responder_candidates``). The earlier new-card rule (review -> instruction role, the asking ROLE
    excluded, the same provider allowed for another role, never a default) is gone.
    """
    try:
        kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
        new, inputs = False, None
        if "review" in resolve.applicable_roles(kind) and role_store.available():
            inputs = resolve.read_card_role_inputs(flow_id, node)
            if inputs.store_error:
                return [], "card_settings_unreadable", False, None
            new = any(r in inputs.slots for r in ("review", "instruction")) or (
                inputs.init is not None and inputs.init.state in ("initialized", "pending_init"))
        project = legacy.project_of(flow_id)
        if not new:
            stored = repository.values(flow_id, node["node_key"])
            pid, src = legacy.independent_question_responder(project, asker, [
                (stored.get("review_ai"), "card_review_ai"), (legacy.default_reviewer(project), "default")])
            return ([(pid, src)] if pid else []), None if pid else "no_independent_responder", True, "qa_legacy"
        slot = inputs.slots.get("review") if "review" in resolve.applicable_roles(kind) else None
        if slot is not None and (not valid_value(slot.value) or slot.source not in role_store.SLOT_SOURCES):
            return [], "card_settings_unreadable", False, "qa_new"
        reviewer = slot.value.provider_id if slot is not None and slot.value.kind == "assigned" else None
        if not asker:
            return [], "asker_unknown", True, "qa_new"
        from modules.flow_gate.services.ai_invoke import review
        heads = [(reviewer, "card_review_role")] if reviewer else []
        picks = review.question_responder_candidates(
            project, asker, [*heads, (legacy.default_reviewer(project), "default")])
        return picks, None if picks else "no_independent_responder", True, "qa_new"
    except Exception:
        return [], "card_settings_unreadable", False, None


def choose(flow_id, node, asker, asker_role=None):
    picks, reason, tail, card_class = candidates(flow_id, node, asker, asker_role)
    pid, source = picks[0] if picks else (None, "default" if tail else None)
    return pid, source, reason, tail, card_class


def asking_role(run, asked_by_instruction=False):
    records = run.get("invocations") or []
    if not records and run.get("run_id") and record_store.available():
        records = record_store.by_run(run["run_id"])
    records = [r for r in records if r.get("inv_role") in ("instruction", "execution", "review")]
    if records:
        return max(records, key=lambda r: r.get("launch_no") or 0)["inv_role"]
    origin_role = (run.get("invocation_origin") or {}).get("role")
    if origin_role in ("instruction", "execution", "review"):
        return origin_role
    if run.get("document_review_loop"):
        return "review" if run.get("hop_kind") == "review" else "execution"
    return "instruction" if asked_by_instruction else "execution"


def origin(flow_id, node, item, run, source, card_class, provider_id):
    target = {k: None for k in ("graph_run_id", "attempt_id", "stage", "seq", "doc_revision_no")}
    target.update(flow_id=flow_id, node_key=node["node_key"], doc_id=item.get("anchor_doc_id"))
    qa = {"item_id": item["id"], "question_id": item.get("question_id"),
          "asker_ai_run_id": item.get("asker_ai_run_id"), "asker_inv_id": None, "card_class": card_class}
    reason = "asker_record_missing"
    try:
        records = (run.get("invocations", []) if run.get("run_id") == item.get("asker_ai_run_id") else
                   record_store.by_run(item.get("asker_ai_run_id")) if record_store.available() else [])
        records = [r for r in records if r["origin_kind"] in ("card_assigned", "card_legacy_attempt")]
        same = [r for r in records if r.get("actual_ai_id") == item.get("asker_actual_provider_id")]
        if same:
            records = same
        if records:
            rec = max(records, key=lambda r: r["launch_no"])
            if rec["target_group_id"] != flow_id or rec["target_node_key"] != node["node_key"]:
                reason = "asker_target_mismatch"
            else:
                reason, qa["asker_inv_id"] = None, rec["inv_id"]
                for key in ("graph_run_id", "attempt_id", "stage", "seq"):
                    target[key] = rec.get("target_" + key)
                if rec.get("target_doc_id") == target["doc_id"]:
                    target["doc_revision_no"] = rec.get("target_doc_revision_no")
    except Exception:
        pass
    return {"origin_kind": "card_qa", "purpose": "qa_response", "role": "qa_response", "target": target,
            "target_unresolved_reason": reason, "qa": qa, "requested_ai_id": provider_id,
            "role_source": "default_chain" if source == "default" else source}
