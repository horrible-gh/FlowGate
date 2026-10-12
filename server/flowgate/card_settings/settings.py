"""Card run settings rules (flowgate.v02.0053 T#2): read a card's settings, change one value, and
answer what a node's AI start takes from its card.

- A card is a node key of the Flow's Graph definition: a card only in the draft (just placed) and a
  card of confirmed revisions (also one whose run is done) are read and written the same way. Its
  kind (``fields.card_kind``) follows the draft node, else the latest revision node.
- One request changes one value, saved at once (no save button): ``value`` null takes the default
  back. The write runs inside the Flow guard, so it orders with draft saves and run steps.
- While the node is open in the Flow's active run its AI already started with the settings it read,
  and while the card's AI review works on the node's result that review follows them, so the card is
  locked (409 ``node_settings_locked``); before and after that it can be changed.
- The requirement card's AI delegation on / off adds / removes the card's AI delegation document
  line (``card_documents.lines.set_delegation``), in the same guard.
- ``run_settings`` is the Graph's ``card_settings_ports.run_settings`` handler: only stored values are
  handed on, so a card with nothing stored starts exactly as before. ``result_type`` is its
  ``result_type`` handler: the type an AI node's result is bound as (the dispatch told the AI the same).
- ``token_step_type`` is the source tools' ``worker_step_type_hooks`` handler: the token of an open AI
  node whose card changes what it writes is judged by that type (an omitted instruction's report),
  so the source access of the token follows what the AI was told to write.
"""
from __future__ import annotations

from typing import Any, Optional

from flowgate.card_documents import lines as card_lines
from flowgate.card_settings import fields
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import repository

DELEGATION_FIELD = "delegation"


def _card(flow_id: str, node_key: str) -> dict:
    node = repository.card_node(flow_id, node_key)
    if node is None:
        raise legacy.GraphApiError(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key},
                                   message="The card does not exist in this flow.")
    return node


def _payload(flow_id: str, node: dict, stored: dict) -> dict:
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    return {"flow_id": flow_id, "node_key": node["node_key"], "card_kind": kind,
            "locked": legacy.node_running(flow_id, node["node_key"]),
            "fields": fields.view(kind, stored, legacy.repeat_choices())}


def read(group: dict, node_key: str) -> dict:
    """``GET /flows/{flow_id}/nodes/{node_key}/settings``."""
    flow_id = group["group_id"]
    node = _card(flow_id, node_key)
    return _payload(flow_id, node, repository.values(flow_id, node_key))


def write(group: dict, node_key: str, name: str, value: Any, user_id: Optional[str]) -> dict:
    """``PATCH /flows/{flow_id}/nodes/{node_key}/settings`` — one field, saved at once."""
    flow_id = group["group_id"]
    legacy.require_writable(flow_id)
    if not repository.available():
        raise legacy.GraphApiError(409, "card_settings_unavailable", {"flow_id": flow_id},
                                   message="Card settings are not available on this server yet.")
    registered = legacy.registered_doc_types(group.get("project_id"))

    def body() -> dict:
        node = _card(flow_id, node_key)
        kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
        field = fields.field_of(kind, name)
        if field is None:
            raise legacy.GraphApiError(422, "invalid_request", {"errors": [
                {"field": "field", "reason": "not_a_setting_of_this_card", "card_kind": kind}]},
                message="This card has no such setting.")
        if legacy.node_running(flow_id, node_key):
            raise legacy.GraphApiError(409, "node_settings_locked", {"flow_id": flow_id, "node_key": node_key},
                                       message="The card is running; its settings cannot change now.")
        if value is None:
            repository.remove(flow_id, node_key, name)
        else:
            try:
                checked = fields.check_value(field, value, repeat_choices=legacy.repeat_choices(),
                                             team_exists=legacy.team_exists, ai_exists=legacy.ai_exists)
            except fields.InvalidValue as exc:
                raise legacy.GraphApiError(422, "invalid_request", {"errors": [
                    {"field": "value", "reason": exc.reason}]}, message="The value does not fit this setting.")
            repository.put(flow_id, node_key, name, checked, user_id)
        stored = repository.values(flow_id, node_key)
        if name == DELEGATION_FIELD:
            card_lines.set_delegation(group, node, stored.get(DELEGATION_FIELD) == "on", user_id, registered)
        return _payload(flow_id, node, stored)

    return legacy.with_flow_guard(flow_id, body)


def run_settings(flow_id: str, node: dict) -> dict:
    """What a node's AI start takes from its card (``node``: the run's node row)."""
    if not repository.available() or not node.get("node_key"):
        return {}
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    return fields.run_plan(kind, repository.values(flow_id, node["node_key"]), node.get("node_type"),
                           node.get("doc_type"))


def result_type(flow_id: str, node: dict) -> Optional[str]:
    """The type an AI node's result is when its card changes it (instruction omitted), else None.

    Only an AI node reads its card at dispatch, so only an AI node's result type follows the card.
    The card cannot change while the node is open (locked), so this is the type its AI was told.
    """
    if node.get("assignee_kind") != "ai" or not repository.available() or not node.get("node_key"):
        return None
    return fields.result_doc_type(node.get("node_type"), node.get("doc_type"),
                                  repository.values(flow_id, node["node_key"]))


def token_step_type(token_rec: dict) -> Optional[str]:
    """The type the holder of a node's token writes when the node's card changes it, else None.

    Only a token the server mapped to a node (the node's dispatch token, the tokens its AI start and that
    start's retries minted) counts, and only while the node is open in its Flow's active run: the card is
    locked then, so this is the type the dispatch told the AI and the result binder binds (``result_type``).
    """
    if not repository.available():
        return None
    found = legacy.open_node_of_token(token_rec)
    return result_type(*found) if found else None
