"""AI roles a chat chooses for the cards it places (decisions 6-8).

The chat picks each placed card's instruction / execution / review AI itself; the AI role group defaults only fill
what it leaves out (they stand in for "nothing chosen" when a person places a card), and an AI group's preset is used
only when the user names the group.

- ``parse_card_roles``: the optional ``roles`` of a placed card (``{role: {kind, provider_id?}}``), the grammar of the
  card role PATCH (``card_roles.parse_change``): only the three roles, never null, no unknown key.
- ``precheck``: before anything is saved, every assigned AI must be one the placing user may select (the account
  level ``availability.check_availability`` that ``apply_role_write`` applies): another user's, a disabled or a
  deleted AI is 422 ``ai_not_selectable`` and nothing is placed.
- ``write``: inside the placement's own Flow guard, after the new cards were created and their roles copied from the
  defaults (``card_init``), each chosen role goes through the card role write (``apply_role_write``, source
  ``card_explicit``) and, when the placing chat is known, one ``chat_card_role_audit`` row per role. A refusal there
  rolls the whole placement back.
- ``summary``: where each placed card's roles came from now: ``chat`` (this placement or a later chat change),
  ``default`` (copied at creation from the AI role group / account defaults), ``person`` (a person's card setting),
  ``unset``.
- ``group_values``: the role values an AI group's profile gives a card type (decision 6): the typed profile cell, else
  the common one, as the creation copy reads the role group.
- ``options``: the AIs and the AI groups with role profiles a chat may pick from before placing
  (``GET /api/v2/chat/flows/{flow_id}/ai-options``).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from flowgate.ai_roles import availability, role_store
from flowgate.ai_roles.account import valid_value, value_detail, value_json
from modules.flow_gate.graph_workflow.services.errors import GraphApiError

ROLES = role_store.ROLES
SOURCE_CHAT, SOURCE_DEFAULT, SOURCE_PERSON, SOURCE_UNSET = "chat", "default", "person", "unset"
DEFAULT_SOURCES = frozenset({"creation_copy", "pending_fill", "wp_plan"})
GROUP_NOT_FOUND = "ai_group_not_found"
GROUP_NO_PROFILE = "ai_group_no_profile"


def _invalid(errors: list[dict]) -> GraphApiError:
    return GraphApiError(422, "invalid_request", {"errors": errors}, message="The request does not fit this API.")


def role_value_errors(value: Any, field: str) -> list[dict]:
    """The faults of one role value in the card role PATCH grammar (empty: valid)."""
    if value is None:
        return [{"field": field, "reason": "role_unset_not_allowed"}]
    detail = value_detail(value)
    if detail:
        return [{"field": field, "reason": "role_value_malformed", "detail": detail}]
    if set(value) - {"kind", "provider_id"}:
        return [{"field": field, "reason": "role_value_malformed", "detail": "unknown_key"}]
    return []


def parse_card_roles(raw: Any, field: str) -> dict:
    """A placed card's ``roles`` (absent / null: none chosen), else 422 naming every fault."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _invalid([{"field": field, "reason": "not_an_object"}])
    errors = [{"field": f"{field}.{name}", "reason": "not_a_role", "roles": list(ROLES)}
              for name in sorted(set(raw) - set(ROLES))]
    for name in ROLES:
        if name in raw:
            errors += role_value_errors(raw[name], f"{field}.{name}")
    if errors:
        raise _invalid(errors)
    return {name: {"kind": raw[name]["kind"], **({"provider_id": raw[name]["provider_id"]}
                                                 if raw[name]["kind"] == "assigned" else {})}
            for name in ROLES if name in raw}


def precheck(cards: Iterable[dict], user_id: Optional[str]) -> None:
    """Every assigned AI of the chosen roles selectable by ``user_id``, else 422 ``ai_not_selectable`` (before any
    save). The write inside the guard judges the same again."""
    errors = []
    for i, card in enumerate(cards):
        for role, value in (card.get("roles") or {}).items():
            if value.get("kind") != "assigned":
                continue
            cause = availability.check_availability(value["provider_id"], availability.ACCOUNT, user_id=user_id)
            if cause:
                errors.append({"field": f"cards[{i}].roles.{role}", "reason": "ai_not_selectable", "cause": cause,
                               "provider_id": value["provider_id"]})
    if errors:
        raise _invalid(errors)


def write(flow_id: str, project_id: Optional[str], user_id: str, chosen: list[tuple[str, dict]],
          owner: Optional[dict]) -> dict[str, list[str]]:
    """Inside the placement's Flow guard: the chosen roles of each new card (``(node_key, roles)``); returns the roles
    each card changed. Audited per requested role when the placing chat is known (``owner``)."""
    from flowgate.ai_roles import card_roles as role_writes
    from flowgate.ai_roles.resolve import applicable_roles
    from flowgate.card_settings import fields, repository, settings

    from . import card_role_store, card_roles

    out: dict[str, list[str]] = {}
    for node_key, roles in chosen:
        if not roles:
            continue
        node = settings._card(flow_id, node_key)
        kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
        applicable = applicable_roles(kind)
        foreign = [r for r in roles if r not in applicable]
        if foreign:
            raise _invalid([{"field": f"roles.{r}", "reason": "role_not_applicable", "card_kind": kind,
                             "applicable_roles": list(applicable)} for r in foreign])
        before = card_roles._role_states(flow_id, node, kind, project_id)
        changed = []
        for role, value in roles.items():
            try:
                wrote = role_writes.apply_role_write(flow_id, node_key, kind, f"{role}_role", value, user_id)
                if not wrote:
                    # the chat chose the value the defaults already copied: it is the card's own choice now
                    old = role_store.slots(flow_id, node_key).get(role)
                    if old is not None and old.source != "card_explicit":
                        role_store.write_slot(flow_id, node_key, role_store.RoleSlot(
                            role, old.value, "card_explicit", user_id, role_store._now()))
                        wrote = True
                if wrote:
                    changed.append(role)
            except GraphApiError as exc:
                if exc.code != "invalid_request":
                    raise
                errors = [{**e, "field": f"roles.{role}", "node_key": node_key}
                          for e in (exc.params or {}).get("errors") or []]
                raise _invalid(errors or [{"field": f"roles.{role}", "reason": "invalid"}]) from exc
        version = repository.settings_version(flow_id, node_key)
        if changed:
            repository.bump_version(flow_id, node_key)
            version = repository.settings_version(flow_id, node_key)
        if owner and owner.get("chat_doc_id") and card_role_store.available():
            after = card_roles._role_states(flow_id, node, kind, project_id)
            at = card_role_store._now()
            for role in roles:
                card_role_store.write_audit(flow_id, node_key, role, before.get(role), after.get(role),
                                            role in changed, version, owner.get("token_id"), owner.get("ai_run_id"),
                                            owner["chat_doc_id"], user_id, at)
        out[node_key] = changed
    return out


