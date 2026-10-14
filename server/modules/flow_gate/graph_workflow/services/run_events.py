"""Run SSE publication (P0008 §8.2 / §8.3): ``graph_run_status_changed``, ``graph_node_state_changed``.

Queued with ``after_commit`` in ``event_seq`` order, so a rolled-back step publishes nothing.
Run events carry no ``origin_client_id``: a tab filters them by ``event_seq`` (P0008 §8.1).
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.api.v1.events.event_types import EventType
from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
from modules.flow_gate.db.connection import after_commit

RUN_STATUS = EventType.GRAPH_RUN_STATUS_CHANGED.value
NODE_STATE = EventType.GRAPH_NODE_STATE_CHANGED.value


def queue(project_id: Optional[str], flow_id: str, event_type: str, payload: dict,
          doc_id: Optional[str] = None) -> None:
    event = FlowEvent(event_type=event_type, payload=payload, audience="*", project=project_id,
                      group_id=flow_id, doc_id=doc_id)

    def _send() -> None:
        broadcast_event_threadsafe(event)

    if not after_commit(_send):
        _send()


# Run status changes no person asked for (0061 T#2: idle, and an idle run whose existing work goes on again).
AUTO_CAUSES = ("completed", "run_idle", "work_resumed")


def run_status_payload(run: dict, seq: int, from_status: Optional[str], to_status: str, cause: str,
                       actor_user_id: Optional[str], reason: Optional[str]) -> dict:
    """flowgate.v02.0061 T#2: with the run's command ``mode`` and, going idle, its ``idle_reason``. A command that
    changes the mode is stored on ``run`` before its event is made, so the event tells the command it goes on with."""
    return {
        "flow_id": run["group_id"], "run_id": run["run_id"], "revision_no": run["revision_no"],
        "event_seq": seq, "from": from_status, "to": to_status, "cause": cause,
        "actor_user_id": actor_user_id if cause not in AUTO_CAUSES else None, "reason": reason,
        "mode": run.get("run_mode") or "full", "idle_reason": run.get("idle_reason") if to_status == "idle" else None,
    }


def node_state_payload(run: dict, seq: int, node_key: str, from_status: str, to_status: str,
                       canvas_state: Optional[str], cause: str, result_doc_id: Optional[str],
                       dispatch: Optional[dict]) -> dict:
    return {
        "flow_id": run["group_id"], "run_id": run["run_id"], "revision_no": run["revision_no"],
        "event_seq": seq, "node_key": node_key, "from": from_status, "to": to_status,
        "canvas_state": canvas_state, "cause": cause, "result_doc_id": result_doc_id, "dispatch": dispatch,
    }
