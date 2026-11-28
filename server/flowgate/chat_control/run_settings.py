"""Chat settings for card placement and roles PATCH.

Run cards expose time_limit_sec (1800..14400) and verify_run_sec (60..1800).
Legacy minute keys are accepted on input and multiplied by 60; responses use only
seconds. Times clamp to boundaries, integers are required, null restores defaults.
TS exposes its four execution-only budget settings alongside review_count.
Every requested setting is checked before writes and audited in the caller's guard.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from flowgate.card_settings import fields
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import repository
from modules.flow_gate.graph_workflow.services.errors import GraphApiError

FIELDS = ("review_count", "restart", "time_limit_sec", "verify_max_runs", "verify_run_sec",
          "auto_case_timeout_sec", "ai_judge_mode", "ai_judge_run_sec", "ai_judge_runs")
UNAVAILABLE = "chat_card_settings_unavailable"
VIEW_KEYS = ("value", "stored", "default", "choices")


def _invalid(errors: list[dict]) -> GraphApiError:
    return GraphApiError(422, "invalid_request", {"errors": errors}, message="The request does not fit this API.")


def parse(raw: Any, field: str) -> dict:
    """The ``settings`` of a request (absent / null: none), else 422 naming every fault: only ``FIELDS``, each an
    integer or null. Whether the card has the field and the value is one of its choices is judged with the card."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _invalid([{"field": field, "reason": "not_an_object"}])
    raw = {**{fields.setting_input(k, v)[0]: fields.setting_input(k, v)[1]
              for k, v in raw.items() if k in fields.LEGACY_SECONDS},
           **{k: v for k, v in raw.items() if k not in fields.LEGACY_SECONDS}}
    errors = [{"field": f"{field}.{name}", "reason": "not_a_chat_setting", "settings": list(FIELDS)}
              for name in sorted(set(raw) - set(FIELDS))]
    for name in FIELDS:
        value = raw.get(name)
        if name == "ai_judge_mode":
            if name in raw and value is not None and value not in ("case_count", "fixed"):
                errors.append({"field": f"{field}.{name}", "reason": "not_a_choice"})
            continue
        if name in raw and value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            errors.append({"field": f"{field}.{name}", "reason": "not_an_integer"})
    if errors:
        raise _invalid(errors)
    return {name: raw[name] for name in FIELDS if name in raw}


def applicable(kind: Optional[str]) -> list[str]:
    """The chat settings a card of ``kind`` has (in ``FIELDS`` order)."""
    own = {f.name for f in fields.fields_of(kind)}
    return [name for name in FIELDS if name in own]


def view(read: dict) -> dict:
    """``{field: {value, stored, default, choices}}`` of the card settings read (``settings.read``), these fields only."""
    shown = read.get("fields") if isinstance(read.get("fields"), dict) else {}
    return {name: {key: shown[name].get(key) for key in VIEW_KEYS} for name in FIELDS if name in shown}


def values(flow_id: str, node_key: str, kind: Optional[str]) -> dict:
    """``{field: shown value}`` of a card now (stored, else the default)."""
    shown = fields.view(kind, repository.values(flow_id, node_key), legacy.repeat_choices())
    return {name: shown[name]["value"] for name in applicable(kind)}


def check(kind: Optional[str], requested: dict, field: str, choices: Optional[Iterable[int]] = None, user_id=None) -> dict:
    """``{name: value to store or None}`` of ``requested`` for a card of ``kind``, else 422 naming every fault
    (``setting_not_applicable``, or the value check's reason with the field's ``choices``). Nothing is written."""
    own = applicable(kind)
    repeat = tuple(legacy.repeat_choices(user_id) if choices is None else choices)
    errors, checked = [], {}
    for name, value in requested.items():
        if name not in own:
            errors.append({"field": f"{field}.{name}", "reason": "setting_not_applicable", "card_kind": kind,
                           "applicable_settings": own})
            continue
        if value is None:
            checked[name] = None
            continue
        spec = fields.field_of(kind, name)
        try:
            checked[name] = fields.check_value(spec, value, repeat_choices=repeat, team_exists=legacy.team_exists,
                                               ai_exists=legacy.ai_exists)
        except fields.InvalidValue as exc:
            errors.append({"field": f"{field}.{name}", "reason": exc.reason,
                           "choices": fields.choices_of(spec, repeat)})
    if errors:
        raise _invalid(errors)
    return checked


def _state(kind: Optional[str], stored: dict, name: str, choices: tuple) -> dict:
    shown = fields.view(kind, stored, choices).get(name) or {}
    return {"value": shown.get("value"), "stored": bool(shown.get("stored"))}


def write(flow_id: str, node_key: str, kind: Optional[str], requested: dict, user_id: Optional[str],
          field: str = "settings") -> tuple[list[str], dict, dict]:
    """Inside the caller's Flow guard: the checked values written (null removes the stored one). Returns ``(changed
    fields, before, after)`` with each requested field's ``{value, stored}``; the caller moves the version."""
    choices = tuple(legacy.repeat_choices(user_id))
    checked = check(kind, requested, field, choices)
    stored = repository.values(flow_id, node_key)
    before = {name: _state(kind, stored, name, choices) for name in checked}
    changed = []
    for name, value in checked.items():
        if value is None:
            wrote = repository.remove(flow_id, node_key, name)
        else:
            wrote = repository.put(flow_id, node_key, name, value, user_id)
        if wrote:
            changed.append(name)
    stored = repository.values(flow_id, node_key)
    after = {name: _state(kind, stored, name, choices) for name in checked}
    for name, value in checked.items():
        after[name]["clamped"] = name in fields.SECOND_RANGES and value != requested.get(name)
    return changed, before, after


def audit(flow_id: str, node_key: str, requested: Iterable[str], before: dict, after: dict, changed: list[str],
          version: Optional[int], owner: dict, user_id: str) -> list[str]:
    """One ``chat_card_setting_audit`` row per requested field (``owner``: token_id, ai_run_id, chat_doc_id)."""
    from . import card_role_store

    at = card_role_store._now()
    return [card_role_store.write_setting_audit(
        flow_id, node_key, name, before.get(name), after.get(name), name in changed, version, owner.get("token_id"),
        owner.get("ai_run_id"), owner["chat_doc_id"], user_id, at) for name in requested]


def require_available() -> None:
    """409 ``chat_card_settings_unavailable`` until migration 191 (the audit every chat settings change writes)."""
    from . import card_role_store

    try:
        ready = card_role_store.setting_audit_available()
    except Exception:  # noqa: BLE001
        ready = False
    if not ready:
        raise GraphApiError(409, UNAVAILABLE, {}, message="Chat card settings are not available on this server yet.")
