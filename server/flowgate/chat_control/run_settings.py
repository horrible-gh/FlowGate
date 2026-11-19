"""Run settings of the cards a chat placed: review count, restarts, time limit (flowgate.v02gui.0032, 0002-CH seq 15-17),
and the two 검증 options (flowgate.v02test.0053 0003-T: ``verify_max_runs`` / ``verify_run_minutes``, a run card only).

The chat's card role API wrote only the three AI roles, so a chat could neither read nor change a card's review count:
it had to ask the user to set it in the Inspector AI tab. The same three run values the AI tab shows next to the roles
are opened here, and nothing else (hold, merge, assignee, instruction use, review basis, ... stay a person's):

- ``FIELDS``: ``review_count``, ``restart``, ``time_limit``, ``verify_max_runs``, ``verify_run_minutes``. A card has those
  of its kind (``fields.fields_of``): a work card (T, run kind) all five, a test card (TS) only ``review_count``. Another
  one is 422 ``setting_not_applicable``. The two 검증 options take any whole number inside their range (``fields``), their
  ``choices`` are the screen combo's presets; ``null`` takes the system default back.
- The values are the person's screen's (``fields.check_value`` with ``legacy.repeat_choices()`` / the time limit
  minutes): a value the screen cannot pick is 422 with the field's ``choices``. ``null`` takes the default back (no
  stored row), as the person's PATCH does.
- ``view``: the card settings read narrowed to these fields (value, stored, default, choices), for the chat's GET.
- ``write``: inside the caller's Flow guard; every value checked before anything is written. The caller moves the
  settings version once and writes one ``chat_card_setting_audit`` row per requested field (``audit``).
- Used by the card role PATCH (``card_roles.change``: ``settings`` next to ``roles``) and a placement (``service.place``:
  ``settings`` per card).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from flowgate.card_settings import fields
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import repository
from modules.flow_gate.graph_workflow.services.errors import GraphApiError

FIELDS = ("review_count", "restart", "time_limit", "verify_max_runs", "verify_run_minutes")
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
    errors = [{"field": f"{field}.{name}", "reason": "not_a_chat_setting", "settings": list(FIELDS)}
              for name in sorted(set(raw) - set(FIELDS))]
    for name in FIELDS:
        value = raw.get(name)
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


def check(kind: Optional[str], requested: dict, field: str, choices: Optional[Iterable[int]] = None) -> dict:
    """``{name: value to store or None}`` of ``requested`` for a card of ``kind``, else 422 naming every fault
    (``setting_not_applicable``, or the value check's reason with the field's ``choices``). Nothing is written."""
    own = applicable(kind)
    repeat = tuple(legacy.repeat_choices() if choices is None else choices)
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
    choices = tuple(legacy.repeat_choices())
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
