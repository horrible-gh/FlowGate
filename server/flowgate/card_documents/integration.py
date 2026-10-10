"""Card document port handlers: how the Graph workflow reaches this package (D0006 §2.3 / §4.1).

``modules.flow_gate.graph_workflow.services.card_document_ports`` defines the ports; the
composition root (``startup.py``) calls :func:`register` once at startup.

    on_draft_saved     draft save: the cards' documents follow the draft (``lines.sync_draft``)
    card_view          Canvas read: each node's card lines, the documents drawn in a card
    on_result_bound    run step: a result bound to a node fills its line (``lines.adopt_result``)
    on_root_converted  R↔B root conversion: the line holding the root follows it
"""
from __future__ import annotations

from modules.flow_gate.graph_workflow.services import card_document_ports as ports

from flowgate.card_documents import lines


def register() -> None:
    ports.register(ports.ON_DRAFT_SAVED, lines.sync_draft)
    ports.register(ports.CARD_VIEW, lines.CardView)
    ports.register(ports.ON_RESULT_BOUND, lines.adopt_result)
    ports.register(ports.ON_ROOT_CONVERTED, lines.follow_root_conversion)


def unregister() -> None:
    for name in ports.PORT_NAMES:
        ports.unregister(name)
