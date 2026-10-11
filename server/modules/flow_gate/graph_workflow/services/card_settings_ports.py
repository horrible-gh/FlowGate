"""Card run settings ports (flowgate.v02.0053 T#2, D0006 §2.1 / §2.3 / §4.1).

The settings every Designer card (Node) keeps are a v0.2 feature: they live in the application
package ``flowgate.card_settings``. The Graph run reads them when it hands a node out and when it
binds a node's result, and ``modules.flow_gate`` never imports ``flowgate``, so this module offers
named ports the Graph calls and the composition root (``startup.py``) registers the handlers.

    run_settings     node dispatch, inside its Flow guard: what the node's AI start takes from its card
    result_type      result binding / reconciliation, inside its Flow guard: the document type the
                     node's result is (instruction omitted: the card's report), or None (the node's own)
    review_running   reconciliation, inside its Flow guard: the card's AI review is still working on
                     the node's result, so its momentary rejection is not the review's outcome yet

``run_settings`` answers a dict; every key is optional and an absent key keeps the existing default:

    provider_id         the AI that runs the node (else the node's assignee provider)
    step_timeout_sec    the run's time budget (else the AI invoke engine's single-run formula)
    restart_max         no-output restarts on the same AI (else none, as before)
    result_doc_type     the document type the AI writes (else the node's own type)
    settings            every setting of the card as stored (carried on the job for the record)

* With no handler registered ``run_settings`` answers ``{}``, ``result_type`` None and
  ``review_running`` False, so a node starts and binds exactly as it did before.
* A handler exception propagates: the callers run inside a run step's Flow guard.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

RUN_SETTINGS = "run_settings"
RESULT_TYPE = "result_type"
REVIEW_RUNNING = "review_running"
PORT_NAMES = (RUN_SETTINGS, RESULT_TYPE, REVIEW_RUNNING)

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


def run_settings(flow_id: str, node: dict) -> dict:
    """``node``: the run's node row (node_key, node_type, doc_type, assignee …)."""
    handler = _handler(RUN_SETTINGS)
    if handler is None:
        return {}
    return dict(handler(flow_id, node) or {})


def result_type(flow_id: str, node: dict) -> Optional[str]:
    """The type a result of ``node`` (the run's node row) must have, or None: the node's own type."""
    handler = _handler(RESULT_TYPE)
    if handler is None:
        return None
    return handler(flow_id, node) or None


def review_running(flow_id: str, node_key: str, doc_id: Optional[str]) -> bool:
    """The card's AI review is working on ``doc_id`` (the node's result)."""
    handler = _handler(REVIEW_RUNNING)
    if handler is None or not doc_id:
        return False
    return bool(handler(flow_id, node_key, doc_id))
