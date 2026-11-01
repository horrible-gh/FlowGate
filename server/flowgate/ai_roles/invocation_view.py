"""P-1 §8 read model. Explicit allowlist; no private columns in the response."""
import json
import logging
import re
from flowgate.ai_roles import invocations, model_evidence, record_store

log = logging.getLogger(__name__)


def query(params):
    errors, values = [], {}
    for key in params:
        if key not in ("limit", "before_seq", "before_inv_id"):
            errors.append({"field": "query." + key, "reason": "unknown_field"})
    for key, default, maximum in (("limit", 50, 200), ("before_seq", None, None)):
        text = params.get(key)
        if text is None:
            values[key] = default
        elif not re.fullmatch(r"[+-]?[0-9]+", text):
            errors.append({"field": "query." + key, "reason": "not_an_integer"})
        else:
            number = int(text)
            values[key] = number
            if number < 1 or (maximum and number > maximum):
                errors.append({"field": "query." + key, "reason": "out_of_range"})
    for a, b in (("before_seq", "before_inv_id"), ("before_inv_id", "before_seq")):
        if a in params and b not in params:
            errors.append({"field": "query." + b, "reason": "required"})
    values["before_inv_id"] = params.get("before_inv_id")
    return values, errors


def _config_state(record):
    if not record.get("provider_fingerprint"):
        return "unknown"
    try:
        from modules.flow_gate.db.connection import get_store
        provider = get_store()._fetch_one(
            "SELECT exec_type, kind, cli_command, api_base_url, api_model FROM ai_providers WHERE provider_id = ?",
            [record["actual_ai_id"]])
        if provider is None:
            return "provider_missing"
        current = invocations.fingerprint(provider)
        if current is None:
            return "unknown"
        return "unchanged" if current == record["provider_fingerprint"] else "changed"
    except Exception:
        return "unknown"


def item(record):
    r = record
    keys = ("inv_id", "create_seq", "ai_run_id", "launch_no", "hop_no", "graph_exec_id", "origin_kind", "purpose",
            "target_unresolved_reason", "round_no", "requested_ai_id", "role_source", "legacy_selected_provider_source",
            "model_capability", "result", "result_detail", "exit_code", "timeout_kind", "run_end_reason", "run_stop_code",
            "started_at", "ended_at", "created_at")
    out = {key: r.get(key) for key in keys}
    out["role"] = r.get("inv_role")
    out["target"] = ({field: r.get("target_" + column) for column, field in invocations.TARGET.items()}
                     if r["origin_kind"] != "non_card" else None)
    out["assignment_ref"] = ({k: out["target"][k] for k in ("attempt_id", "stage", "seq")}
                             if r["origin_kind"] == "card_assigned" else None)
    out["qa"] = ({k: r.get("qa_" + k) for k in ("item_id", "question_id", "asker_ai_run_id", "asker_inv_id", "card_class")}
                 if r["origin_kind"] == "card_qa" else None)
    actual = r.get("actual_ai_id")
    out["actual_ai"] = {"id": actual, "name": r.get("actual_ai_name")} if actual else None
    out["provider_match"] = "not_called" if not actual else "same" if r.get("requested_ai_id") == actual else "different"
    out["provider_config"] = {"exec_type": r.get("provider_exec_type"), "kind": r.get("provider_kind"),
                              "shell_kind": r.get("provider_shell_kind"), "config_state": _config_state(r)}
    out["requested_model"] = {k: r.get("requested_model_" + k) for k in ("state", "value", "reason")}
    try:
        vals = json.loads(r.get("confirmed_model_values_json") or "[]")
        if not isinstance(vals, list):
            vals = []
    except (TypeError, ValueError):
        vals = []
    out["confirmed_model"] = {"state": r.get("confirmed_model_state"), "values": vals,
        "truncated": bool(r.get("confirmed_model_truncated")), "coverage": r.get("confirmed_coverage"),
        "calls": {k: r.get("calls_" + k) for k in ("total", "reported", "absent", "unreadable", "failed")}
        if r.get("calls_total") is not None else None, "bad_lines": r.get("bad_lines"), "reason": r.get("unverifiable_reason")}
    out["compare"] = model_evidence.compare(out["requested_model"], out["confirmed_model"], bool(actual))
    out["model_call_failure"] = ({"kind": r["model_call_failure_kind"], "call_no": r.get("model_call_no"),
        "turn_no": r.get("model_turn_no"), "http_status": r.get("model_http_status")}
        if r.get("model_call_failure_kind") else None)
    out["link"] = {"kind": r.get("link_kind"), "prev_inv_id": r.get("prev_inv_id")}
    root = record_store.first(r["unit_key_hash"])
    out["chain_root_id"] = (root or {}).get("inv_id") or r.get("chain_root_id")
    if out["chain_root_id"] != r.get("chain_root_id"):
        log.warning("role invocation root differs for %s", r["inv_id"])
    return out


def payload(flow_id, node_key, limit=50, before_seq=None, before_inv_id=None):
    available = record_store.available()
    rows = record_store.page_for_node(flow_id, node_key, limit + 1, before_seq, before_inv_id) if available else []
    more, rows = len(rows) > limit, rows[:limit]
    return {"flow_id": flow_id, "node_key": node_key, "available": available,
            "invocations": [item(r) for r in rows], "next_cursor":
            {"before_seq": rows[-1]["create_seq"], "before_inv_id": rows[-1]["inv_id"]} if more else None}
