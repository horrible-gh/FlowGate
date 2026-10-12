"""Card settings port handlers: how the Graph workflow reaches this package (D0006 §2.3 / §4.1).

``modules.flow_gate.graph_workflow.services.card_settings_ports`` defines the Graph's ports and
``modules.flow_gate.services.ai_run_end_hooks`` the AI invoke engine's run-end port; the composition
root (``startup.py``) calls :func:`register` once at startup.

    run_settings     node dispatch: what a node's AI start takes from its card (``settings.run_settings``)
    result_type      result binding: the type an AI node's result is (``settings.result_type``)
    review_running   reconciliation: the card's AI review still works on a result (``review_stage``)
    on_run_ended     a run ended and released its lease: the card's review stage (``review_stage``)
    token_step_type  the source tools' step type of a node token (``settings.token_step_type``)
"""
from __future__ import annotations

from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
from modules.flow_gate.services import ai_run_end_hooks, worker_step_type_hooks

from flowgate.card_settings import review_stage, settings


def register() -> None:
    ports.register(ports.RUN_SETTINGS, settings.run_settings)
    ports.register(ports.RESULT_TYPE, settings.result_type)
    ports.register(ports.REVIEW_RUNNING, review_stage.review_running)
    ai_run_end_hooks.register(ai_run_end_hooks.ON_RUN_ENDED, review_stage.on_run_ended)
    worker_step_type_hooks.register(worker_step_type_hooks.TOKEN_STEP_TYPE, settings.token_step_type)


def unregister() -> None:
    for name in ports.PORT_NAMES:
        ports.unregister(name)
    ai_run_end_hooks.unregister(ai_run_end_hooks.ON_RUN_ENDED)
    worker_step_type_hooks.unregister(worker_step_type_hooks.TOKEN_STEP_TYPE)
