"""L-1 §2.3 / P-1 §2: three account defaults, saved atomically with preservation."""
from fastapi import HTTPException
from flowgate.ai_roles import availability, role_store


def value_detail(value):
    """P-1 value grammar, shared by account and card writes. No normalization of ids."""
    if not isinstance(value, dict):
        return "not_an_object"
    if "kind" not in value:
        return "kind_missing"
    if value["kind"] not in ("assigned", "not_used"):
        return "unknown_kind"
    pid = value.get("provider_id")
    if value["kind"] == "assigned" and (not isinstance(pid, str) or not pid.strip() or pid != pid.strip()):
        return "provider_id_required"
    if value["kind"] == "not_used" and pid is not None:
        return "provider_id_not_allowed"
    return None


def valid_value(value):
    return isinstance(value, role_store.RoleValue) and value_detail(value_json(value)) is None


def value_json(value):
    return {"kind": value.kind, "provider_id": value.provider_id}


def same_value(left, right):
    return left is not None and (left.kind, left.provider_id) == (right.kind, right.provider_id)


def _require_available():
    try:
        available = role_store.available()
    except Exception as exc:
        raise HTTPException(503, detail={"code": "ai_roles_unreadable"}) from exc
    if not available:
        raise HTTPException(409, detail={"code": "ai_roles_unavailable"})


def account_view(user_id):
    _require_available()
    try:
        rec = role_store.account_roles(user_id)
        options = availability.selectable_ais(user_id)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "ai_roles_unreadable"}) from exc
    roles = {}
    for role in role_store.ROLES:
        value = rec.values.get(role) if rec else None
        result = {"state": "unset", "value": None, "display_name": None, "cause": None}
        if valid_value(value):
            result["value"] = value_json(value)
            if value.kind == "not_used":
                result["state"] = "not_used"
            else:
                cause = availability.check_availability(value.provider_id, availability.ACCOUNT, user_id=user_id)
                result.update(state="assigned_unavailable" if cause else "assigned_ok", cause=cause,
                              display_name=availability.display_name(value.provider_id, value.provider_name))
        roles[role] = result
    return {"ok": True, "setup_completed": rec is not None,
            "setup_completed_at": rec.setup_completed_at if rec else None,
            "updated_at": rec.updated_at if rec else None, "roles": roles,
            "selectable_count": len(options), "options": options}


def parse_and_validate_save(user_id, body):
    _require_available()
    try:
        current = role_store.account_roles(user_id)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "ai_roles_unreadable"}) from exc
    errors, values = [], {}
    if not isinstance(body, dict):
        errors.append({"field": "roles", "reason": "required"})
        body = {}
    errors += [{"field": key, "reason": "unknown_field"} for key in sorted(set(body) - {"roles"})]
    submitted = body.get("roles")
    if submitted is None:
        if not errors or errors[-1] != {"field": "roles", "reason": "required"}:
            errors.append({"field": "roles", "reason": "required"})
    elif not isinstance(submitted, dict):
        errors.append({"field": "roles", "reason": "malformed", "detail": "not_an_object"})
    else:
        errors += [{"field": "roles." + key, "reason": "unknown_field"}
                   for key in sorted(set(submitted) - set(role_store.ROLES))]
        for role in role_store.ROLES:
            raw = submitted.get(role)
            field = "roles." + role
            if raw is None:
                errors.append({"field": field, "reason": "required"})
                continue
            detail = value_detail(raw)
            if detail:
                errors.append({"field": field, "reason": "malformed", "detail": detail})
                continue
            value = role_store.RoleValue(raw["kind"], raw.get("provider_id"))
            old = current.values.get(role) if current else None
            if same_value(old, value):
                value = old
            elif value.kind == "assigned":
                cause = availability.check_availability(value.provider_id, availability.ACCOUNT, user_id=user_id)
                if cause:
                    errors.append({"field": field, "reason": "ai_not_selectable", "cause": cause})
                    continue
                value = role_store.RoleValue(value.kind, value.provider_id,
                                            availability.display_name(value.provider_id))
            values[role] = value
    if errors:
        raise HTTPException(422, detail={"code": "validation_failed", "errors": errors})
    return values


def save(user_id, body):
    values = parse_and_validate_save(user_id, body)
    try:
        role_store.save_account_roles(user_id, values)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "ai_roles_unreadable"}) from exc
    return account_view(user_id)
