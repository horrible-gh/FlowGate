"""Hold (flowgate.v02.0061 T#11, R's Hold, 0002-CH turn 2·3 Q4·Q8, turn 22·28, turn 20).

A held node is not opened by a Graph run. Hold is a card setting (``graph_node_settings`` field ``hold`` = true,
``card_settings``), so it is kept by node key whatever the revision, an idle run (Q4) or a Time Machine leaves it as it
is: only a person's release clears it.

- ``held`` is the Flow's held node keys. The resolver reads them in its run state (``RunState.held``, through
  ``graph_execution_hooks`` ``held_nodes``) and ``frontier.openable_only`` — the step every opening goes through: the
  start node of a ``start``, a propagation's opening right after an approval, a rejection path, the run candidates of a
  command and of a refill (``settle`` / ``scheduling.opening``) — skips them. A step's targets are the nodes its
  decision opens, so they never hold a held node either (``command.record_step_targets`` drops one all the same).
  A held node stays ``pending`` and is not done, so its successors and the joins they enter wait (the propagation
  structure already waits for every predecessor done); the Hold is not set on them, and branches without it go on.
  Hold bounds the opening only: a held node already open / in review goes on (review, approval, integration); once it
  is pending again (Stop, Time Machine) it is not opened. Released, the next step / full command's run candidates open
  it (a propagation that opened nothing because of the Hold is done, the candidates need none).
- ``require_settable`` (inside the Flow guard): a running / paused / stopping run refuses setting and releasing alike
  (409 ``graph_run_locked``, the T#3 edit lock, WP answer 1); a node done in the Flow's run cannot be held (409
  ``graph_hold_done_node``, Q8). Releasing is never refused on an idle run, and it only changes the setting: nothing
  is opened before the next command (R).
  A root-bound start node is bounded too: held, it does not take its root document's state at the run start (it stays
  ``pending``, nothing follows it); released, the next command takes the root's state and goes on
  (``run_service._take_root_binding``), never an ordinary opening of the card.
- ``changed``: every change of the setting is a ``graph_canvas_changed`` (reason ``hold``, the card in ``node_keys``,
  sent after the commit) so every screen reads the Flow's Graph again (its ``hold``) — with or without a run, also for a
  card its run has no node run of; when the Flow's run has the node, also its ``graph_node_state_changed`` (its status
  unchanged, cause ``hold_set`` / ``hold_released``) so the screen reads the run again. No run or node run is made up.
- ``blocks``: the idle reason ``hold`` (``settle.idle_reason``): no run candidate is left — the whole run candidates
  (``frontier.run_candidates``), not only the ones the current step may open: a step's boundary with a candidate left
  waits for the next command, not for a release — and a run target not done is held and still ``pending`` (it, and
  through it every target below it, waits for its release). The completion policy needs every target done, so a run
  with a held target left is never completed.
"""
from __future__ import annotations

from typing import Optional

from flowgate.card_settings import repository
from flowgate.card_settings.fields import HOLD
from flowgate.graph_run import editing
from flowgate.graph_run import legacy_adapter as engine
from flowgate.graph_validation.run_scope import run_scope

FIELD = HOLD.name
DONE_NODE = "graph_hold_done_node"
CAUSE_SET, CAUSE_RELEASED = "hold_set", "hold_released"
CANVAS_REASON = "hold"


def held(flow_id: str) -> frozenset:
    """The Flow's held node keys (``graph_execution_hooks`` ``held_nodes``)."""
    return repository.keys_with(flow_id, FIELD, True)


def require_settable(flow_id: str, node_key: str, on: bool) -> None:
    """Inside the Flow guard, before the setting changes."""
    run = engine.active_run(flow_id)
    if not run:
        return
    params = {"flow_id": flow_id, "run_id": run["run_id"], "status": run["status"]}
    if run["status"] in editing.LOCKED:
        raise engine.GraphApiError(409, "graph_run_locked", params)
    if on and any(r["node_key"] == node_key and r["status"] == "done" for r in engine.node_runs(run["run_id"])):
        raise engine.GraphApiError(409, DONE_NODE, {**params, "node_key": node_key})


def changed(flow_id: str, project_id: Optional[str], node_key: str, on: bool, actor: Optional[str]) -> None:
    """Inside the Flow guard, after the setting changed: the Canvas refresh signal, and the run's node state event (no
    status change) when the Flow's run has the node."""
    engine.publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason=CANVAS_REASON, actor_user_id=actor,
                                  origin_client_id=None, node_keys=(node_key,))
    run = engine.active_run(flow_id)
    if not run:
        return
    row = next((r for r in engine.node_runs(run["run_id"]) if r["node_key"] == node_key), None)
    if row is None:
        return
    ctx = engine.load_context(run, project_id, actor)
    engine.emit_node_state(ctx, node_key, row["status"], row["status"], CAUSE_SET if on else CAUSE_RELEASED,
                           row.get("result_doc_id"), note={"actor_user_id": actor})


def blocks(ctx, state) -> bool:
    """No run candidate is left and a run target not done is held and pending (the idle reason ``hold``). The Hold
    itself, not ``state.held`` (which also keeps a bound node waiting for its root out of the openings)."""
    keys = held(ctx.flow_id)
    if not keys or engine.run_candidates(ctx.revision, state):
        return False
    for key in run_scope(ctx.revision).targets:
        node = state.node(key)
        if key in keys and (node is None or node.status == "pending"):
            return True
    return False
