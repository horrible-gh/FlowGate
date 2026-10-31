"""flowgate.v02.0113 T#2 rework (0030-M §2, 0002-D 개정 §3.1-6 / §3.2 / §3.2-A / §3.3-6 / §3.4, 0031-CH turn 6 #4): the
account's first AI setup, saved and applied to the Flow it was asked from, in one transaction.

    POST /api/v1/flows/{flow_id}/ai-roles/first-setup   {"request_id": <text>, "roles": {instruction, execution, review}}

- One Flow guard transaction: ① the account's three role values (P-1 value grammar and the account selection rules of
  ``account.parse_and_validate_save``) ② the setup completed marker (the account row, ``setup_completed_at`` set by its
  first INSERT only) ③ every AI-settings card of the Flow's current definition (the draft, else the latest revision;
  kinds with roles: run / test / WP — the cards an AI tab is attached to) gets each of its roles' value and marker
  (slot source ``first_setup``, set by the account), every applicable role, also one set before. A WP card has no
  instruction role. The card's init row becomes ``initialized``, so the legacy fields and the pending fill of L-1 §2.4
  are no longer read for these roles; the card's settings version moves once when anything changed.
- Nothing else is written: no document, report, review history, review count / basis, instruction review, time limit,
  restart, instruction use, assignee, stage assignment, ``graph_node_settings`` value. Another Flow's cards are not
  targets; a node only on the screen (no server key yet) is not one either — it takes the same values when it is saved
  (``card_init.initialize_new_card`` copies the account's values).
- Refusal: a target card *actually running* (T#1 ``legacy.node_running``) refuses the whole request, account included
  (409 ``ai_role_setup_cards_running`` with the cards); an unfinished Time Machine operation refuses as for a card
  setting. Any failure rolls the transaction back: nothing is partly applied.
- Concurrency: an account's requests are serialized in the server (an in-process lock per account, then the Flow guard).
  A completed setup is neither refused nor skipped: the request's values overwrite the account's and are applied to its
  own Flow; a Flow another tab applied to keeps its values. The same request id sent again answers its first result
  (``ai_role_setup_requests``, recorded in the same transaction) and writes nothing; a refused or failed request
  records nothing, so sending it again tries again.
- ``PUT /me/ai-roles`` (personal settings) is unchanged: it writes the account only, so only new cards take it (7-4).
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

from fastapi import HTTPException

from flowgate.ai_roles import account, role_store
from flowgate.ai_roles.resolve import applicable_roles
from flowgate.card_settings import fields, repository
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.graph_run import editing
from modules.flow_gate.graph_workflow.store import definition_store

CARDS_RUNNING = "ai_role_setup_cards_running"
UNAVAILABLE = "ai_role_setup_unavailable"
BUSY = "ai_role_setup_busy"
REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_BODY_KEYS = frozenset(("request_id", "roles"))
ACCOUNT_LOCK_WAIT_SEC = 30

_account_locks: dict[str, threading.Lock] = {}
_account_locks_guard = threading.Lock()


def _account_lock(user_id: str) -> threading.Lock:
    with _account_locks_guard:
        lock = _account_locks.get(user_id)
        if lock is None:
            lock = _account_locks[user_id] = threading.Lock()
        return lock


def _invalid(errors: list[dict]) -> legacy.GraphApiError:
    return legacy.GraphApiError(422, "validation_failed", {"errors": errors}, message="The setup request is not valid.")


def parse_request(body: Any) -> tuple[str, Any]:
    """``(request_id, roles)``; the body's own shape (the role values are checked by ``_values``)."""
    if not isinstance(body, dict):
        raise _invalid([{"field": "body", "reason": "not_an_object"}])
    errors = [{"field": key, "reason": "unknown_field"} for key in sorted(set(body) - _BODY_KEYS)]
    request_id = body.get("request_id")
    if request_id is None:
        errors.append({"field": "request_id", "reason": "required"})
    elif not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        errors.append({"field": "request_id", "reason": "malformed"})
    if errors:
        raise _invalid(errors)
    return request_id, body.get("roles")


def _require_available() -> None:
    try:
        on = role_store.available() and role_store.setup_requests_available()
    except Exception as exc:  # noqa: BLE001 - an unreadable store is the account API's 503
        raise legacy.GraphApiError(503, "ai_roles_unreadable", {}) from exc
    if not on:
        raise legacy.GraphApiError(409, UNAVAILABLE, {}, message="The first AI setup is not available on this server yet.")


