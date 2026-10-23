"""Card document ports (flowgate.v02.0053 T#1, D0006 §2.1 / §2.3 / §4.1).

The documents every Designer card owns are a v0.2 feature: they live in the application package
``flowgate.card_documents``. The Graph workflow calls them at a few points of its existing flow,
and ``modules.flow_gate`` never imports ``flowgate``, so this module offers named ports the Graph
calls and the composition root (``startup.py``) registers the handlers.

    on_draft_saved     draft save, inside its Flow guard: the cards' documents follow the draft;
                       returns ``node_key -> [line]`` for the save response
    card_view          Canvas read: the card lines of each node and the documents drawn in a card
    on_result_bound    run step, inside its Flow guard: a result bound to a node fills its line
    on_root_converted  R↔B root conversion, inside its Flow guard: the line holding the root follows;
                       returns the node keys whose requirement line holds it (the draft cards to sync)
    card_line_documents  AI start (0064 T-B1e, read only): the live documents the lines of a card show --
                       the card ``node_key``, or the cards whose lines show ``doc_id`` -- for the body precheck

* With no handler registered a port is a no-op (no lines, nothing hidden), so the Graph behaves
  exactly as it did before the ports existed.
* A handler exception propagates: the write ports run inside the caller's Flow guard, so the
  documents commit with the draft / the node transition / the conversion or not at all.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Iterable, Optional

ON_DRAFT_SAVED = "on_draft_saved"
CARD_VIEW = "card_view"
ON_RESULT_BOUND = "on_result_bound"
ON_ROOT_CONVERTED = "on_root_converted"
CARD_LINE_DOCUMENTS = "card_line_documents"
PORT_NAMES = (ON_DRAFT_SAVED, CARD_VIEW, ON_RESULT_BOUND, ON_ROOT_CONVERTED, CARD_LINE_DOCUMENTS)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


class EmptyCardView:
    """What the Canvas reads with no handler: no card lines, no document drawn inside a card."""

    hidden_doc_ids: frozenset = frozenset()

    def lines_for(self, node: dict) -> list:
        return []


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in PORT_NAMES:
        raise ValueError(f"unknown card document port: {name}")
    if not callable(handler):
        raise TypeError(f"card document port {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def clear() -> None:
    with _lock:
        _handlers.clear()


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Optional[Callable[..., Any]]:
    with _lock:
        return _handlers.get(name)


def draft_saved(group: dict, user_id: Optional[str], nodes: list[dict], removed_keys: Iterable[str],
                registered_types: set[str], previous_nodes: Iterable[dict], locale: Optional[str] = None) -> dict:
    """``previous_nodes``: the nodes of the draft (or, for a new draft, the revision) the save replaces.
    ``locale``: the saving request's, handed on as is (flowgate.v02.0088: the documents made are titled in it)."""
    handler = _handler(ON_DRAFT_SAVED)
    if handler is None:
        return {}
    return handler(group, user_id, nodes, list(removed_keys), registered_types, list(previous_nodes),
                   locale=locale) or {}


def card_view(flow_id: str, docs_by_id: dict):
    """An object with ``lines_for(node) -> [line]`` and ``hidden_doc_ids``."""
    handler = _handler(CARD_VIEW)
    return handler(flow_id, docs_by_id) if handler is not None else EmptyCardView()


def result_bound(flow_id: str, node_key: str, doc_id: Optional[str]) -> None:
    handler = _handler(ON_RESULT_BOUND)
    if handler is not None and doc_id:
        handler(flow_id, node_key, doc_id)


def root_converted(flow_id: str, old_doc_id: str, converted: dict) -> list[str]:
    """The node keys whose requirement line now holds the converted root (none without a handler)."""
    handler = _handler(ON_ROOT_CONVERTED)
    if handler is None:
        return []
    return list(handler(flow_id, old_doc_id, converted) or [])


def card_line_doc_ids(flow_id: Optional[str], *, node_key: Optional[str] = None,
                      doc_id: Optional[str] = None) -> list[str]:
    """The doc ids the lines of the card ``node_key`` (or of the cards showing ``doc_id``) show, card by card in
    line order (none without a handler, a Flow or a card)."""
    handler = _handler(CARD_LINE_DOCUMENTS)
    if handler is None or not flow_id or not (node_key or doc_id):
        return []
    return [d for d in handler(flow_id, node_key, doc_id) or [] if d]
