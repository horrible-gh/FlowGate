"""Card AI roles of the cards a chat placed: view and change.

The v1 card settings routes are a person's (user JWT only); a chat reaches only the three AI roles of the work cards it
placed itself, through its own routes (``routes``: ``GET`` / ``PATCH /api/v2/chat/flows/{flow_id}/nodes/{node_key}/roles``)
and API tools (``tools``: ``get_card_roles`` / ``set_card_roles``).

- Allowed cards: a card whose placement record (``card_role_store.chat_placed_cards``, written by ``service.place``)
  names this chat's conversation document (the CH ``doc_ref`` of the chat token) — the same chat in any of its runs.
  Every other card (placed by a person, by another chat, before migration 190, the start / confirm card a placement
  adds) is 403 ``chat_card_not_owned``; another group's Flow is refused earlier by the read boundary
  (``flow_access_denied``).
- Read: a live chat token of the Flow's group with the read permission (``authz.read_caller``; card workers refused),
  any mode. The answer is the card settings read (``card_settings.settings.read``) narrowed to the roles: each
  applicable role's resolved state and selection (``ai_roles.card_roles.card_role_view``), the settings version and
  context a change sends back, the lock state, and the public selectable AI list (id, name, exec type, kind, owner
  scope — ``availability.selectable_ais``; never a key or endpoint).
- Change: ``authz.write_caller`` (edit mode or a claimed edit-once, no pending claim, ``perm_document_update``), then
  inside the Flow guard: the chat re-judged (``chat_authz.chat_recheck``: the run alive, the token still its current
  one, edit mode, no pending claim — then the write permission and the ownership again), and the v1 PATCH's own rules
  for a role field (``settings.write``): Graph ownership, the expected context, the expected settings version (required
  here; stale: 409 ``gate_context_changed``), the Time Machine lock, the role applicable to the card kind (422), the
  running lock (409 ``node_settings_locked``) and ``card_roles.apply_role_write`` per role (the value grammar, the AI
  selectable by the acting user: another user's AI, a disabled or deleted AI is 422 ``ai_not_selectable``). One
  request may change several roles: all or nothing, the version moves once, one ``graph_canvas_changed``.
- Audit: one ``chat_card_role_audit`` row per requested role, in the same transaction (token, AI run, CH, user, card,
  role, before / after, changed, version).
- Only the three roles (and the three run settings below): nothing else of the card (hold, merge, assignee, other
  settings) and never the account defaults (``/api/v1/me/ai-roles``) are written here.
- the earlier change (decision 6): ``from_group`` (an AI group id with role profiles, ``ai-options``
  ``role_groups``) fills the roles from that group's profile for the card's document type (typed cell, else common;
  ``placement_roles.group_values``), only when the user named the group. Roles the card does not have are skipped;
  ``roles`` sent with it win over the group's. The same checks, write, audit and version move as above.
- the earlier change (CH seq 15-17): ``settings`` changes the card's run settings the AI tab shows next to the
  roles — review count, restarts, time limit (``run_settings``; the card's kind decides which: a test card has only the
  review count) — and nothing else. Same request as the roles: the same checks and locks, all or nothing, the version
  moves once, one ``chat_card_setting_audit`` row per requested setting. The read answers their value, default and
  choices (``settings``). Without migration 191 a settings change is 409 ``chat_card_settings_unavailable``.
"""
from __future__ import annotations

from typing import Any, Optional

from flowgate.ai_roles import card_roles as role_writes
from flowgate.ai_roles import role_store
from flowgate.ai_roles.account import valid_value, value_detail, value_json
from flowgate.ai_roles.resolve import applicable_roles, read_card_role_inputs, resolve_role
from flowgate.card_document_edit import chat_authz, chat_errors, chat_gate
from flowgate.card_settings import context as settings_context
from flowgate.card_settings import fields, repository, settings
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.gate_decisions import settings_lock as gate_lock
from flowgate.graph_run import editing
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.rbac.permission_service import has_permission

from . import authz, card_role_store, placement_roles, run_settings

ROLES = role_store.ROLES
NOT_OWNED = "chat_card_not_owned"
UNAVAILABLE = "chat_card_roles_unavailable"
BODY_KEYS = frozenset({"roles", "settings", "expected_settings_version", "expected_context", "from_group"})
STATE_KEYS = ("state", "outcome", "source", "provider_id", "display_name", "cause", "selected")
NEXT_AFTER_READ = ("To change a role or a run setting send PATCH (set_card_roles) with `roles` and / or `settings` and "
                   "this settings_version (and context). Pick provider_id only from `options` and a setting value only "
                   "from its `choices`.")