def _values(user_id: str, roles: Any) -> dict:
    """The three role values by the account save's rules (all errors at once, nothing written)."""
    try:
        return account.parse_and_validate_save(user_id, {"roles": roles})
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if exc.status_code == 422:
            raise _invalid(list(detail.get("errors") or [])) from exc
        raise legacy.GraphApiError(exc.status_code, detail.get("code") or "ai_roles_unreadable", {}) from exc


def target_cards(flow_id: str) -> list[dict]:
    """The AI-settings cards of the Flow's current definition (the draft, else its latest revision), in order."""
    definition = definition_store.draft(flow_id)
    if definition is None:
        latest = definition_store.latest_revision_no(flow_id)
        definition = definition_store.revision(flow_id, latest) if latest is not None else None
    cards = []
    for node in (definition or {}).get("nodes") or []:
        kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
        if node.get("node_key") and applicable_roles(kind):
            cards.append({**node, "card_kind": kind})
    return cards


def _apply(flow_id: str, user_id: str, request_id: str, values: dict) -> dict:
    """Inside the Flow guard: the refusal checks, then the account and every target card, then the request record."""
    editing.require_settings_editable(flow_id)
    cards = target_cards(flow_id)
    running = [card for card in cards if legacy.node_running(flow_id, card["node_key"])]
    if running:
        raise legacy.GraphApiError(409, CARDS_RUNNING, {
            "flow_id": flow_id,
            "cards": [{"node_key": card["node_key"], "label": card.get("label")} for card in running]},
            message="A card of this flow is running; the AI setup cannot be applied now.")
    now = role_store._now()
    role_store.save_account_roles(user_id, values, now)
    applied = []
    for card in cards:
        key, roles = card["node_key"], applicable_roles(card["card_kind"])
        changed = False
        for role in roles:
            slot = role_store.RoleSlot(role, values[role], role_store.FIRST_SETUP, user_id, now)
            changed = role_store.write_slot(flow_id, key, slot) or changed
        init = role_store.card_init(flow_id, key)
        if init is None:
            role_store.create_card_init(flow_id, key, role_store.INITIALIZED, None, now)
            changed = True
        elif init.state == role_store.PENDING_INIT:
            changed = role_store.mark_initialized(flow_id, key, now) or changed
        if changed:
            repository.bump_version(flow_id, key)
        applied.append({"node_key": key, "card_kind": card["card_kind"], "roles": list(roles)})
    result = {"flow_id": flow_id, "request_id": request_id,
              "roles": {role: account.value_json(values[role]) for role in role_store.ROLES},
              "cards": applied}
    role_store.record_setup_request(user_id, request_id, flow_id, json.dumps(result, ensure_ascii=False), now)
    return result


def _recorded(user_id: str, request_id: str) -> Optional[dict]:
    done = role_store.setup_request(user_id, request_id)
    return json.loads(done["result_json"]) if done else None


def _answer(user_id: str, result: dict, replayed: bool) -> dict:
    try:
        view = account.account_view(user_id)
    except HTTPException:
        view = None                                 # applied and committed; the screen reads the account again
    return {**result, "replayed": replayed, "account": view}


def apply(group: dict, user_id: str, body: Any) -> dict:
    """The first setup request of ``user_id`` for the Flow ``group`` (see the module header)."""
    flow_id = group["group_id"]
    request_id, roles = parse_request(body)
    _require_available()
    lock = _account_lock(user_id)
    if not lock.acquire(timeout=ACCOUNT_LOCK_WAIT_SEC):
        raise legacy.GraphApiError(409, BUSY, {}, message="Another AI setup of this account is being saved.")
    try:
        first = _recorded(user_id, request_id)
        if first is not None:
            return _answer(user_id, first, True)
        values = _values(user_id, roles)
        legacy.require_writable(flow_id)
        try:
            result = legacy.with_flow_guard(flow_id, lambda: _apply(flow_id, user_id, request_id, values))
        except Exception:
            first = _recorded(user_id, request_id)  # the same request applied by another server process meanwhile
            if first is not None:
                return _answer(user_id, first, True)
            raise
        return _answer(user_id, result, False)
    finally:
        lock.release()
