"""Run state port handlers: how the Graph workflow reaches this package (D0006 §2.3).

``modules.flow_gate.graph_workflow.services.run_state_ports`` defines the ports; the composition root
(``startup.py``) calls :func:`register` once at startup.

    settle               after a step: whether the run goes on (``settle.settle_run``)
    settle_plan          a run read: whether a settle has anything to do (``settle.settle_plan``)
    command_of           the command a paused / idle run goes on with (``command.command_of``)
    record_command       the new command, before its run status event (``command.record_command``)
    record_step_targets  a step's targets (``command.record_step_targets``)
"""
from __future__ import annotations

from modules.flow_gate.graph_workflow.services import run_state_ports as ports

from flowgate.graph_run import command, settle, editing, revision


def register() -> None:
    from flowgate.graph_run import execution
    execution.register()
    ports.register(ports.REQUIRE_EDITABLE, editing.require_editable)
    ports.register(ports.PROTECT_DEFINITION, editing.protect_definition)
    ports.register(ports.ADOPT_REVISION, revision.adopt_revision)
    ports.register(ports.SETTLE, settle.settle_run)
    ports.register(ports.SETTLE_PLAN, settle.settle_plan)
    ports.register(ports.COMMAND_OF, command.command_of)
    ports.register(ports.RECORD_COMMAND, command.record_command)
    ports.register(ports.RECORD_STEP_TARGETS, command.record_step_targets)


def unregister() -> None:
    from modules.flow_gate.services import graph_execution_hooks
    graph_execution_hooks.unregister()
    for name in ports.PORT_NAMES:
        ports.unregister(name)
