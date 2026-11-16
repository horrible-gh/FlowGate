"""API chat tools and the CLI mention section of the control tower (stage 1).

flowgate.v02gui.0028: ``get_card_roles`` / ``set_card_roles`` read and change the AI roles of a work card this chat
placed (``card_roles``), the API twins of ``GET`` / ``PATCH .../nodes/{node_key}/roles``."""
from __future__ import annotations

import json

from . import authz, card_roles, routes, service

PLACE_TOOL = "place_flow_cards"
START_TOOL = "start_flow_run"
GET_ROLES_TOOL = "get_card_roles"
SET_ROLES_TOOL = "set_card_roles"

GUIDE = (
    "Two ways to change source exist side by side. Direct edit (default): in edit mode you change files yourself "
    "and FlowGate commits them when the chat ends. Card run: when the user asks for it (for example \"카드로 돌려\") or "
    "the change is large, first show the plan (which cards, after which card, what each does) and wait for the "
    "user's go-ahead; then place the cards, write each card's instruction yourself, and start the run. Never switch "
    "paths silently. Card work runs in the card's own workspace and is committed and merged by the card; while the "
    "Flow runs, edit-mode chat is refused (graph_run_locked) but reading and messages work. Keep one change per card "
    "and do not place research/design/plan cards: that part stays in this chat. An instruction is not a summary: put "
    "in the user's own words, facts you checked (file:line), what was decided and rejected, the done criteria and "
    "what not to do."
)

PLACE_DESCRIPTION = (
    "Place work cards on this chat's Flow (edit mode only). One call puts up to 5 cards in one chain after a card "
    "of the Flow and ends the chain in a confirm card: the anchor's unstarted confirm card when it has one, else a "
    "new confirm card (gate=false: none). Without `after` the server picks the single work card -> unstarted confirm "
    "card edge, else the single tail card; when that is ambiguous it answers 409 chat_cards_anchor_ambiguous with "
    "candidates and you pass `after` (a node_key from the canvas read). A Flow with no card yet (a new group whose "
    "bug/requirement is only a document on the canvas) starts the chain with a new start card of that root document "
    "(answer `root`). Nothing is confirmed or run. The answer names each new card's node_key and instruction line; "
    "write the instruction into it (card document edit, slot=instruction) before starting the run."
)
START_DESCRIPTION = (
    "Start this chat's Flow (edit mode only): the draft is confirmed as a new revision and the run starts in full "
    "mode (an idle or paused run goes on with the latest revision). Work cards that still have an empty instruction "
    "line are refused (409 chat_run_instruction_missing) unless allow_instruction_ai=true lets their instruction AI "
    "write one. The answer's `waiting` lists cards that already wait instead of running and why (manual_write_wait: "
    "the card has no AI for that role; ai_setup_required / ai_unavailable; needs_intervention): tell the user. "
    "This chat is not woken when the run stops: tell the user and ask them to message you when it is done."
)

PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_id": {"type": "string", "minLength": 1, "description": "This chat's Flow (its group id); defaults to it."},
        "cards": {
            "type": "array", "minItems": 1, "maxItems": service.MAX_CARDS,
            "items": {"type": "object", "properties": {
                "label": {"type": "string", "minLength": 1, "description": "Card title shown on the canvas."},
                "doc_type": {"type": "string", "enum": list(service.WORK_TYPES),
                             "description": "T (work, default) or TS (test)."}},
                "additionalProperties": False},
        },
        "after": {"type": "string", "minLength": 1, "description": "node_key of the card the chain follows."},
        "gate": {"type": "boolean", "description": "End in a new confirm card when needed (default true)."},
    },
    "required": ["cards"],
    "additionalProperties": False,
}
GET_ROLES_DESCRIPTION = (
    "Read the instruction / execution / review AI of a work card this chat placed (any mode). The answer has each "
    "applicable role's state and selection, settings_version and context (send them back with set_card_roles), "
    "whether the card is locked (running) and `options`: the AIs you may pick (id, name, kind). Other cards are "
    "refused (403 chat_card_not_owned, with the node_keys this chat placed)."
)
SET_ROLES_DESCRIPTION = (
    "Change one or more of the instruction / execution / review AI of a work card this chat placed (edit mode only). "
    "roles: {role: {kind: assigned, provider_id: <an id from get_card_roles options>} or {kind: not_used}}; "
    "expected_settings_version (and expected_context) from get_card_roles. All or nothing. Refusals: 403 "
    "chat_edit_mode_required / chat_card_not_owned, 422 role_not_applicable / ai_not_selectable (another user's or a "
    "disabled AI), 409 node_settings_locked (the card runs) / gate_context_changed (stale version: read again). "
    "Nothing else of the card and never the account defaults change. Only a get_card_roles read after it that shows "
    "the new selection confirms the change; on a refusal tell the user its code and message."
)