NEXT_AFTER_CHANGE = ("Read the card's roles again (GET / get_card_roles): only that read showing the new selection "
                     "confirms the change. Tell the user what changed.")
SETTING_NOTE = ("review_count: AI reviews of the result (0 none, -1 until it passes); restart: restarts after a failed "
                "run (-1 until done); time_limit: minutes per run; verify_max_runs: verifications one card may use (default "
                "3); verify_run_minutes: longest minutes of one verification, 1..30 (default 2). "
                "null in a change takes the default back.")


def _invalid(errors: list[dict]) -> GraphApiError:
    return GraphApiError(422, "invalid_request", {"errors": errors}, message="The request does not fit this API.")


def require_owned(flow_id: str, node_key: Any, chat_doc_id: Optional[str]) -> dict:
    """The card's placement record when this chat (its CH) placed it, else 403 ``chat_card_not_owned``."""
    try:
        ready = card_role_store.available()
    except Exception:
        ready = False
    if not ready:
        raise GraphApiError(409, UNAVAILABLE, {"flow_id": flow_id},
                            message="Chat card roles are not available on this server yet.")
    row = card_role_store.placed(flow_id, node_key) if isinstance(node_key, str) and node_key else None
    if row is None or not chat_doc_id or str(row.get("chat_doc_id") or "") != str(chat_doc_id):
        raise GraphApiError(403, NOT_OWNED, {
            "flow_id": flow_id, "node_key": node_key,
            "placed_by_this_chat": card_role_store.placed_keys(flow_id, chat_doc_id)},
            message="Only the work cards this chat placed (place_flow_cards) can be read or changed here.")
    return row


def _answer(caller: authz.Caller, node_key: str, record: dict) -> dict:
    """The card settings read narrowed to the AI roles (the v1 GET's own values, nothing else of the card)."""
    flow_id = caller.group["group_id"]
    read = settings.read(caller.group, node_key, viewer=caller.scope.user_id)
    kind = read.get("card_kind")
    block = read.get("ai_roles") if isinstance(read.get("ai_roles"), dict) else None
    roles = {}
    for role, state in ((block or {}).get("roles") or {}).items():
        roles[role] = {key: state.get(key) for key in STATE_KEYS}
    return {
        "flow_id": flow_id, "node_key": node_key, "card_kind": kind,
        "applicable_roles": list(applicable_roles(kind)),
        "roles_available": block is not None,
        "card_init_state": (block or {}).get("card_init_state"),
        "roles": roles,
        "settings_version": read.get("settings_version"),
        "context": read.get("context"),
        "locked": read.get("locked"), "node_running": read.get("node_running"),
        "settings_capability": read.get("settings_capability"),
        "options_state": (block or {}).get("options_state", "unavailable"),
        "options": list((block or {}).get("options") or []),
        "placed": {"chat_doc_id": record.get("chat_doc_id"), "placed_at": record.get("placed_at")},
        "applicable_settings": run_settings.applicable(kind),
        "settings": run_settings.view(read),
        "settings_note": SETTING_NOTE,
    }


def view(caller: authz.Caller, node_key: Any) -> dict:
    """``GET .../nodes/{node_key}/roles``."""
    flow_id = caller.group["group_id"]
    record = require_owned(flow_id, node_key, caller.chat_doc_id)
    answer = _answer(caller, node_key, record)
    answer["chat_can_change"] = authz.can_write(caller)
    answer["next"] = NEXT_AFTER_READ
    return answer


# ── change ───────────────────────────────────────────────────────────────────

def parse_change(body: Any) -> tuple[dict, int, Optional[dict]]:
    """``(roles, expected_settings_version, expected_context)`` of a PATCH body, else 422 naming every fault."""
    roles, _settings, version, expected_context = parse_request(body)
    return roles, version, expected_context


