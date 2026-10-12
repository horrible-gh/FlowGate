"""Reuse boundary of the card memos (flowgate.v02.0056 T#3, D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed: the
Graph API envelope (user JWT, error shape), the RBAC permission check the envelope uses, the Flow load /
disposal check and the Flow guard. Which card a node key is comes from the card settings (``card_settings``).
"""
from __future__ import annotations

from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services import canvas_service, graph_service
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.rbac.permission_service import has_permission

__all__ = ["GraphApiError", "with_flow_guard", "authenticate", "authorize", "invalid", "run", "load_flow",
           "require_writable", "PERMISSIONS"]

# A memo follows the document permissions (0002-CH turn 10 O3: the existing document authoring rights).
PERMISSIONS = {"read": "perm_document_read", "create": "perm_document_create",
               "update": "perm_document_update", "delete": "perm_document_delete"}


def authenticate(request) -> str:
    return envelope.authenticate(request)


def authorize(user_id: str, group: dict, required: str) -> None:
    """``required``: read / create / update / delete — the document permission of that name."""
    if not has_permission(user_id, group.get("project_id") or "", PERMISSIONS[required]):
        raise GraphApiError(403, "flow_access_denied", {"flow_id": group["group_id"], "required": required})


def invalid(request, errors: list[dict]) -> GraphApiError:
    return envelope.invalid(request, errors)


def run(request, handler, status: int = 200):
    return envelope.run(request, handler, status)


def load_flow(flow_id: str) -> dict:
    return canvas_service.load_flow(flow_id)


def require_writable(flow_id: str) -> None:
    graph_service.require_writable(flow_id)
