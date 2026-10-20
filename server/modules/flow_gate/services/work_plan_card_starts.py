"""Graph card start port (flowgate.v02.0074 T0017).

Whether a work plan card already ran in the v0.2 Graph is a rule of ``flowgate.work_plan_deploy``: the node whose
hidden ``wp_source`` card setting names the plan card, and that node's run facts (``node_started``). The WP read and
the WP saves need the same answer as the deploy so a started card's instruction choice is locked in the editor, on
save and on deploy alike. ``modules.flow_gate`` never imports ``flowgate``, so they ask through this port and the
composition root (``startup.py``) registers the handler.

    card_starts   ``doc`` (the WP document row) -> ``{card_id: True | False | None}`` of the plan's deployed Graph
                  cards: True when the card ran, None when its run facts cannot be read, False otherwise

* No handler registered: ``{}`` — no Graph card is known, as before this port (a server without the v0.2 package).
  The deploy keeps its own check, so nothing a started card holds is changed by it.
* A handler exception propagates: the read drops it as a hint, the save refuses the change.
* One handler. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

_handler: Optional[Callable[[dict], dict]] = None
_lock = threading.Lock()


def register(handler: Callable[[dict], dict]) -> None:
    global _handler
    if not callable(handler):
        raise TypeError("work plan card start handler is not callable")
    with _lock:
        _handler = handler


def unregister() -> None:
    global _handler
    with _lock:
        _handler = None


def registered() -> bool:
    with _lock:
        return _handler is not None


def card_starts(doc: dict) -> dict:
    with _lock:
        handler = _handler
    if handler is None:
        return {}
    return dict(handler(doc) or {})