def parse_request(body: Any) -> tuple[dict, dict, int, Optional[dict]]:
    """``(roles, settings, expected_settings_version, expected_context)`` of a PATCH body, else 422 naming every fault.
    ``roles`` may be left out when ``from_group`` or ``settings`` is there."""
    if not isinstance(body, dict):
        raise _invalid([{"field": "body", "reason": "not_an_object"}])
    errors = [{"field": key, "reason": "unknown_field"} for key in sorted(set(body) - BODY_KEYS)]
    group = body.get("from_group")
    if "from_group" in body and (not isinstance(group, str) or not group.strip()):
        errors.append({"field": "from_group", "reason": "not_an_ai_group_id"})
    try:
        settings_req = run_settings.parse(body.get("settings"), "settings")
    except GraphApiError as exc:
        errors += (exc.params or {}).get("errors") or []
        settings_req = {}
    if "settings" in body and body.get("settings") is not None and not settings_req and not errors:
        errors.append({"field": "settings", "reason": "required_non_empty_object"})
    roles = body.get("roles")
    if (group or settings_req) and roles is None:
        roles = {}                                       # the group / the settings alone
    elif not isinstance(roles, dict) or (not roles and not group and not settings_req):
        errors.append({"field": "roles", "reason": "required_non_empty_object"})
        roles = {}
    for name in sorted(set(roles) - set(ROLES)):
        errors.append({"field": f"roles.{name}", "reason": "not_a_role", "roles": list(ROLES)})
    for name in ROLES:
        if name not in roles:
            continue
        value = roles[name]
        if value is None:
            errors.append({"field": f"roles.{name}", "reason": "role_unset_not_allowed"})
        else:
            detail = value_detail(value)
            if detail:
                errors.append({"field": f"roles.{name}", "reason": "role_value_malformed", "detail": detail})
            elif set(value) - {"kind", "provider_id"}:
                errors.append({"field": f"roles.{name}", "reason": "role_value_malformed", "detail": "unknown_key"})
    version = body.get("expected_settings_version")
    if "expected_settings_version" not in body:
        errors.append({"field": "expected_settings_version", "reason": "required"})
    elif isinstance(version, bool) or not isinstance(version, int) or version < 0:
        errors.append({"field": "expected_settings_version", "reason": "not_a_version"})
    expected_context = body.get("expected_context")
    if "expected_context" in body and expected_context is not None and not settings_context.parse(expected_context):
        errors.append({"field": "expected_context", "reason": "invalid_context"})
    if errors:
        raise _invalid(errors)
    return {name: roles[name] for name in ROLES if name in roles}, settings_req, version, expected_context


def _role_states(flow_id: str, node: dict, kind: Optional[str], project_id: Optional[str]) -> dict:
    """Each applicable role's selection and resolution now (the audit's before / after)."""
    inputs = read_card_role_inputs(flow_id, node)
    out = {}
    for role in applicable_roles(kind):
        result = resolve_role(flow_id, node, role, project_id=project_id, inputs=inputs)
        slot = inputs.slots.get(role) if not inputs.store_error else None
        out[role] = {"selected": value_json(slot.value) if slot and valid_value(slot.value) else None,
                     "outcome": result.outcome, "source": result.source, "provider_id": result.provider_id,
                     "cause": result.cause}
    return out


def _recheck(caller: authz.Caller, node_key: str, holder: chat_gate.TicketHolder) -> None:
    """Inside the Flow guard: the chat run still alive with this token, edit mode, no pending claim (the chat card
    document edit's ``chat_recheck``), the Flow write permission and the card still this chat's."""
    try:
        chat_authz.chat_recheck(caller.run, caller.token, holder)
    except GraphApiError as exc:
        if exc.code == chat_errors.EDIT_MODE_DENIED:
            raise GraphApiError(403, authz.EDIT_MODE_REQUIRED, {}, message=authz.EDIT_MODE_REQUIRED) from exc
        raise
    if not has_permission(caller.scope.user_id, caller.scope.project_id, authz.WRITE_PERMISSION):
        raise GraphApiError(403, "flow_access_denied", {}, message="flow_access_denied")
    require_owned(caller.group["group_id"], node_key, caller.chat_doc_id)


def _write_role(flow_id: str, node_key: str, kind: str, role: str, value: dict, user_id: str) -> bool:
    try:
        return role_writes.apply_role_write(flow_id, node_key, kind, f"{role}_role", value, user_id)
    except GraphApiError as exc:
        if exc.code != "invalid_request":
            raise
        errors = [{**e, "field": f"roles.{role}"} for e in (exc.params or {}).get("errors") or []]
        raise _invalid(errors or [{"field": f"roles.{role}", "reason": "invalid"}]) from exc


