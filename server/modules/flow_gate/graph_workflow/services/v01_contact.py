"""v0.1 contact delegation (D0004 §3.5 / §4.3, L0009 §2.2.3 / §2.2.4, P0008 §9).

The v0.1 routes call these through ``api.v01_contact`` — one thin call per contact, placed after
the existing v0.1 guards (``group_disposed``, AI run in progress). For an unassigned or v0.1 Flow
the v0.1 behaviour and result are unchanged; a Graph or conflict Flow is refused before the v0.1
engine moves it.

- decide / sequence save (``guarded_v01_assignment``): on an unassigned Flow the v0.1 call runs
  inside the Flow guard, so it and a Graph confirmation cannot both assign the Flow (L0009 E-05).
- advance (``progress_refusal``): read-only judgement.
- inbox registration / review decision (``after_document_event``): after v0.1 committed; a Graph
  Flow hands the event to the result binder, never changing the v0.1 response.
- R↔B root conversion (``follow_root_conversion``): runs inside the guard of ``guarded_v01_assignment``
  after the converter, so a card line holding the root follows its new id (flowgate.v02.0053 T#1).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.graph_workflow.store import definition_store

from . import card_document_ports, result_binder
from .canvas_events import publish_ownership_changed
from .engine_router import Ownership, read_ownership
from .flow_guard import with_flow_guard

_log = logging.getLogger(__name__)


@dataclass
class V01Rejection:
    """P0008 §9 refusal in the v0.1 flat error shape (HTTP 409)."""
    error: str              # "flow_owned_by_graph" | "flow_ownership_conflict"
    doc_id: str
    group_id: str
    graph_revision_no: Optional[int]

    def body(self) -> dict:
        return {"error": self.error, "doc_id": self.doc_id, "group_id": self.group_id,
                "graph_revision_no": self.graph_revision_no}


def _refusal(own: Ownership, doc_id: str, flow_id: str) -> Optional[V01Rejection]:
    if own.state not in ("graph", "conflict"):
        return None
    code = "flow_owned_by_graph" if own.state == "graph" else "flow_ownership_conflict"
    return V01Rejection(code, doc_id, flow_id, definition_store.latest_revision_no(flow_id))


def guarded_v01_assignment(doc: Optional[dict], call: Callable[[], Any], actor: Optional[str] = None) -> Any:
    """decide and sequence save (L0009 §2.2.3). Returns the v0.1 call's result or a V01Rejection.

    ``doc`` is the v0.1 document row the request names (None when it does not exist). A v0.1
    exception propagates unchanged (the guard transaction rolls back, the Flow stays unassigned
    and v0.1 answers with its own error).
    """
    flow_id = (doc or {}).get("group_id")
    if not flow_id:
        return call()                                   # v0.1 reports doc_not_found itself
    doc_id = doc.get("doc_id") or ""
    pre = read_ownership(flow_id)
    refused = _refusal(pre, doc_id, flow_id)
    if refused is not None:
        return refused
    if pre.state == "v01":
        return call()                                   # existing behaviour, unwrapped

    def body() -> Any:
        now = read_ownership(flow_id)                   # the deciding read, inside the guard
        late = _refusal(now, doc_id, flow_id)
        if late is not None:
            return late
        result = call()                                 # v0.1 transactions join this one
        if now.state == "unassigned":
            after = read_ownership(flow_id)
            if after.sequence is not None:
                group = db_groups.get_by_id(flow_id) or {}
                publish_ownership_changed(
                    project_id=group.get("project_id"), flow_id=flow_id, to="v01", actor_user_id=actor,
                    sequence_root_doc_id=after.sequence_root_doc_id,
                )
        return result

    return with_flow_guard(flow_id, body)


def follow_root_conversion(doc: Optional[dict], converted: Any) -> Any:
    """After the R↔B converter, in its transaction: the card lines holding the converted root take
    its new id and kind. Returns the converter's result unchanged (same-type no-op: nothing moves)."""
    flow_id = (doc or {}).get("group_id")
    if flow_id and isinstance(converted, dict) and converted.get("doc_id") not in (None, doc.get("doc_id")):
        card_document_ports.root_converted(flow_id, doc["doc_id"], converted)
    return converted


def progress_refusal(doc: Optional[dict]) -> Optional[V01Rejection]:
    """advance / sequence edit of an existing sequence (L0009 §2.2.4): refusal or None (pass)."""
    flow_id = (doc or {}).get("group_id")
    if not flow_id:
        return None
    return _refusal(read_ownership(flow_id), doc.get("doc_id") or "", flow_id)


def after_document_event(kind: str, flow_id: Optional[str], payload: dict) -> None:
    """inbox registration / review decision, called after v0.1 committed. Never raises."""
    if not flow_id:
        return
    try:
        if read_ownership(flow_id).state == "graph":
            result_binder.on_document_event(kind, flow_id, payload)
    except Exception:  # noqa: BLE001 — L0009 §2.2.4: the v0.1 response stays a success
        _log.exception("graph result delegation failed kind=%s flow=%s", kind, flow_id)
