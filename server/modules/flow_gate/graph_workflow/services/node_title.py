"""Node title service — a person's card title (0062 D0005 §3.2 / set 1, L0006 2.7 / 4.3, 0064 P0006 §4.2.2~§4.2.4).

``PUT /flows/{flow_id}/graph/nodes/{node_key}/title``. The title lives in the card's title state only: no draft,
revision, label, document title, draft version or Undo history changes. The client's state version is never
asked for: an allowed person save always wins and is never refused by version (the version only orders what a
screen shows). Order (P0006 §4.2.3): the disposal pre-check and the text before the guard, then inside the Flow
guard the final disposal judgement (the DC discard row, read without failing open), ownership, the card,
its kind, the editable judgement and the running lock the card settings use (``card_settings_ports.node_running``),
then one write and, after the commit, ``graph_canvas_changed`` (``title``) for that one card. A refusal writes
nothing and sends nothing. Every Graph / draft node may take a title (Custom included); a free document or a
v0.1 slot cannot (``node_title_not_supported``). A Graph node in neither the draft nor the latest revision is not
found, even while a run's pinned (older) revision still draws it (L0006 2.7 step 2, P0006 §4.2.3).
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.projection.graph_projector import VIEW_ORIGIN, title_state_block
from modules.flow_gate.graph_workflow.store import definition_store, title_store

from . import card_settings_ports
from . import node_title_names as names
from .canvas_events import CANVAS_REASON_TITLE, publish_canvas_changed
from .canvas_service import free_node_keys
from .engine_router import read_ownership
from .errors import GraphApiError
from .flow_guard import with_flow_guard
from .graph_service import require_writable

DISPOSAL_TYPE = "DC"                        # process_service._GROUP_DISCARD_TYPE: the Flow's discard document
EDITABLE_OWNERSHIP = ("graph", "unassigned")


def flow_disposed_strict(flow_id: str) -> bool:
    """0062 L0006 2.7: the source of ``is_group_disposed`` (a DC row of the Flow) read in the caller's guard
    transaction. A failing read is not swallowed: it rolls the save back."""
    row = get_store()._fetch_one(
        "SELECT doc_id FROM documents WHERE group_id = ? AND type_code = ? LIMIT 1", [flow_id, DISPOSAL_TYPE])
    return row is not None


def _graph_row(flow_id: str, node_key: str, draft: Optional[dict]) -> tuple[Optional[dict], str]:
    """The card's node row in the draft, else in the latest revision, and the view it is drawn in."""
    for row in (draft or {}).get("nodes") or []:
        if row["node_key"] == node_key:
            return row, "draft"
    latest_no = definition_store.latest_revision_no(flow_id)
    latest = definition_store.revision(flow_id, latest_no) if latest_no else None
    for row in (latest or {}).get("nodes") or []:
        if row["node_key"] == node_key:
            return row, "revision"
    return None, ""


def _refused(status: int, code: str, node_key: str) -> GraphApiError:
    return GraphApiError(status, code, {"node_key": node_key})


def save_human_title(group: dict, node_key: str, raw_title: str, user_id: str, client_id: Optional[str]) -> dict:
    flow_id, project_id = group["group_id"], group.get("project_id")
    require_writable(flow_id)                                     # fast refusal; the guard judges again
    check = names.validate_title(raw_title, names.HUMAN_RAW_MAX_CHARS)
    if check.reason == "empty":
        raise GraphApiError(422, "title_empty", {})
    if check.reason is not None:
        raise GraphApiError(422, "title_too_long", {"max": names.TITLE_MAX_CHARS, "length": check.length})
    if not title_store.available():
        raise GraphApiError(409, "feature_not_enabled", {"flow_id": flow_id, "feature": "node_title"})

    def body() -> dict:
        if flow_disposed_strict(flow_id):
            raise GraphApiError(409, "flow_disposed", {"flow_id": flow_id})
        own = read_ownership(flow_id)
        draft = definition_store.draft(flow_id)
        if own.state not in EDITABLE_OWNERSHIP or (own.state == "unassigned" and draft is None):
            raise _refused(409, "node_title_not_editable", node_key)
        row, view = _graph_row(flow_id, node_key, draft)
        if row is None:
            if node_key in free_node_keys(flow_id):               # a free document / v0.1 slot of the Canvas
                raise _refused(422, "node_title_not_supported", node_key)
            raise _refused(404, "node_not_found", node_key)       # no card, or one only a run's pinned revision draws
        if not cm.editable_for(own.state, VIEW_ORIGIN[view])["move"]:
            raise _refused(409, "node_title_not_editable", node_key)
        if card_settings_ports.node_running(flow_id, node_key):
            raise _refused(409, "node_title_locked", node_key)
        title_store.save_human(flow_id, node_key, check.value, user_id)
        publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason=CANVAS_REASON_TITLE,
                               actor_user_id=user_id, origin_client_id=client_id, node_keys=[node_key])
        return {"flow_id": flow_id, "node_key": node_key,
                "title_state": title_state_block(title_store.state(flow_id, node_key))}

    return with_flow_guard(flow_id, body)