def _source(slot) -> str:
    if slot is None:
        return SOURCE_UNSET
    if slot.source in DEFAULT_SOURCES:
        return SOURCE_DEFAULT
    return SOURCE_CHAT if slot.source == "card_explicit" else SOURCE_PERSON


def summary(flow_id: str, node_key: str, chosen: Optional[dict] = None) -> dict:
    """Each role of a placed card now: its value, the AI's name and where it came from (``chat`` for the roles this
    placement chose). Empty when the role store is not there."""
    try:
        if not role_store.available():
            return {}
        slots = role_store.slots(flow_id, node_key)
    except Exception:  # noqa: BLE001 - a summary never fails a committed placement
        return {}
    out = {}
    for role in ROLES:
        slot = slots.get(role)
        value = slot.value if slot is not None and valid_value(slot.value) else None
        out[role] = {**(value_json(value) if value else {"kind": None, "provider_id": None}),
                     "name": (value.provider_name if value and value.kind == "assigned" else None),
                     "source": SOURCE_CHAT if role in (chosen or {}) else _source(slot if value else None)}
    return out


def defaults_used(roles_by_card: dict[str, dict]) -> list[dict]:
    """The (card, role) pairs still on a default or unset after a placement: the chat should choose them itself."""
    return [{"node_key": key, "role": role, "source": state["source"]}
            for key, roles in roles_by_card.items() for role, state in roles.items()
            if state.get("source") in (SOURCE_DEFAULT, SOURCE_UNSET)]


# ── AI groups (decision 6: only when the user names the group) ───────────────

def _profile_groups() -> list[dict]:
    from modules.flow_gate.db import ai_group_role_profiles, ai_groups
    out = []
    for group in ai_groups.list_all() or []:
        profiles = ai_group_role_profiles.list_profiles(group["ai_group_id"]) or []
        if profiles:
            out.append({"ai_group_id": group["ai_group_id"], "name": group.get("name"),
                        "role_group": group.get("role_group_key") == "role",
                        "type_keys": [p["type_key"] for p in profiles]})
    return out


def group_values(ai_group_id: Any, doc_type: Optional[str]) -> dict:
    """``{role: value}`` the group's profile gives a card of ``doc_type`` (typed cell, else common; a role neither
    sets is left out). 404 ``ai_group_not_found``; 422 ``ai_group_no_profile`` when it gives the card no role."""
    from modules.flow_gate.db import ai_group_role_profiles, ai_groups
    group = ai_groups.get(ai_group_id) if isinstance(ai_group_id, str) and ai_group_id else None
    if group is None:
        raise GraphApiError(404, GROUP_NOT_FOUND, {"ai_group_id": ai_group_id},
                            message="No AI group has this id (see ai-options role_groups).")
    rows = {row["type_key"]: row for row in ai_group_role_profiles.list_profiles(group["ai_group_id"]) or []}
    typed = rows.get(doc_type or "", {})
    common = rows.get(ai_group_role_profiles.COMMON, {})
    values = {}
    for role in ROLES:
        cell = typed if typed.get(role + "_kind") in ("assigned", "not_used") else common
        kind = cell.get(role + "_kind")
        if kind == "not_used":
            values[role] = {"kind": "not_used"}
        elif kind == "assigned" and cell.get(role + "_provider_id"):
            values[role] = {"kind": "assigned", "provider_id": cell[role + "_provider_id"]}
    if not values:
        raise GraphApiError(422, GROUP_NO_PROFILE, {"ai_group_id": group["ai_group_id"], "doc_type": doc_type},
                            message="The AI group has no role profile for this card type.")
    return values


OPTIONS_NEXT = ("Choose each card's instruction / execution / review AI yourself from `options` and send them as "
                "`roles` of each card in the placement. Instruction: not_used when you write the card's instruction "
                "yourself, else an AI. Review: preferably another AI than the execution. Use a role group "
                "(`role_groups`, set_card_roles from_group) only when the user names it.")


def options(user_id: Optional[str], flow_id: str) -> dict:
    """``GET .../ai-options``: the AIs the chat may pick (public fields only) and the AI groups with role profiles."""
    try:
        picks, state = availability.selectable_ais(user_id), "ok"
    except Exception:  # noqa: BLE001 - an unreadable registry is a state of the answer
        picks, state = [], "unreadable"
    try:
        groups = _profile_groups()
    except Exception:  # noqa: BLE001
        groups = []
    return {"flow_id": flow_id, "options_state": state, "options": picks, "role_groups": groups,
            "next": OPTIONS_NEXT}
