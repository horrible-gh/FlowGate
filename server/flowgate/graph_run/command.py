"""The step / full command (flowgate.v02.0061 T#2, 0002-CH turn 33).

A command is ``step`` (open the run candidates of the moment; what they make runnable waits for the next command) or
``full`` (go on opening until no candidate is left). ``POST /flows/{flow_id}/runs`` and ``POST /runs/{run_id}/resume``
take it as ``mode``; the engine (``run_service._go_on``) asks here which command a paused / idle run goes on with and
records it through here:

- ``command_of``: a mode given is a new command; an idle run always begins a new one (no mode: its last command's
  mode); a paused run given no mode goes on with its own command (None: a step keeps its targets).
- ``record_command``: the new command is stored on the running run — mode, no step targets yet — before the run
  status event, so the event, the stored run and the response tell the same command.
- ``record_step_targets``: a step's targets are the nodes its command's decision opens (the drained propagations and
  the run candidates of the moment); a full command has none. T#11: never a held node (the decision opens none; one is
  dropped all the same, so a refill of the step cannot open it either).
"""
from __future__ import annotations

from typing import Optional

from flowgate.graph_run import legacy_adapter as engine


def command_of(run: dict, mode: Optional[str]) -> Optional[str]:
    if mode is not None:
        return mode
    if run.get("status") == engine.IDLE:
        return run.get("run_mode") or engine.MODE_FULL
    return None


def record_command(ctx, mode: str) -> None:
    if engine.set_run_command(ctx.run_id, mode, None) != 1:
        raise AssertionError("command missed its run")
    ctx.run["run_mode"], ctx.run["step_node_keys"] = mode, None


def record_step_targets(ctx, mode: str, decision) -> None:
    if mode != engine.MODE_STEP:
        return
    from flowgate.graph_run import hold
    keys = sorted(set(decision.open) - hold.held(ctx.flow_id))
    if engine.set_run_command(ctx.run_id, engine.MODE_STEP, keys) != 1:
        raise AssertionError("step command missed its run")
    ctx.run["step_node_keys"] = keys
