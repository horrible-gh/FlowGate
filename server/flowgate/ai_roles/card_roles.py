"""L-1 §2.7/2.8 and P-1 §4/5: one role write and read-only ai_roles block."""
from flowgate.ai_roles import availability, role_store
from flowgate.ai_roles.account import value_detail, value_json, same_value, valid_value
from flowgate.ai_roles.resolve import applicable_roles, read_card_role_inputs, resolve_role
from flowgate.card_settings import legacy_adapter as legacy, repository
from flowgate.ai_roles.card_init import _require_guard
from flowgate.ai_roles.legacy_adapter import isolated_read

ROLE_FIELDS = {"instruction_role": "instruction", "execution_role": "execution", "review_role": "review",
               "instruction_ai": "instruction", "run_ai": "execution", "review_ai": "review"}
NEW_FIELDS = frozenset(("instruction_role", "execution_role", "review_role"))
PRESERVED_FIELDS = ("instruction_ai", "run_ai", "review_ai", "review_count", "review_basis",
                    "instruction_review_ai", "instruction_review_count", "assignee_kind", "assignee_provider_id")


def _invalid(reason, **details):
    return legacy.GraphApiError(422, "invalid_request", {"errors": [
        {"field": "value", "reason": reason, **details}]}, message="The value does not fit this setting.")


def apply_role_write(flow_id, node_key, kind, name, raw, user_id):
    _require_guard(flow_id)
    role = ROLE_FIELDS[name]
    if raw is None:
        raise _invalid("role_unset_not_allowed")
    if name not in NEW_FIELDS and isinstance(raw, str):
        raw = {"kind": "assigned", "provider_id": raw}
    detail = value_detail(raw)
    if detail:
        raise _invalid("role_value_malformed", detail=detail)
    value = role_store.RoleValue(raw["kind"], raw.get("provider_id"))
    old = role_store.slots(flow_id, node_key).get(role)
    if old and same_value(old.value, value):
        return False
    if value.kind == "assigned":
        cause = availability.check_availability(value.provider_id, availability.ACCOUNT, user_id=user_id)
        if cause:
            raise _invalid("ai_not_selectable", cause=cause)
        value = role_store.RoleValue(value.kind, value.provider_id, availability.display_name(value.provider_id))
    role_store.write_slot(flow_id, node_key, role_store.RoleSlot(role, value, "card_explicit", user_id, role_store._now()))
    if all(r in role_store.slots(flow_id, node_key) for r in applicable_roles(kind)):
        init = role_store.card_init(flow_id, node_key)
        if init is None:
            role_store.create_card_init(flow_id, node_key, "initialized", None)
        elif init.state == "pending_init":
            role_store.mark_initialized(flow_id, node_key)
    return True                                      # settings.write moves version once


def card_role_view(flow_id, node, kind, viewer=None, project_id=None, stored=None):
    try:
        if not isolated_read(role_store.available, "fg_role_available_read"):
            return None
    except Exception:
        pass                                        # unreadable is a view state, not feature off
    inputs = read_card_role_inputs(flow_id, node)
    preserved = stored if stored is not None else inputs.stored
    roles = {}
    for role in applicable_roles(kind):
        result = resolve_role(flow_id, node, role, project_id=project_id, inputs=inputs)
        slot = inputs.slots.get(role) if not inputs.store_error else None
        state = {"ai": "assigned_ok", "unavailable": "assigned_unavailable", "person": "not_used",
                 "no_review": "not_used"}.get(result.outcome, "unset")
        name = availability.display_name(result.provider_id, slot.value.provider_name if slot else None) if result.provider_id else None
        roles[role] = {"state": state, "outcome": result.outcome, "source": result.source,
                       "legacy_flag": result.legacy_flag, "provider_id": result.provider_id,
                       "display_name": name,
                       "cause": "store_error" if result.source == "store_error" else result.cause,
                       "selected": value_json(slot.value) if slot and valid_value(slot.value) else None}
    try:
        options = availability.selectable_ais(viewer)
        options_state = "ok"
    except Exception:
        options, options_state = [], "unreadable"
    preserved = {key: ((inputs.node or node).get(key) if key.startswith("assignee_") else preserved.get(key)) for key in PRESERVED_FIELDS}
    return {"card_init_state": None if inputs.store_error else (inputs.init.state if inputs.init else "legacy"),
            "has_legacy_roles": any(r["legacy_flag"] for r in roles.values()), "roles": roles,
            "preserved": preserved, "options_state": options_state, "options": options}
