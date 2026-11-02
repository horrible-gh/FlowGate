"""Card completion port handler: how the Graph workflow reaches this package (flowgate.v02.0119 T#2, T-1; D0006 §2.3).

``modules.flow_gate.graph_workflow.services.card_completion_ports`` defines the Graph's port; the composition root
(``startup.py``) calls :func:`register` once at startup, which also records when the service was installed (the boundary
between existing runs and runs completed by records, ``repository.ensure_installed``). A failed registration fails the
server's start (``startup.start_card_completion_integration``): with no handler nothing completes a node.

    completion_of   result binding / reconciliation / a code node's merge end: is the node's result in review complete
                    (a live record of its attempt — or an existing run's approved result with no record of the node)
    applied         ``apply_decision``, the node's done: the record turns ``applied`` in the same transaction; its id goes
                    on the node event
    unsettled       before a run read: a written record of the Flow that the reconciliation must settle
    settle          inside the reconciliation's guard: the Flow's written records that no longer answer their node turn
                    ``superseded`` (done with them: ``applied``)
"""
from __future__ import annotations

import logging

from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

from flowgate.card_completion import completion, repository

_log = logging.getLogger(__name__)


def register() -> None:
    ports.register(ports.COMPLETION_OF, completion.completion_of)
    ports.register(ports.APPLIED, completion.applied)
    ports.register(ports.UNSETTLED, completion.unsettled)
    ports.register(ports.SETTLE, completion.settle)
    try:
        repository.ensure_installed()
    except Exception:  # noqa: BLE001 — no boundary yet: no run reads as an existing one until the next start writes it
        _log.warning("card completion install mark not written", exc_info=True)


def unregister() -> None:
    for name in ports.PORT_NAMES:
        ports.unregister(name)