def change(caller: authz.Caller, node_key: Any, body: Any) -> dict:
    """``PATCH .../nodes/{node_key}/roles``: one or more of the three roles and / or the run settings, all or
    nothing."""
    flow_id = caller.group["group_id"]
    project_id = caller.group.get("project_id")
    user_id = caller.scope.user_id
    require_owned(flow_id, node_key, caller.chat_doc_id)
    explicit, settings_req, expected_version, expected_context = parse_request(body)
    group_id = body.get("from_group") if isinstance(body, dict) else None
    requested = dict(explicit)
    if settings_req:
        run_settings.require_available()
    legacy.require_writable(flow_id)
    if not repository.available():
        raise GraphApiError(409, "card_settings_unavailable", {"flow_id": flow_id},
                            message="Card settings are not available on this server yet.")
    holder = chat_gate.TicketHolder()

    def guarded() -> dict:
        _recheck(caller, node_key, holder)
        settings_context.require_owner(flow_id)
        if expected_context is not None:
            settings_context.require_current(flow_id, node_key, expected_context)
        current_version = repository.settings_version(flow_id, node_key)
        if current_version != expected_version:
            raise GraphApiError(409, "gate_context_changed", {
                "flow_id": flow_id, "node_key": node_key, "reason": "settings_version",
                "expected_settings_version": expected_version, "settings_version": current_version},
                message="The card's settings changed since they were read; read them again.")
        found = repository.card_node(flow_id, node_key)
        if found is not None and fields.card_kind(found.get("node_type"), found.get("doc_type")) == fields.CHECK:
            gate_lock.require_unlocked(flow_id, node_key)
        else:
            editing.require_settings_editable(flow_id)
        node = settings._card(flow_id, node_key)
        kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
        if (explicit or group_id) and not role_store.available():
            raise GraphApiError(409, "card_roles_unavailable", {"flow_id": flow_id, "node_key": node_key},
                                message="Card AI roles are not available on this server yet.")
        applicable = applicable_roles(kind)
        if group_id:
            preset = placement_roles.group_values(group_id, node.get("doc_type"))
            requested.update({r: v for r, v in preset.items() if r in applicable and r not in explicit})
        roles = requested
        foreign = [r for r in roles if r not in applicable]
        if foreign:
            raise _invalid([{"field": f"roles.{r}", "reason": "role_not_applicable", "card_kind": kind,
                             "applicable_roles": list(applicable)} for r in foreign])
        if kind != fields.CHECK and legacy.node_running(flow_id, node_key):
            raise GraphApiError(409, "node_settings_locked", {"flow_id": flow_id, "node_key": node_key},
                                message="The card is running; its settings cannot change now.")
        if group_id and not roles:
            raise _invalid([{"field": "from_group", "reason": "no_applicable_role", "ai_group_id": group_id}])
        if settings_req:
            run_settings.check(kind, settings_req, "settings")     # every value judged before any role is written
        before = _role_states(flow_id, node, kind, project_id) if roles else {}
        changed = [role for role, value in roles.items() if _write_role(flow_id, node_key, kind, role, value, user_id)]
        changed_settings, settings_before, settings_after = (
            run_settings.write(flow_id, node_key, kind, settings_req, user_id) if settings_req else ([], {}, {}))
        version = current_version
        if changed or changed_settings:
            repository.bump_version(flow_id, node_key)
            version = repository.settings_version(flow_id, node_key)
            from modules.flow_gate.graph_workflow.services import canvas_events
            canvas_events.publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason="settings",
                                                 actor_user_id=user_id, origin_client_id=None, node_keys=[node_key])
        after = _role_states(flow_id, node, kind, project_id) if roles else {}
        at = card_role_store._now()
        audit_ids = [card_role_store.write_audit(
            flow_id, node_key, role, before.get(role), after.get(role), role in changed, version,
            caller.scope.token_id, caller.scope.ai_run_id, caller.chat_doc_id, user_id, at) for role in roles]
        setting_audit_ids = run_settings.audit(
            flow_id, node_key, settings_req, settings_before, settings_after, changed_settings, version,
            {"token_id": caller.scope.token_id, "ai_run_id": caller.scope.ai_run_id, "chat_doc_id": caller.chat_doc_id},
            user_id) if settings_req else []
        return {"changed_roles": changed, "audit_ids": audit_ids,
                "changed_settings": changed_settings, "setting_audit_ids": setting_audit_ids,
                **({"from_group": group_id, "applied_roles": sorted(roles)} if group_id else {})}

    try:
        done = legacy.with_flow_guard(flow_id, guarded)
    finally:
        chat_gate.release(holder.ticket)
    answer = _answer(caller, node_key, card_role_store.placed(flow_id, node_key) or {})
    answer.update(done)
    answer["next"] = NEXT_AFTER_CHANGE
    return answer
