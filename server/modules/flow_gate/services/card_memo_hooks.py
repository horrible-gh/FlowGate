"""Card memo mention port (flowgate.v02.0056 T#3, D0006 §2.3).

A Designer card's memos (``flowgate.card_memos``, the v0.2 application package) reach the AI that works for
the card: the memos a person turned on for AI reading go into the mention as one "User memos" section,
title and body, placed before the mention as ``invoke_mention_service.prepend_messages_section`` places the
user messages. The mentions are built in ``modules.flow_gate`` (the Graph node's AI start, the per-document
AI invoke, the Q&A answer), and that dependency may only point one way -- ``flowgate`` ->
``modules.flow_gate`` -- so this module offers one named port the mention builders call, and the
composition root (``startup.py``) registers the handler. Nothing here imports ``flowgate``.

    memo_section   ``(flow_id, node_key, doc_id)`` -> the section text, or "" (no memo to give): the memos
                   of the card ``node_key``, or of the cards whose document lines show ``doc_id``

* With no handler registered the port answers "", so every mention is exactly what it was before.
* A memo is advice to the AI, never a condition of its start: a handler exception is logged and the
  mention goes out without the section.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MEMO_SECTION = "memo_section"
HOOK_NAMES = (MEMO_SECTION,)
# The separator ``invoke_mention_service`` puts between a leading section and the mention.
SECTION_SEPARATOR = "\n\n"

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in HOOK_NAMES:
        raise ValueError(f"unknown card memo hook: {name}")
    if not callable(handler):
        raise TypeError(f"card memo hook {name} handler is not callable")
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


def memo_section(flow_id: Optional[str], *, node_key: Optional[str] = None, doc_id: Optional[str] = None) -> str:
    """The "User memos" section for the card ``node_key`` (or the cards showing ``doc_id``), or "". Never raises."""
    handler = _handler(MEMO_SECTION)
    if handler is None or not flow_id or not (node_key or doc_id):
        return ""
    try:
        return str(handler(flow_id, node_key, doc_id) or "")
    except Exception:  # noqa: BLE001 — a memo never stops an AI start
        logger.warning("card memo section failed flow=%s node=%s doc=%s (ignored)", flow_id, node_key, doc_id,
                       exc_info=True)
        return ""


def prepend_memo_section(mention_text: str, flow_id: Optional[str], *, node_key: Optional[str] = None,
                         doc_id: Optional[str] = None) -> str:
    """``mention_text`` with the card's memo section before it (unchanged when there is none)."""
    section = memo_section(flow_id, node_key=node_key, doc_id=doc_id)
    if not section:
        return mention_text
    if not mention_text:
        return section
    return section + SECTION_SEPARATOR + mention_text