_ROLE_VALUE = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": ["assigned", "not_used"]},
                   "provider_id": {"type": "string", "minLength": 1,
                                   "description": "With kind=assigned: an id from get_card_roles options."}},
    "required": ["kind"],
    "additionalProperties": False,
}
GET_ROLES_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_id": {"type": "string", "minLength": 1, "description": "This chat's Flow (its group id); defaults to it."},
        "node_key": {"type": "string", "minLength": 1, "description": "A card this chat placed (place_flow_cards)."},
    },
    "required": ["node_key"],
    "additionalProperties": False,
}
SET_ROLES_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_id": {"type": "string", "minLength": 1, "description": "This chat's Flow (its group id); defaults to it."},
        "node_key": {"type": "string", "minLength": 1, "description": "A card this chat placed (place_flow_cards)."},
        "roles": {"type": "object", "minProperties": 1, "additionalProperties": False,
                  "properties": {role: _ROLE_VALUE for role in card_roles.ROLES}},
        "expected_settings_version": {"type": "integer", "minimum": 0,
                                      "description": "settings_version from get_card_roles."},
        "expected_context": {"type": "object", "description": "context from get_card_roles (optional)."},
    },
    "required": ["node_key", "roles", "expected_settings_version"],
    "additionalProperties": False,
}

START_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_id": {"type": "string", "minLength": 1, "description": "This chat's Flow (its group id); defaults to it."},
        "allow_instruction_ai": {"type": "boolean",
                                 "description": "Let the instruction AI write empty instructions (default false)."},
    },
    "additionalProperties": False,
}

PROMPT_HINT = ("Card run (control tower): `place_flow_cards` then write each card's instruction with the card "
               "document edit tool, then `start_flow_run`. " + GUIDE + " The AI of each role of a card you placed: "
               "`get_card_roles` / `set_card_roles` (edit mode, only cards this chat placed).")


def _result(handler):
    response = routes.response(None, handler)
    return response.status_code, json.loads(response.body)


def _split(tool_input):
    data = dict(tool_input) if isinstance(tool_input, dict) else tool_input
    flow_id = data.pop("flow_id", None) if isinstance(data, dict) else None
    return flow_id, data


def place_call(run, tool_input):
    def handle():
        flow_id, body = _split(tool_input)
        scope, group = authz.from_run(run, flow_id or (run or {}).get("group_id"))
        # flowgate.v02gui.0028: the run's conversation document (bound to its current token by from_run) owns the cards.
        return service.place(group, scope.user_id, body, (run or {}).get("locale"),
                             owner=authz.owner(scope, (run or {}).get("doc_ref")))
    return _result(handle)


def start_call(run, tool_input):
    def handle():
        flow_id, body = _split(tool_input if tool_input is not None else {})
        scope, group = authz.from_run(run, flow_id or (run or {}).get("group_id"))
        return service.start(group, scope.user_id, body or None)
    return _result(handle)


def _node_key(data):
    """The tool input's ``node_key`` (removed from the body), else 422."""
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    key = data.pop("node_key", None) if isinstance(data, dict) else None
    if not isinstance(key, str) or not key:
        raise GraphApiError(422, "invalid_request", {"errors": [{"field": "node_key", "reason": "required"}]})
    return key


def get_roles_call(run, tool_input):
    def handle():
        from modules.flow_gate.graph_workflow.services.errors import GraphApiError
        flow_id, body = _split(tool_input if tool_input is not None else {})
        key = _node_key(body)
        if body:
            raise GraphApiError(422, "invalid_request", {"errors": [
                {"field": name, "reason": "unknown_field"} for name in sorted(body)]})
        caller = authz.caller_from_run(run, flow_id or (run or {}).get("group_id"), write=False)
        return card_roles.view(caller, key)
    return _result(handle)


