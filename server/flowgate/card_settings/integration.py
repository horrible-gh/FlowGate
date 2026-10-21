"""Card settings port handlers: how the Graph workflow reaches this package (D0006 §2.3 / §4.1).

``modules.flow_gate.graph_workflow.services.card_settings_ports`` defines the Graph's ports and
``modules.flow_gate.services.ai_run_end_hooks`` the AI invoke engine's run-end port; the composition
root (``startup.py``) calls :func:`register` once at startup.

    uses_ai          the effective execution assignment (explicit human stays human; default run cards use AI)
    run_settings     node dispatch: what a node's AI start takes from its card (``settings.run_settings``)
    result_type      result binding: the type an AI node's result is (``settings.result_type``)
    ai_working       node approval state: the node's AI run or the card's AI review still works (``review_stage``)
    instruction_document   the instruction stage: the card's instruction type and document, written or not
                     (``settings.instruction_document``, flowgate.v02.0056 T#2)
    approve_instruction    the instruction stage: the instruction's automatic approval (``review_stage``)
    on_run_ended     a run ended and released its lease: the card's instruction / review stage (``review_stage``)
    on_run_ending    a run is about to release its lease: the node this stage will follow stays worked on
                     (``review_stage``, flowgate.v02.0061 T#2)
    on_question_answered   a question was answered: the card whose AI asked it goes on after its last answer
                     (``question_wait``, flowgate.v02.0063)
    token_step_type  the source tools' step type of a node token (``settings.token_step_type``)
    node_running     a card title save: the card is running (``legacy_adapter.node_running``, the card settings lock;
                     0064 T-B4b)
    person_result    result binding: a person's work plan result takes its card's review or automatic approval
                     (``review_stage``, flowgate.v02.0083 WP-4)
    after_registration   the inbox's card title candidate of a registration (``modules.flow_gate.services.
                     node_title_hooks``): the Graph's AI naming applies it (``node_title_ai``, 0064 T-B4d)
"""
from __future__ import annotations

from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
from modules.flow_gate.graph_workflow.services import node_title_ai
from modules.flow_gate.services import ai_run_end_hooks, node_title_hooks, worker_step_type_hooks

from flowgate.card_settings import legacy_adapter, question_wait, review_stage, settings


def register() -> None:
    ports.register(ports.USES_AI, settings.uses_ai)
    ports.register(ports.RUN_SETTINGS, settings.run_settings)
    ports.register(ports.RESULT_TYPE, settings.result_type)
    ports.register(ports.AI_WORKING, review_stage.ai_working)
    ports.register(ports.INSTRUCTION_DOCUMENT, settings.instruction_document)
    ports.register(ports.APPROVE_INSTRUCTION, review_stage.approve_instruction)
    ports.register(ports.REVIEW_INSTRUCTION, review_stage.review_instruction)
    ports.register(ports.NODE_RUNNING, legacy_adapter.node_running)
    ports.register(ports.PERSON_RESULT, review_stage.person_result)          # flowgate.v02.0083 WP-4
    ai_run_end_hooks.register(ai_run_end_hooks.ON_RUN_ENDED, review_stage.on_run_ended)
    ai_run_end_hooks.register(ai_run_end_hooks.ON_RUN_ENDING, review_stage.on_run_ending)
    ai_run_end_hooks.register(ai_run_end_hooks.ON_QUESTION_ANSWERED, question_wait.on_question_answered)
    worker_step_type_hooks.register(worker_step_type_hooks.TOKEN_STEP_TYPE, settings.token_step_type)
    node_title_hooks.register(node_title_hooks.AFTER_REGISTRATION, node_title_ai.after_registration_title)


def unregister() -> None:
    for name in ports.PORT_NAMES:
        ports.unregister(name)
    ai_run_end_hooks.unregister(ai_run_end_hooks.ON_RUN_ENDED)
    ai_run_end_hooks.unregister(ai_run_end_hooks.ON_RUN_ENDING)
    ai_run_end_hooks.unregister(ai_run_end_hooks.ON_QUESTION_ANSWERED)
    worker_step_type_hooks.unregister(worker_step_type_hooks.TOKEN_STEP_TYPE)
    node_title_hooks.unregister(node_title_hooks.AFTER_REGISTRATION)
