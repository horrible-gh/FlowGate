"""L-3 §2.1: one read-only adapter for automatic Graph question responders."""
from flowgate.ai_roles import record_store, resolve, role_store
from flowgate.ai_roles.account import valid_value
from flowgate.card_settings import fields, legacy_adapter as legacy, repository


def choose(flow_id, node, asker):
    """(provider, source, handoff reason, default tail, card class)."""
    try:
        kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
        new, inputs = False, None
        if "review" in resolve.applicable_roles(kind) and role_store.available():
            inputs = resolve.read_card_role_inputs(flow_id, node)
            if inputs.store_error:
                return None, None, "card_settings_unreadable", False, None
            new = "review" in inputs.slots or (inputs.init is not None and
                                                inputs.init.state in ("initialized", "pending_init"))
        project = legacy.project_of(flow_id)
        if not new:
            stored = repository.values(flow_id, node["node_key"])
            pid, src = legacy.independent_question_responder(project, asker, [
                (stored.get("review_ai"), "card_review_ai"), (legacy.default_reviewer(project), "default")])
            return pid, src, None if pid else "no_independent_responder", True, "qa_legacy"
        slot = inputs.slots.get("review")
        if slot is not None and (not valid_value(slot.value) or slot.source not in role_store.SLOT_SOURCES):
            return None, None, "card_settings_unreadable", False, "qa_new"
        designated = slot.value.provider_id if slot is not None and slot.value.kind == "assigned" else None
        if not designated:
            reason = "responder_not_designated"
        elif not isinstance(asker, str) or not asker.strip():
            reason = "asker_unknown"
        elif designated == asker:
            reason = "designated_is_asker"
        else:
            from modules.flow_gate.services.ai_invoke import review
            enabled = {p.get("id") for p in review._enabled_provider_chain(project)}
            if designated in enabled:
                return designated, "card_review_role", None, False, "qa_new"
            reason = "designated_not_enabled"
        return None, None, reason, False, "qa_new"
    except Exception:
        return None, None, "card_settings_unreadable", False, None


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
