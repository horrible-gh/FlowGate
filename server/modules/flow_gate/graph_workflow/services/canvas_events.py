"""Graph SSE publication: ``graph_canvas_changed`` and ``flow_ownership_changed``.

Queued after commit; best-effort delivery (an event is a refresh signal, never the source of
state). Sent through the existing publisher and stream — no new stream.
"""
from __future__ import annotations

from typing import Optional, Sequence

from modules.flow_gate.api.v1.events.event_types import EventType
from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
from modules.flow_gate.db.connection import after_commit


CANVAS_REASON_DOCUMENTS = "documents"
CANVAS_REASON_TITLE = "title"
_NODE_KEY_REASONS = ("layout", "hold", "settings", CANVAS_REASON_TITLE)


def _queue(event: FlowEvent) -> None:
    def _send() -> None:
        broadcast_event_threadsafe(event)

    if not after_commit(_send):
        _send()


def publish_canvas_changed(
    *,
    project_id: Optional[str],
    flow_id: str,
    reason: str,
    actor_user_id: Optional[str],
    origin_client_id: Optional[str],
    node_keys: Sequence[str] = (),
    draft_version: Optional[int] = None,
    revision_no: Optional[int] = None,
) -> None:
    """``reason``: "layout" | "draft" | "revision" | "identity_rematch" | "hold" (the earlier implementation: a card's
    Hold was set / released, with or without a run; ``node_keys`` names the card),
    or "documents" when document body facts changed without a draft change, or "title" (0064 T-B4b, the plan:
    a card's title was saved; ``node_keys`` names that one card)."""
    _queue(FlowEvent(
        event_type=EventType.GRAPH_CANVAS_CHANGED.value,
        payload={
            "flow_id": flow_id,
            "reason": reason,
            "actor_user_id": actor_user_id,
            "origin_client_id": origin_client_id,
            "draft_version": draft_version if reason == "draft" else None,
            "revision_no": revision_no if reason == "revision" else None,
            "node_keys": list(node_keys) if reason in _NODE_KEY_REASONS else [],
        },
        audience="*",
        project=project_id,
        group_id=flow_id,
        doc_id=None,
    ))


def publish_archive_changed(*, project_id: str, flow_id: str, archived: bool, actor_user_id: str) -> None:
    """Reuse the existing tree refresh stream after the display-state transaction commits."""
    _queue(FlowEvent(
        event_type=EventType.GROUP_VIEW_REFRESH.value,
        payload={"group_id": flow_id, "reason": "flow_archived" if archived else "flow_restored",
                 "actor_user_id": actor_user_id},
        audience="*", project=project_id, group_id=flow_id,
    ))


def publish_module_archive_changed(*, project_id: str, module: str, archived: bool, actor_user_id: Optional[str]) -> None:
    """a Group (module) archive / restore refreshes every open tree of the project.

    The same ``group_view_refresh`` stream as the Flow archive, project-wide: no Flow is named
    (``group_id`` None), so per-Flow listeners (inspector, document windows) ignore it."""
    _queue(FlowEvent(
        event_type=EventType.GROUP_VIEW_REFRESH.value,
        payload={"group_id": None, "module": module,
                 "reason": "module_archived" if archived else "module_restored",
                 "actor_user_id": actor_user_id},
        audience="*", project=project_id, group_id=None,
    ))


def publish_module_deleted(*, project_id: str, module: str, actor_user_id: Optional[str]) -> None:
    """an empty Group (module) was deleted; refresh every open tree.

    Same project-wide ``group_view_refresh`` as the Group archive (``group_id`` None)."""
    _queue(FlowEvent(
        event_type=EventType.GROUP_VIEW_REFRESH.value,
        payload={"group_id": None, "module": module, "reason": "module_deleted",
                 "actor_user_id": actor_user_id},
        audience="*", project=project_id, group_id=None,
    ))


def publish_ownership_changed(
    *,
    project_id: Optional[str],
    flow_id: str,
    to: str,
    actor_user_id: Optional[str],
    origin_client_id: Optional[str] = None,
    revision_no: Optional[int] = None,
    sequence_root_doc_id: Optional[str] = None,
    root_binding: Optional[dict] = None,
) -> None:
    """unassigned -> ``to`` ("graph" on confirmation, "v01" on a guarded v0.1 decide)."""
    binding = {"node_key": root_binding["node_key"], "doc_id": root_binding["doc_id"]} if root_binding else None
    _queue(FlowEvent(
        event_type=EventType.FLOW_OWNERSHIP_CHANGED.value,
        payload={
            "flow_id": flow_id,
            "from": "unassigned",
            "to": to,
            "revision_no": revision_no if to == "graph" else None,
            "sequence_root_doc_id": sequence_root_doc_id if to == "v01" else None,
            "root_binding": binding,
            "actor_user_id": actor_user_id,
            "origin_client_id": origin_client_id if to == "graph" else None,
        },
        audience="*",
        project=project_id,
        group_id=flow_id,
        doc_id=sequence_root_doc_id if to == "v01" else (binding or {}).get("doc_id"),
    ))