def set_roles_call(run, tool_input):
    def handle():
        flow_id, body = _split(tool_input if tool_input is not None else {})
        key = _node_key(body)
        caller = authz.caller_from_run(run, flow_id or (run or {}).get("group_id"), write=True)
        return card_roles.change(caller, key, body)
    return _result(handle)


def cli_mention(api_base, raw_token):
    base = str(api_base or "").rstrip("/")
    base = base[:-len("/api/v1")] if base.endswith("/api/v1") else base
    return "\n".join([
        "## Card run (control tower, stage 1)",
        "---",
        GUIDE,
        "",
        f"POST {base}/api/v2/chat/flows/{{flow_id}}/cards",
        f"Authorization: Bearer {raw_token}",
        '{"cards": [{"label": "<card title>", "doc_type": "T"}], "after": "<node_key, optional>", "gate": true}',
        "Places up to 5 work cards (T, or TS for a test) in one chain after a card and ends it in a confirm card",
        "(the anchor's unstarted confirm card, else a new one). Without `after` the server picks the only",
        "work card -> unstarted confirm card edge, else the only tail; otherwise 409 chat_cards_anchor_ambiguous",
        "lists candidates. A Flow with no card yet starts the chain with a start card of its bug/requirement",
        "document (answer `root`). The answer gives each card's node_key and its instruction line: write the",
        "instruction with the card document edit (POST .../nodes/{node_key}/slots/instruction/document/revisions;",
        "content without front matter keeps the document's current front matter).",
        "",
        f"POST {base}/api/v2/chat/flows/{{flow_id}}/runs",
        f"Authorization: Bearer {raw_token}",
        '{"allow_instruction_ai": false}',
        "Confirms the draft and starts the run in full mode (an idle/paused run goes on). Empty instruction lines",
        "are refused (409 chat_run_instruction_missing) unless allow_instruction_ai is true. `waiting` lists cards",
        "that already wait and why (manual_write_wait = no AI on that card role); runs/current node_runs carry the",
        "same wait_reason / intervention later.",
        "",
        f"GET {base}/api/v2/chat/flows/{{flow_id}}/nodes/{{node_key}}/roles",
        f"Authorization: Bearer {raw_token}",
        "The instruction / execution / review AI of a work card this chat placed (any mode): each role's state and",
        "selection, settings_version and context (send them back with a change), locked, and `options` (the AIs",
        "you may pick: id, name, kind). Other cards: 403 chat_card_not_owned (lists the node_keys this chat placed).",
        "",
        f"PATCH {base}/api/v2/chat/flows/{{flow_id}}/nodes/{{node_key}}/roles",
        f"Authorization: Bearer {raw_token}",
        '{"roles": {"execution": {"kind": "assigned", "provider_id": "<options id>"}, "review": {"kind": "not_used"}},',
        ' "expected_settings_version": <from GET>, "expected_context": <context from GET, optional>}',
        "Edit mode only, and only cards this chat placed. Changes only those roles, all or nothing (never the card's",
        "other settings or your account defaults). 422 role_not_applicable / ai_not_selectable, 409",
        "node_settings_locked (the card runs) / gate_context_changed (stale: GET again). Only a GET after it that",
        "shows the new selection counts as confirmed; on a refusal report its code and message to the user.",
        "",
        "flow_id is this chat's group id. Edit mode only: reading mode answers 403 chat_edit_mode_required.",
        "You are not woken when the run stops: report what you started and ask the user to message you when it",
        "is done, then read runs/current and the canvas. Neither call consumes this token.",
    ])


def descriptors():
    return [
        {"name": PLACE_TOOL, "description": PLACE_DESCRIPTION, "schema": PLACE_SCHEMA, "handler": place_call,
         "cli_mention": cli_mention, "prompt_hint": PROMPT_HINT},
        {"name": START_TOOL, "description": START_DESCRIPTION, "schema": START_SCHEMA, "handler": start_call},
        {"name": GET_ROLES_TOOL, "description": GET_ROLES_DESCRIPTION, "schema": GET_ROLES_SCHEMA,
         "handler": get_roles_call},
        {"name": SET_ROLES_TOOL, "description": SET_ROLES_DESCRIPTION, "schema": SET_ROLES_SCHEMA,
         "handler": set_roles_call},
    ]
