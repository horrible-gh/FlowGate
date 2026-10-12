"""Card approval port handler: how the Graph workflow reaches this package (D0006 §2.3 / §4.1).

``modules.flow_gate.graph_workflow.services.card_approval_ports`` defines the Graph's port; the composition
root (``startup.py``) calls :func:`register` once at startup.

    pending_documents   result binding: the card's documents other than the node's result that are not
                        approved yet (``approval.pending_documents``) — the node is done only when none is left
"""
from __future__ import annotations

from modules.flow_gate.graph_workflow.services import card_approval_ports as ports

from flowgate.card_approval import approval


def register() -> None:
    ports.register(ports.PENDING_DOCUMENTS, approval.pending_documents)


def unregister() -> None:
    for name in ports.PORT_NAMES:
        ports.unregister(name)
