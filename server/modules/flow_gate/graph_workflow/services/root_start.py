"""(CH): a bug / requirement Flow has its start card from the moment its root is registered.

The Flow's one live root (R or B, ``root_binding.resolve_root``) is the start card a person connects cards from and
runs: a real draft card of the root's type whose requirement line holds that root (``card_documents.lines``
``_existing_root``, through the draft save), not a document node drawn like a card. ``ensure_start_card`` puts that
card into the draft when the definition has no card of the root's type yet. It is called

- when the root is registered (``process_service.create_workflow_root``: the console / GUI and a chat's document
  create);
- at startup for every Flow made before this rule (``ensure_all``; e.g. the earlier change, flowgate.tr-update.0001,
  ): the cards already placed stay as they are;
- by the chat card API (``chat_control.service.place``), whatever the draft already holds.

No edge is made: a person connects the start card (a card nothing connects is left out of a run, as before). Nothing
happens for a Flow owned by v0.1 or already confirmed with its root bound, with no or several live roots, with a card
of the root's type already there (also one with an input edge: the structure stays the person's), or whose project
does not register the root's type. The card takes the place the root's document node had on the Canvas.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.graph_workflow.domain import draft_request as dr
from modules.flow_gate.graph_workflow.domain.root_binding import doc_key, resolve_root
from modules.flow_gate.graph_workflow.projection.flow_snapshot import (
    alive_documents,
    read_snapshot,
    registered_doc_types,
)
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store, layout_store

from .engine_router import read_ownership
from .root_conversion_cards import ROOT_NAMES, ROOT_PALETTE_IDS

log = logging.getLogger(__name__)

CLIENT_START = "tmp_root_start"


def _locale(locale: Optional[str]) -> str:
    return locale if locale in ("ko", "ja", "en") else "ko"


def _node_input(i: int, n: dict) -> dr.NodeInput:
    return dr.NodeInput(index=i, node_key=n["node_key"], client_key=None, node_type=n["node_type"],
                        doc_type=n.get("doc_type"), palette_item_id=n.get("palette_item_id"), label=n["label"],
                        assignee_kind=n.get("assignee_kind"), assignee_user_id=n.get("assignee_user_id"),
                        assignee_provider_id=n.get("assignee_provider_id"))


def start_root(flow_id: str) -> Optional[dict]:
    """The Flow's root a start card is missing for: ``{"doc_id", "doc_type"}``, else None (nothing to do)."""
    if read_ownership(flow_id).state != "unassigned" or assignment_store.root_binding(flow_id) is not None:
        return None
    status, candidates = resolve_root(alive_documents(read_snapshot(flow_id)))
    if status != "one":
        return None
    root_type = str(candidates[0].get("type_code") or "").upper()
    if root_type not in ROOT_NAMES:
        return None
    draft = definition_store.draft(flow_id)
    nodes = (draft or {}).get("nodes") or []
    if any(n.get("node_type") == "document" and n.get("doc_type") == root_type for n in nodes):
        return None
    return {"doc_id": candidates[0]["doc_id"], "doc_type": root_type}


def ensure_start_card(group: dict, user_id: Optional[str], locale: Optional[str] = None) -> Optional[str]:
    """Inside the Flow guard of ``group``: the start card of the Flow's root put into the draft (one draft save,
    ``graph_service.write_draft``: version, card lines, Canvas event). Its node key, else None (nothing to do)."""
    from . import card_settings_ports, graph_service
    flow_id = group["group_id"]
    root = start_root(flow_id)
    if root is None:
        return None
    registered = registered_doc_types(group.get("project_id"))
    if root["doc_type"] not in registered:
        return None
    draft = definition_store.draft(flow_id)
    record = draft or {"nodes": [], "edges": []}
    nodes = [_node_input(i, n) for i, n in enumerate(record["nodes"])]
    nodes.append(dr.NodeInput(index=len(nodes), node_key=None, client_key=CLIENT_START, node_type="document",
                              doc_type=root["doc_type"], palette_item_id=ROOT_PALETTE_IDS[root["doc_type"]],
                              label=ROOT_NAMES[root["doc_type"]][_locale(locale)]))
    edges = [dr.EdgeInput(index=i, edge_id=e["edge_id"], client_key=None, source=e["source"], target=e["target"],
                          condition=e.get("condition")) for i, e in enumerate(record["edges"])]
    request = dr.DraftRequest(draft_version=int(draft["draft_version"]) if draft else 0, nodes=nodes, edges=edges)
    saved = graph_service.write_draft(group, user_id, request, None, registered, _locale(locale))
    key = saved["key_map"][CLIENT_START]
    stored = definition_store.draft(flow_id) or {"nodes": []}
    card_settings_ports.cards_created(group, user_id, [n for n in stored["nodes"] if n["node_key"] == key])
    at = layout_store.positions(flow_id).get(doc_key(root["doc_id"]))
    if at is not None:
        layout_store.save_position_if_absent(flow_id, key, at[0], at[1])
    return key


def ensure_in_guard(flow_id: str, user_id: Optional[str] = None, locale: Optional[str] = None) -> Optional[str]:
    """``ensure_start_card`` in a Flow guard of its own (a registration that just committed its root). Never raises:
    a Flow that cannot take the card now is logged and gets it at the next startup or chat placement."""
    from modules.flow_gate.db import groups as db_groups
    from .flow_guard import with_flow_guard
    try:
        if not definition_store.available():
            return None
        group = db_groups.get_by_id(flow_id)
        if group is None:
            return None
        return with_flow_guard(flow_id, lambda: ensure_start_card(group, user_id, locale))
    except Exception as exc:
        log.warning("[root_start] %s: start card not placed: %s", flow_id, exc)
        return None


def ensure_all() -> list[str]:
    """Startup: every Flow with a live root and no start card of its type gets one (Flows made before this rule).
    The Flows that got their start card."""
    if not definition_store.available():
        return []
    from modules.flow_gate.db.connection import get_store
    from . import graph_service
    from .errors import GraphApiError
    rows = get_store()._fetch_all(
        "SELECT DISTINCT d.group_id FROM documents d WHERE d.group_id IS NOT NULL AND d.type_code IN ('R', 'B') "
        "AND NOT EXISTS (SELECT 1 FROM graph_assignments a WHERE a.group_id = d.group_id) ORDER BY d.group_id", [])
    placed = []
    for row in rows or []:
        flow_id = row["group_id"]
        try:
            graph_service.require_writable(flow_id)     # an archived / disposed Flow is left as it is
        except GraphApiError:
            continue
        try:
            if start_root(flow_id) is None:
                continue
        except Exception as exc:
            log.warning("[root_start] %s: startup check failed: %s", flow_id, exc)
            continue
        if ensure_in_guard(flow_id):
            placed.append(flow_id)
    return placed
