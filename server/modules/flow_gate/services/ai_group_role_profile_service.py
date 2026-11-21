"""group role settings only; does not resolve or mutate card/runtime AI.

(T, A-server): each profile row also keeps a default review count (``review_count``,
migration 191). NULL on a type row inherits the common row; NULL on the common row is the existing card default 0;
0 is an explicit "no review". It has its own write (``set_review_count``), separate from the AI cells, and is copied
into a card only when the card is created (``flowgate.ai_roles.card_init``) — existing cards never change.
"""
from modules.flow_gate.db import ai_group_role_profiles as repo
from modules.flow_gate.graph_workflow.services.node_title_names import PALETTE_KEY_OF_DOC_TYPE, PALETTE_NAMES
from modules.flow_gate.services import ai_group_service as groups
from modules.flow_gate.settings import ai_execution_policy_service as policy


def _group(ai_group_id: str) -> dict:
    group = groups._group_row(ai_group_id)
    if group.get("group_type", "general") != "role":
        raise groups.AIGroupError("ai_role_group_required", 409)
    return group


def _types(locale: str = "ko") -> list[dict]:
    # These are the Designer palette's document cards, in palette order.
    # The human confirmation card has no document type and no AI role profile.
    language = locale if locale in ("ko", "ja", "en") else "ko"
    return [
        {"type_key": key, "label": PALETTE_NAMES[item][language]}
        for key, item in PALETTE_KEY_OF_DOC_TYPE.items()
    ]


def review_count_choices() -> tuple[int, ...]:
    """The values a new group review count may take: the server policy's choices with 0 ("안 함"), without -1
    ("until it passes") — the same new-selection rule as the card Inspector's 검수 횟수 (SettingsTab ``optionsOf``).
    The finite tail follows the effective repeat-count maximum; no fixed list."""
    return policy.repeat_count_choices(allow_zero=True, allow_unlimited=False)


def card_review_count_choices() -> tuple[int, ...]:
    """The card 검수 횟수 values a new card may store (-1, 0, 1..the effective max) — the same policy call as the
    creation copy's ``flowgate.ai_roles.legacy_adapter.card_review_count_choices``. ``effective_review_count`` is
    judged against these, so the API's effective value is what a new card copies today."""
    return policy.repeat_count_choices(allow_zero=True)


def options(ai_group_id: str, locale: str = "ko") -> dict:
    _group(ai_group_id)
    return {"types": _types(locale), "providers": [
        {"provider_id": pid, "provider_name": p["name"]}
        for pid, p in groups._available().items()],
        "review_count_choices": list(review_count_choices()),
        "review_count_default": 0}


def profiles(ai_group_id: str) -> dict:
    _group(ai_group_id)
    rows = repo.list_profiles(ai_group_id)
    if not any(row["type_key"] == repo.COMMON for row in rows):
        raise groups.AIGroupError("ai_role_profiles_inconsistent", 500)
    available, registry = groups._available(), {}
    common = next(row for row in rows if row["type_key"] == repo.COMMON)
    card_choices = card_review_count_choices()
    items, counts = [], {}
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
        stored = row.get("review_count")
        typed = None if row["type_key"] == repo.COMMON else row
        effective, source = repo.effective_review_count(typed, common, card_choices)
        # Kept apart from the AI cells: the stored value as it is (null = inherit / the default 0; one outside today's
        # choices is kept) and what a new card of the type copies today, by the same policy judgment as the creation
        # copy (source: type / common / default; a stored value outside today's card choices copies 0 = default).
        counts[row["type_key"]] = {"review_count": None if stored is None else int(stored),
                                   "effective_review_count": effective, "source": source}
    return {"ai_group_id": ai_group_id, "items": items, "review_counts": counts}


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


def set_review_count(ai_group_id: str, type_key: str, data: dict) -> dict:
    """One profile row's default review count. ``{"review_count": null}`` clears it (type row: inherit the common row,
    common row: the card default 0). A new value must be one of ``review_count_choices``; the value already stored is
    accepted again unchanged even when today's choices no longer offer it (it is never cleared by a read)."""
    if set(data) != {"review_count"}:
        raise groups.AIGroupError("ai_role_profile_review_count_invalid")
    value = data["review_count"]
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise groups.AIGroupError("ai_role_profile_review_count_invalid")
    with repo.get_store().transaction():
        repo.lock_group(ai_group_id)
        _group(ai_group_id)
        row = repo.get(ai_group_id, type_key)
        if row is None:
            raise groups.AIGroupError("ai_role_profile_not_found", 404)
        stored = row.get("review_count")
        kept = stored is not None and value is not None and int(stored) == value
        if value is not None and not kept and value not in review_count_choices():
            raise groups.AIGroupError("ai_role_profile_review_count_invalid")
        repo.set_review_count(ai_group_id, type_key, value)
    return profiles(ai_group_id)


def remove(ai_group_id: str, type_key: str) -> dict:
    with repo.get_store().transaction():
        repo.lock_group(ai_group_id)
        _group(ai_group_id)
        if type_key == repo.COMMON:
            raise groups.AIGroupError("ai_role_profile_common_required", 409)
        repo.delete(ai_group_id, type_key)
    return profiles(ai_group_id)
