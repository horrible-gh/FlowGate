"""Worker token step type port (flowgate.v02.0053 T#2, D0006 §2.3).

The remote source tools decide what a worker token may do to the source from the document type its
holder writes (``remote_tool_service._worker_token_step_type_result`` -> ``tool_registry.kind_for_step``):
the token's own ``doc_ref`` type, or the head of the v0.1 workflow sequence it belongs to. A Designer
card node's token names its Flow's root (R / B) as ``doc_ref`` and that Flow has no v0.1 sequence, while
the card (``flowgate.card_settings``, the v0.2 application package) can make the node's AI write another
type than the node's own (instruction omitted: the card's report). That dependency may only point one
way -- ``flowgate`` -> ``modules.flow_gate`` -- so this module offers one named port the judgement
calls, and the composition root (``startup.py``) registers the handler. Nothing here imports ``flowgate``.

    token_step_type   the document type the server recorded for the token's holder to write (the
                      run node the token was mapped to and that node's card), or None: judge as before

* With no handler registered the port answers None, so every token is judged exactly as before.
* A handler exception propagates: the judgement already turns a failed step lookup into the narrowest
  kind (``read``, ``step_lookup_failed``), never into a wider one.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

TOKEN_STEP_TYPE = "token_step_type"
HOOK_NAMES = (TOKEN_STEP_TYPE,)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in HOOK_NAMES:
        raise ValueError(f"unknown worker step type hook: {name}")
    if not callable(handler):
        raise TypeError(f"worker step type hook {name} handler is not callable")
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


def token_step_type(token_rec: dict) -> Optional[str]:
    """The type the holder of ``token_rec`` (its ``tokens`` row) writes, when the server recorded one."""
    handler = _handler(TOKEN_STEP_TYPE)
    if handler is None:
        return None
    return handler(token_rec) or None
