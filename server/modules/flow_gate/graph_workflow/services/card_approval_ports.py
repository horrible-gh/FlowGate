"""Card approval ports (flowgate.v02.0056 T#1, D0006 §2.1 / §2.3 / §4.1).

A card is approved as a whole (0002-CH turn 5~10): its node is done only when every document of the card's
approval lines is approved — the instruction as well as the result (T0004 §4 "역방향"). Which documents a
card has and whether they are written is a v0.2 feature (``flowgate.card_approval``), and
``modules.flow_gate`` never imports ``flowgate``, so this module offers a named port the Graph calls and the
composition root (``startup.py``) registers the handler.

    pending_documents   result binding / reconciliation, inside its Flow guard: the documents of the card
                        of ``node`` other than its result ``result_doc_id`` that are not approved yet (a
                        line's empty draft is no document); while any is left, the result's approval is no
                        node event and the node stays in review, awaiting the card's approval

* With no handler registered ``pending_documents`` answers ``[]``: the result's approval moves the node on,
  exactly as before the port existed.
* A handler exception propagates: the callers run inside a run step's Flow guard or a run read.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

PENDING_DOCUMENTS = "pending_documents"
PORT_NAMES = (PENDING_DOCUMENTS,)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in PORT_NAMES:
        raise ValueError(f"unknown card approval port: {name}")
    if not callable(handler):
        raise TypeError(f"card approval port {name} handler is not callable")
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


def pending_documents(flow_id: str, node: dict, result_doc_id: Optional[str]) -> list[str]:
    """``node``: the run's node row (node_key, node_type, doc_type …); ``result_doc_id``: the node's result."""
    handler = _handler(PENDING_DOCUMENTS)
    if handler is None:
        return []
    return list(handler(flow_id, node, result_doc_id) or [])
