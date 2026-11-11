"""API chat tools and the CLI mention section of the control tower (stage 1)."""
from __future__ import annotations

import json

from . import authz, routes, service

PLACE_TOOL = "place_flow_cards"
START_TOOL = "start_flow_run"

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
    "candidates and you pass `after` (a node_key from the canvas read). Nothing is confirmed or run. The answer "
    "names each new card's node_key and instruction line; write the instruction into it (card document edit, "
    "slot=instruction) before starting the run."
)
START_DESCRIPTION = (
    "Start this chat's Flow (edit mode only): the draft is confirmed as a new revision and the run starts in full "
    "mode (an idle or paused run goes on with the latest revision). Work cards that still have an empty instruction "
    "line are refused (409 chat_run_instruction_missing) unless allow_instruction_ai=true lets their instruction AI "
    "write one. This chat is not woken when the run stops: tell the user and ask them to message you when it is done."
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
               "document edit tool, then `start_flow_run`. " + GUIDE)


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
        return service.place(group, scope.user_id, body, (run or {}).get("locale"))
    return _result(handle)


def start_call(run, tool_input):
    def handle():
        flow_id, body = _split(tool_input if tool_input is not None else {})
        scope, group = authz.from_run(run, flow_id or (run or {}).get("group_id"))
        return service.start(group, scope.user_id, body or None)
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
        "lists candidates. The answer gives each card's node_key and its instruction line: write the instruction",
        "with the card document edit (POST .../nodes/{node_key}/slots/instruction/document/revisions).",
        "",
        f"POST {base}/api/v2/chat/flows/{{flow_id}}/runs",
        f"Authorization: Bearer {raw_token}",
        '{"allow_instruction_ai": false}',
        "Confirms the draft and starts the run in full mode (an idle/paused run goes on). Empty instruction lines",
        "are refused (409 chat_run_instruction_missing) unless allow_instruction_ai is true.",
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
    ]
