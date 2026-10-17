"""Card run settings ports (flowgate.v02.0053 T#2, D0006 §2.1 / §2.3 / §4.1).

The settings every Designer card (Node) keeps are a v0.2 feature: they live in the application
package ``flowgate.card_settings``. The Graph run reads them when it hands a node out, when it
binds a node's result and when it tells whether a node awaits approval, and ``modules.flow_gate``
never imports ``flowgate``, so this module offers
named ports the Graph calls and the composition root (``startup.py``) registers the handlers.

    uses_ai          whether the node uses AI (explicit assignment, or an unassigned runnable Designer card)
    run_settings     node dispatch, inside its Flow guard: what the node's AI start takes from its card
    result_type      result binding / reconciliation, inside its Flow guard: the document type the
                     node's result is (instruction omitted: the card's report), or None (the node's own).
                     The node row may carry its node run's card run stage as ``run_stage`` (flowgate.v02.0056
                     T#2): None there is a node run opened before migration 148, whose AI ran one stage
    ai_working       node approval state (``node_approval``): AI still works on a node in review —
                     the node's own AI run has not ended, or the card's AI review works on its result —
                     so the node is not waiting for a person's approval (flowgate.v02.0056 T#1)
    instruction_document   the instruction stage (flowgate.v02.0056 T#2), at its start and when its document
                     is registered: the card's instruction type and its instruction line's document
                     (``doc_type``, ``doc_id`` or None, ``written``: it has a body), or ``{}`` (no stage)
    approve_instruction    the instruction stage: approve the card's instruction automatically, without a
                     review (``flow_id, node, doc_id, actor_user_id`` -> True when approved); a refusal is
                     left to the card approval (False), the stage goes on
    node_running     a card title save, inside its Flow guard (0064 T-B4b, 0062 L0006 2.7 step 4): the card is
                     running — open in the active run, or in review while its AI review works — the same
                     judgement that locks the card settings

``run_settings`` answers a dict; every key is optional and an absent key keeps the existing default:

    provider_id         the AI that runs the node (else the node's assignee provider)
    step_timeout_sec    the run's time budget (else the AI invoke engine's single-run formula)
    restart_max         no-output restarts on the same AI (else none, as before)
    result_doc_type     the document type the AI writes (else the node's own type)
    instruction         the card runs an instruction stage first (flowgate.v02.0056 T#2):
                        ``{"doc_type": the instruction type, "provider_id": its AI or None (the assignee)}``
    settings            every setting of the card as stored (carried on the job for the record)

* With no handler registered ``run_settings`` answers ``{}``, ``result_type`` None,
  ``ai_working`` False, ``instruction_document`` ``{}``, ``approve_instruction`` False and ``node_running``
  False, so a node starts and binds exactly as it did before (one stage).
* A handler exception propagates: the callers run inside a run step's Flow guard or a run read.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

USES_AI = "uses_ai"
RUN_SETTINGS = "run_settings"
RESULT_TYPE = "result_type"
AI_WORKING = "ai_working"
INSTRUCTION_DOCUMENT = "instruction_document"
APPROVE_INSTRUCTION = "approve_instruction"
NODE_RUNNING = "node_running"
PORT_NAMES = (USES_AI, RUN_SETTINGS, RESULT_TYPE, AI_WORKING, INSTRUCTION_DOCUMENT, APPROVE_INSTRUCTION, NODE_RUNNING)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in PORT_NAMES:
        raise ValueError(f"unknown card settings port: {name}")
    if not callable(handler):
        raise TypeError(f"card settings port {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Optional[Callable[..., Any]]:
    with _lock:
        return _handlers.get(name)


def uses_ai(flow_id: str, node: dict) -> bool:
    """One assignment judgement for dispatch, instruction/result binding and run-end processing.
    Without the v0.2 handler only an explicit AI assignment runs, as before."""
    handler = _handler(USES_AI)
    return bool(handler(flow_id, node)) if handler else node.get("assignee_kind") == "ai"


def run_settings(flow_id: str, node: dict) -> dict:
    """``node``: the run's node row (node_key, node_type, doc_type, assignee …)."""
    handler = _handler(RUN_SETTINGS)
    if handler is None:
        return {}
    return dict(handler(flow_id, node) or {})


def result_type(flow_id: str, node: dict) -> Optional[str]:
    """The type a result of ``node`` (the run's node row, with ``run_stage`` when the binder knows its node run)
    must have, or None: the node's own type."""
    handler = _handler(RESULT_TYPE)
    if handler is None:
        return None
    return handler(flow_id, node) or None


def ai_working(flow_id: str, node_key: str, doc_id: Optional[str]) -> bool:
    """AI still works on the node whose result in review is ``doc_id``: its own run or the card's review."""
    handler = _handler(AI_WORKING)
    if handler is None:
        return False
    return bool(handler(flow_id, node_key, doc_id))


def instruction_document(flow_id: str, node: dict) -> dict:
    """``node`` (the run's node row): ``{doc_type, doc_id, written}`` of its card's instruction, or ``{}``."""
    handler = _handler(INSTRUCTION_DOCUMENT)
    if handler is None:
        return {}
    return dict(handler(flow_id, node) or {})


def approve_instruction(flow_id: str, node: dict, doc_id: str, actor_user_id: str) -> bool:
    """Approve the card's instruction ``doc_id`` with no review (True when it is approved)."""
    handler = _handler(APPROVE_INSTRUCTION)
    if handler is None:
        return False
    return bool(handler(flow_id, node, doc_id, actor_user_id))


def node_running(flow_id: str, node_key: str) -> bool:
    """The card is running (open, or its AI review works on its result): its title cannot change now."""
    handler = _handler(NODE_RUNNING)
    if handler is None:
        return False
    return bool(handler(flow_id, node_key))
