"""Graph confirmation and engine assignment (D0004 §3.1 / §3.3 / §3.7, L0009 §2.4.3, P0008 §6 S4).

Inside one Flow guard: ownership re-judged -> base revision -> draft version -> structure and root
binding -> (revision + assignment + root binding) recorded together. Anything failing leaves
nothing behind, so an unassigned Flow either becomes Graph with all three records or stays
unassigned (L0009 §4.4 fixes the order of the checks, i.e. the precedence of the error codes).
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.graph_workflow.domain.root_binding import doc_key
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store, layout_store

from .canvas_events import publish_canvas_changed, publish_ownership_changed
from . import run_state_ports
from .engine_router import read_ownership, require_graph_editable
from .errors import GraphApiError
from .flow_guard import with_flow_guard
from .graph_service import check_draft, require_writable, version_conflict


def _binding_response(status: str, binding, revision_no: int, record: Optional[dict]) -> dict:
    if status == "bindable":
        return {"status": "bound", "node_key": binding.node_key, "doc_id": binding.doc_id,
                "bound_at_revision_no": revision_no}
    if status == "inherited":
        return {"status": "inherited", "node_key": binding.node_key, "doc_id": binding.doc_id,
                "bound_at_revision_no": (record or {}).get("bound_at_revision_no")}
    return {"status": "not_needed", "node_key": None, "doc_id": None, "bound_at_revision_no": None}


def confirm_revision(group: dict, user_id: str, draft_version: int, base_revision_no: Optional[int],
                     client_id: Optional[str]) -> dict:
    """``POST /flows/{flow_id}/graph/revisions`` -> 201 body (P0008 S4-1~S4-7)."""
    flow_id, project_id = group["group_id"], group.get("project_id")
    require_writable(flow_id)

    def body() -> dict:
        own = read_ownership(flow_id)                                            # 1 담당
        require_graph_editable(flow_id, own)
        run_state_ports.require_editable(flow_id)
        latest = definition_store.latest_revision_no(flow_id)                    # 2 기준 개정
        if base_revision_no != latest:
            raise GraphApiError(409, "revision_conflict", {
                "flow_id": flow_id, "base_revision_no": base_revision_no, "latest_revision_no": latest,
            })
        draft = definition_store.draft(flow_id)                                  # 3 초안
        if draft is None:
            raise GraphApiError(404, "draft_not_found", {"flow_id": flow_id})
        if draft["draft_version"] != draft_version:
            raise version_conflict(flow_id, draft_version, draft)
        run_state_ports.protect_definition(flow_id, draft["nodes"], draft["edges"])
        issues, binding = check_draft(flow_id, own, draft)                       # 4 구조·결합
        if issues:
            raise GraphApiError(422, "graph_validation_failed",
                                {"flow_id": flow_id, "draft_version": draft_version, "issues": issues})
        revision_no = (latest or 0) + 1                                          # 5 한 단위 기록
        confirmed_at = definition_store.insert_revision_from_draft(
            flow_id, revision_no, draft_version, base_revision_no, user_id)
        record = assignment_store.root_binding(flow_id)
        retired: list[str] = []
        assigned_at = (own.assignment or {}).get("assigned_at")
        if own.state == "unassigned":
            assignment_store.insert_assignment(flow_id, revision_no, confirmed_at, user_id)
            assigned_at = confirmed_at
            if binding.status == "bindable":
                assignment_store.insert_root_binding(flow_id, binding.node_key, binding.doc_id, binding.doc_type,
                                                     revision_no, user_id)
                key = doc_key(binding.doc_id)
                layout_store.retire(flow_id, key, "root_binding")                # absorbed, no position handoff
                retired.append(key)
        if latest is not None:
            kept = {n["node_key"] for n in draft["nodes"]}
            for node in (definition_store.revision(flow_id, latest) or {}).get("nodes", []):
                if node["node_key"] not in kept:
                    layout_store.retire(flow_id, node["node_key"], "revision_removed")
        definition_store.delete_draft(flow_id)
        root = _binding_response(binding.status, binding, revision_no, record)
        if own.state == "unassigned":
            publish_ownership_changed(
                project_id=project_id, flow_id=flow_id, to="graph", actor_user_id=user_id,
                origin_client_id=client_id, revision_no=revision_no,
                root_binding=root if root["status"] == "bound" else None,
            )
        publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason="revision", actor_user_id=user_id,
                               origin_client_id=client_id, revision_no=revision_no)
        return {
            "flow_id": flow_id,
            "revision_no": revision_no,
            "confirmed_by": user_id,
            "ownership": {"state": "graph", "sequence_root_doc_id": None, "graph_revision_no": revision_no,
                          "assigned_at": assigned_at},
            "root_binding": root,
            "retired_node_keys": retired,
        }

    return with_flow_guard(flow_id, body)
