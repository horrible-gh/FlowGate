"""Node approval state (flowgate.v02.0056 T#1, 0002-CH turn 5~10): is a card waiting for a person's approval?

A card shows one of three states: working (its node is open, or in review while AI still works on the
result), awaiting approval, or approved (its node is done). Awaiting approval means:

- the node is in review with a result, and
- that result was not approved automatically — a result a person wrote, or a card review that ended
  without passing (count spent, failure, stop); an automatic approval would have made the node done —
  and
- no AI works on it any more: neither the node's own AI run nor the card's AI review
  (``card_settings_ports.ai_working``, answered by the card's review stage).

``refusal`` names why a node is not awaiting approval; the run responses show ``awaiting_approval``
(``refusal`` is None) on every node run and the card approval API answers 409 with that reason, so the
two can never disagree. A gate card is decided by its own confirm / reject, never approved here.
A stopped run (다음 단계) does not change the judgement: an approval while stopped makes the node done at
once and only its propagation waits (``frontier.resolve_node_event`` defer).
"""
from __future__ import annotations

from typing import Optional

from . import card_settings_ports

GATE_NODE_TYPE = "gate"
# Why a node's card is not awaiting approval (the card approval API's 409 codes).
NODE_IS_GATE = "node_is_gate"
NODE_NOT_STARTED = "node_not_started"        # pending: the run has not opened it yet
NO_RESULT = "no_result"                      # open: its work has no result yet
AI_RUNNING = "ai_running"                    # in review, but its AI run or its card review still works
ALREADY_APPROVED = "already_approved"        # done
NODE_NOT_IN_REVIEW = "node_not_in_review"    # rejected / closed
REASONS = (NODE_IS_GATE, NODE_NOT_STARTED, NO_RESULT, AI_RUNNING, ALREADY_APPROVED, NODE_NOT_IN_REVIEW)

_BY_STATUS = {"done": ALREADY_APPROVED, "pending": NODE_NOT_STARTED, "open": NO_RESULT}


def refusal(flow_id: str, row: dict, node_type: Optional[str] = None) -> Optional[str]:
    """Why the card of the node run ``row`` cannot be approved now, or None: it awaits approval."""
    if node_type == GATE_NODE_TYPE:
        return NODE_IS_GATE
    status = row.get("status")
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    if status != "review":
        return NODE_NOT_IN_REVIEW
    if not row.get("result_doc_id"):
        return NO_RESULT
    if card_settings_ports.ai_working(flow_id, row["node_key"], row["result_doc_id"]):
        return AI_RUNNING
    return None


def awaiting_approval(flow_id: str, row: dict, node_type: Optional[str] = None) -> bool:
    return refusal(flow_id, row, node_type) is None
