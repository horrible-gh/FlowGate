"""Card completion ports (flowgate.v02.0119 T#2, T-1; D0006 §2.1 / §2.3 / §4.1).

A Graph node's ``review -> done`` follows its completion record (``flowgate.card_completion``), never the document's v0.1
review status: the record is the only source of "this node is done". ``modules.flow_gate`` never imports ``flowgate``, so
this module offers named ports the Graph calls and the composition root (``startup.py``) registers the handlers.

    completion_of   result binding / reconciliation (``result_binder.next_missing_event``) and a code node's merge end
                    (``result_merge._finish``), inside the Flow guard or a run read: the completion of the node's result
                    ``doc`` in review — ``{"completion_id", "legacy"}`` — or None (the node stays in review)
    applied         ``run_apply.apply_decision``, inside the step's guard, when a ``review_approved`` makes the node done
                    (before its attempt ends): the record turns ``applied`` in that transaction; its id (the node event's
                    ``completion_id``), or None
    unsettled       ``result_binder.needs_reconcile`` (read only, before a run read): a written record of the Flow no
                    longer answers its node (or its node is done with it) — the reconciliation must settle it
    settle          ``result_binder.reconcile_links`` (inside the guard of a run read's reconciliation or a command): the
                    Flow's written records that no longer answer their node turn ``superseded`` (done with them:
                    ``applied``); the rows changed

* With no handler registered nothing completes a node: ``completion_of`` answers None whatever the document's review
  status is (no v0.1 compatibility branch — an existing run's approved result is judged by the registered service only),
  ``applied`` answers None, ``unsettled`` False and ``settle`` 0. The server's start registers the handlers and fails
  when it cannot (``startup.start_card_completion_integration``).
* A handler exception propagates: the callers run inside a run step's Flow guard or a run read.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

COMPLETION_OF = "completion_of"
APPLIED = "applied"
UNSETTLED = "unsettled"
SETTLE = "settle"
PORT_NAMES = (COMPLETION_OF, APPLIED, UNSETTLED, SETTLE)

_log = logging.getLogger(__name__)
_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in PORT_NAMES:
        raise ValueError(f"unknown card completion port: {name}")
    if not callable(handler):
        raise TypeError(f"card completion port {name} handler is not callable")
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


def completion_of(flow_id: str, run: dict, node_key: str, doc: dict) -> Optional[dict]:
    """``run``: the run row; ``doc``: the node's result document row (the node in review). Unregistered: None."""
    handler = _handler(COMPLETION_OF)
    if handler is None:
        _log.warning("card completion port not registered: flow=%s node=%s stays in review", flow_id, node_key)
        return None
    found = handler(flow_id, run, node_key, doc)
    return dict(found) if found else None


def applied(flow_id: str, run_id: str, node_key: str, result_doc_id: Optional[str],
            event_seq: Optional[int] = None) -> Optional[str]:
    handler = _handler(APPLIED)
    if handler is None:
        return None
    found = handler(flow_id, run_id, node_key, result_doc_id, event_seq)
    return str(found) if found else None


def unsettled(flow_id: str, run: Optional[dict]) -> bool:
    handler = _handler(UNSETTLED)
    return bool(handler(flow_id, run)) if handler is not None else False


def settle(flow_id: str, run: Optional[dict]) -> int:
    handler = _handler(SETTLE)
    return int(handler(flow_id, run) or 0) if handler is not None else 0
