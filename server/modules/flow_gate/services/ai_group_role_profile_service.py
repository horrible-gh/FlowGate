"""0120 T#1: group role settings only; does not resolve or mutate card/runtime AI."""
from modules.flow_gate.db import ai_group_role_profiles as repo, templates
from modules.flow_gate.services import ai_group_service as groups


def _group(ai_group_id: str) -> dict:
    group = groups._group_row(ai_group_id)
    if group.get("group_type", "general") != "role":
        raise groups.AIGroupError("ai_role_group_required", 409)
    return group


def _types(locale: str = "ko") -> list[dict]:
    # Use the live global catalog, never card_kind or guessed display-name mappings.
    result = {}
    for row in templates.list_document_types(locale=locale):
        key = row["type_code"]
        if row.get("is_active") and row.get("project_id") is None and key != repo.COMMON:
            result.setdefault(key, {"type_key": key, "label": row.get("type_name") or key})
    return list(result.values())


def options(ai_group_id: str, locale: str = "ko") -> dict:
    _group(ai_group_id)
    return {"types": _types(locale), "providers": [
        {"provider_id": pid, "provider_name": p["name"]}
        for pid, p in groups._available().items()]}


def profiles(ai_group_id: str) -> dict:
    _group(ai_group_id)
    rows = repo.list_profiles(ai_group_id)
    if not any(row["type_key"] == repo.COMMON for row in rows):
        raise groups.AIGroupError("ai_role_profiles_inconsistent", 500)
    available, registry = groups._available(), {}
    items = []
    for row in rows:
        values = {}
        for role in repo.ROLES:
            kind = row[role + "_kind"]
            value = {"kind": kind}
            if kind == "assigned":
                ref = groups._connection(row[role + "_provider_id"], available, registry)
                # The assigned name snapshot survives a deleted or renamed provider.
                ref["provider_name"] = ref["provider_name"] or row[role + "_provider_name"]
                value.update(ref)
            values[role] = value
        items.append({"type_key": row["type_key"], "position": row["position"], "roles": values})
    return {"ai_group_id": ai_group_id, "items": items}


def add(ai_group_id: str, type_key: str) -> dict:
    with repo.get_store().transaction():
        repo.lock_group(ai_group_id)
        _group(ai_group_id)
        # An existing row remains valid even after its catalog type is retired.
        if repo.get(ai_group_id, type_key) is None:
            if type_key == repo.COMMON or type_key not in {t["type_key"] for t in _types()}:
                raise groups.AIGroupError("ai_role_profile_type_invalid")
            repo.create(ai_group_id, type_key)
    return profiles(ai_group_id)


def set_cell(ai_group_id: str, type_key: str, role: str, data: dict) -> dict:
    if role not in repo.ROLES:
        raise groups.AIGroupError("ai_role_profile_role_invalid")
    kind, pid = data.get("kind"), data.get("provider_id")
    kinds = ("unset", "not_used", "assigned") if type_key == repo.COMMON else ("inherit", "not_used", "assigned")
    if kind not in kinds or set(data) - {"kind", "provider_id"}:
        raise groups.AIGroupError("ai_role_profile_value_invalid")
    if kind == "assigned":
        if not isinstance(pid, str) or not pid or pid != pid.strip():
            raise groups.AIGroupError("ai_role_profile_value_invalid")
    elif pid is not None:
        raise groups.AIGroupError("ai_role_profile_value_invalid")
    with repo.get_store().transaction():
        repo.lock_group(ai_group_id)
        _group(ai_group_id)
        row = repo.get(ai_group_id, type_key)
        if row is None:
            raise groups.AIGroupError("ai_role_profile_not_found", 404)
        name = None
        if kind == "assigned":
            provider = groups._available().get(pid)
            if provider is not None:
                name = provider["name"]
            elif row[role + "_kind"] == "assigned" and row[role + "_provider_id"] == pid:
                name = row[role + "_provider_name"]
            else:
                raise groups.AIGroupError("ai_group_provider_unavailable")
        repo.set_cell(ai_group_id, type_key, role, kind, pid, name)
    return profiles(ai_group_id)


def remove(ai_group_id: str, type_key: str) -> dict:
    with repo.get_store().transaction():
        repo.lock_group(ai_group_id)
        _group(ai_group_id)
        if type_key == repo.COMMON:
            raise groups.AIGroupError("ai_role_profile_common_required", 409)
        repo.delete(ai_group_id, type_key)
    return profiles(ai_group_id)
