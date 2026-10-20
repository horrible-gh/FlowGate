"""Work plan deploy port handler: how the WP read and saves reach this package (flowgate.v02.0074 T0017).

``modules.flow_gate.services.work_plan_card_starts`` defines the port; the composition root (``startup.py``) calls
:func:`register` once at startup.

    card_starts   which deployed cards of a plan already ran (``service.card_starts``): the WP editor locks their
                  instruction choice and the WP saves refuse to change it, as the deploy does
"""
from __future__ import annotations

from modules.flow_gate.services import work_plan_card_starts as port

from flowgate.work_plan_deploy import service


def register() -> None:
    port.register(service.card_starts)


def unregister() -> None:
    port.unregister()
