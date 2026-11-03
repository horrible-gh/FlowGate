"""Node approval state (flowgate.v02.0056 T#1, 0002-CH turn 5~10) — revised by flowgate.v02.0119 T#3 (T-2).

A card shows one of these states: working (its node is open, or in review while AI still works on the result),
done (its node is done), and — for a general card in review that nothing completes — ``needs_intervention`` or
``system_error`` (``intervention``).

flowgate.v02.0119 T#3 (T-2, 0004-CH seq 9 §1 / T-2): a general card is never approved by a person. Its ``review -> done``
comes from its completion record only (``card_completion_ports``, T-1). A card in review with a result that no AI works
on any more (neither the node's own AI run nor the card's AI review, ``card_settings_ports.ai_working``) and whose
integration does not wait is in intervention:

- ``needs_intervention``: its automatic rework ran first and ended without a completion (count spent, hold, stall,
  failure, the AI's abnormal end) — or nothing told why. Only its descendants wait (a node not done propagates nothing:
  the ``stop_path`` way); independent branches go on. It moves again by a memo and a rework / re-review request, by
  the person's step / full command when its review count was spent on a revision no review answered (its re-review,
  flowgate.v02.0125 T#1), or by the Time Machine — never by an approval.
- ``system_error``: the automatic completion path failed (the ``auto_approval_skipped`` family: ``issuer_missing``,
  ``approval_refused``, ``review_not_started``, ``review_result_unknown``). It is the run's error family and the run
  read's reconciliation retries it; no person approves it either.

Which of the two is the card's (``card_settings_ports.intervention``, answered by ``flowgate.card_intervention``).

flowgate.v02.0119 T#3 (T-2 rework, review P2): the automatic path of the instruction stage fails the same way (its
instruction's automatic approval: ``issuer_missing`` / ``approval_refused``; its review: ``review_not_started`` /
``review_result_unknown``). A card open in its instruction stage whose instruction no AI works on (neither its writer
nor its review) and whose latest reason recorded for that instruction is one of those is a ``system_error`` too — the
node run tells it and the run is in its error family. Anything else of an open card stays as it was (working, a
person's write or setup wait: no intervention here); a gate is never one.

``refusal`` names why a node cannot be approved; it never answers None any more (the card approval API, removed by T-3,
answers 409 with it — ``needs_intervention`` / ``system_error`` for a card in review that nothing completes), so
``awaiting_approval`` is always False. A gate card is decided by its own confirm, never approved here. A code node whose
result waits for its merge (``INTEGRATION_WAITING``) is an execution gate, not an intervention.
"""
from __future__ import annotations

from typing import Optional

from . import card_settings_ports

GATE_NODE_TYPE = "gate"
# Why a node's card cannot be approved (the card approval API's 409 codes).
NODE_IS_GATE = "node_is_gate"
NODE_NOT_STARTED = "node_not_started"        # pending: the run has not opened it yet
NO_RESULT = "no_result"                      # open: its work has no result yet
AI_RUNNING = "ai_running"                    # in review, but its AI run or its card review still works
ALREADY_APPROVED = "already_approved"        # done
NODE_NOT_IN_REVIEW = "node_not_in_review"    # rejected / closed
INTEGRATION_WAITING = "integration_waiting"  # 0061 T#9: completed, in review until its result is merged
NEEDS_INTERVENTION = "needs_intervention"    # 0119 T-2: nothing completes it; a memo rework / re-review or the Time Machine
SYSTEM_ERROR = "system_error"                # 0119 T-2: its automatic completion path failed; retried, never a person's
REASONS = (NODE_IS_GATE, NODE_NOT_STARTED, NO_RESULT, AI_RUNNING, ALREADY_APPROVED, NODE_NOT_IN_REVIEW,
           INTEGRATION_WAITING, NEEDS_INTERVENTION, SYSTEM_ERROR)
INTERVENTION_KINDS = (NEEDS_INTERVENTION, SYSTEM_ERROR)

_BY_STATUS = {"done": ALREADY_APPROVED, "pending": NODE_NOT_STARTED, "open": NO_RESULT}


def _settled_refusal(flow_id: str, row: dict, node_type: Optional[str]) -> Optional[str]:
    """Why the node run ``row`` is no card in intervention (it is not in review with a result, AI works on it, its
    integration waits), or None: it is in review and nothing completes it."""
    if node_type == GATE_NODE_TYPE:
        return NODE_IS_GATE
    status = row.get("status")
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    if status != "review":
        return NODE_NOT_IN_REVIEW
    if not row.get("result_doc_id"):
        return NO_RESULT
    from modules.flow_gate.services import graph_execution_hooks
    if row.get("run_id") and graph_execution_hooks.call("integration_waiting", row["run_id"], row["node_key"]):
        return INTEGRATION_WAITING
    if card_settings_ports.ai_working(flow_id, row["node_key"], row["result_doc_id"]):
        return AI_RUNNING
    return None


def _state(flow_id: str, row: dict) -> dict:
    found = card_settings_ports.intervention(flow_id, row)
    found = found if isinstance(found, dict) else {}
    kind = found.get("kind")
    return {"kind": kind if kind in INTERVENTION_KINDS else NEEDS_INTERVENTION, "reason": found.get("reason")}


INSTRUCTION_STAGE = "instruction"


def _instruction_error(flow_id: str, row: dict, node_type: Optional[str]) -> Optional[dict]:
    """0119 T-2 rework: ``{"kind": system_error, "reason": …}`` of a general card open in its instruction stage whose
    automatic path failed on its instruction (the latest reason recorded for it), else None — a gate, another stage,
    no instruction yet, an AI still working on it (its writer, its review loop, the run-end hand-off), no reason or a
    reason that is no system error (a person's wait is judged as before)."""
    if node_type == GATE_NODE_TYPE or row.get("status") != "open" or row.get("stage") != INSTRUCTION_STAGE:
        return None
    doc_id = row.get("instruction_doc_id")
    if not doc_id or card_settings_ports.ai_working(flow_id, row["node_key"], doc_id):
        return None
    found = _state(flow_id, row)
    return found if found["kind"] == SYSTEM_ERROR and found.get("reason") else None


def intervention(flow_id: str, row: dict, node_type: Optional[str] = None) -> Optional[dict]:
    """0119 T-2: ``{"kind": needs_intervention | system_error, "reason": …}`` of a general card in review that nothing
    completes, else None (not in review with a result, a gate, AI still working, its integration waiting). 0119 T-2
    rework: a card open in its instruction stage whose instruction's automatic path failed is a ``system_error``
    (``_instruction_error``)."""
    if row.get("status") == "open":
        return _instruction_error(flow_id, row, node_type)
    if _settled_refusal(flow_id, row, node_type) is not None:
        return None
    return _state(flow_id, row)


def refusal(flow_id: str, row: dict, node_type: Optional[str] = None) -> str:
    """Why the card of the node run ``row`` cannot be approved: never None (0119 T-2, no person approves a card)."""
    settled = _settled_refusal(flow_id, row, node_type)
    return settled if settled is not None else _state(flow_id, row)["kind"]


def awaiting_approval(flow_id: str, row: dict, node_type: Optional[str] = None) -> bool:
    """Kept for the readers T-3 / T-5 remove: no card awaits a person's approval (0119 T-2) — always False."""
    return False
