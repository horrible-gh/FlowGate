"""Where a chat's new work cards go in the Flow's definition. Pure: no storage, no guard.

The new cards form one chain ``anchor -> card 1 -> ... -> card n -> end``:

- ``end`` is an existing confirm card (``gate``) when the anchor's only successor is a confirm card that has not
  started in the Flow's run (its input edge ``anchor -> gate`` is replaced: the cards are inserted before it);
- otherwise a new confirm card when ``want_gate`` (the default), else nothing.

A card that is done, open or in review in the current run keeps its input edges (``graph_run.editing.protect_definition``),
so a finished confirm card is never inserted before; the chain then follows the Flow's tail and gets a new confirm card,
as a person extends a finished Flow by hand (B -> confirm -> confirm).

Without an explicit anchor: the single edge ``work card -> unstarted confirm card`` (the anchor's only successor), else the
single tail (a card with no successor). Anything else is ambiguous and names the candidates; the caller passes ``after``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

CLIENT_NODE = "tmp_cc_n"
CLIENT_GATE = "tmp_cc_gate"
CLIENT_EDGE = "tmp_cc_e"


class PlacementError(Exception):
    def __init__(self, code: str, params: dict):
        super().__init__(code)
        self.code, self.params = code, params


@dataclass(frozen=True)
class Plan:
    anchor: str                      # the card the new chain follows
    before: Optional[str] = None     # the existing confirm card the chain ends in
    removed_edge: Optional[str] = None
    new_gate: bool = False


def is_gate(node: Optional[dict]) -> bool:
    return bool(node) and node.get("node_type") == "gate"


def plan(nodes: Iterable[dict], edges: Iterable[dict], protected: Iterable[str] = (),
         after: Optional[str] = None, want_gate: bool = True) -> Plan:
    by_key = {n["node_key"]: n for n in nodes}
    edges = list(edges)
    protected = set(protected)
    if not by_key:
        raise PlacementError("chat_cards_flow_empty", {})
    outs: dict[str, list[dict]] = {}
    for e in edges:
        outs.setdefault(e["source"], []).append(e)

    def insertion(source: str) -> Optional[dict]:
        out = outs.get(source, [])
        if len(out) == 1 and is_gate(by_key.get(out[0]["target"])) and out[0]["target"] not in protected:
            return out[0]
        return None

    if after is not None:
        if after not in by_key:
            raise PlacementError("chat_cards_anchor_unknown", {"after": after})
        edge = insertion(after)
        if edge is not None:
            return Plan(after, edge["target"], edge["edge_id"], False)
        return Plan(after, None, None, want_gate)
    candidates = [e for e in edges
                  if e["source"] in by_key and not is_gate(by_key[e["source"]]) and insertion(e["source"]) is e]
    if len(candidates) == 1:
        e = candidates[0]
        return Plan(e["source"], e["target"], e["edge_id"], False)
    if candidates:
        raise PlacementError("chat_cards_anchor_ambiguous", {"candidates": sorted({e["source"] for e in candidates})})
    tails = sorted(k for k in by_key if not outs.get(k))
    if len(tails) == 1:
        return Plan(tails[0], None, None, want_gate)
    raise PlacementError("chat_cards_anchor_ambiguous", {"candidates": tails})


def _wire_node(node: dict) -> dict:
    """A stored node as the draft save request carries it (as the work plan deploy does)."""
    kind = node.get("assignee_kind")
    assignee = ({"kind": "human", "user_id": node.get("assignee_user_id")} if kind == "human" else
                {"kind": "ai", "provider_id": node.get("assignee_provider_id")} if kind == "ai" else None)
    return {"node_key": node["node_key"], "client_key": None, "node_type": node["node_type"],
            "doc_type": node.get("doc_type"), "palette_item_id": node.get("palette_item_id"),
            "label": node.get("label"), "assignee": assignee}


def draft_body(record: dict, draft_version: int, chosen: Plan, cards: list[dict], gate_label: str) -> dict:
    """The whole draft save request: the definition as it is, the new cards chained in (``cards``: each with
    ``doc_type``, ``label``, ``palette_item_id``)."""
    nodes = [_wire_node(n) for n in record.get("nodes", [])]
    removed = next((e for e in record.get("edges", []) if e.get("edge_id") == chosen.removed_edge), None)
    edges = [{"edge_id": e["edge_id"], "client_key": None, "source": e["source"], "target": e["target"],
              "condition": e.get("condition")} for e in record.get("edges", []) if e is not removed]
    keys = [f"{CLIENT_NODE}{i}" for i in range(len(cards))]
    for key, card in zip(keys, cards):
        nodes.append({"node_key": None, "client_key": key, "node_type": "document", "doc_type": card["doc_type"],
                      "palette_item_id": card.get("palette_item_id"), "label": card["label"], "assignee": None})
    chain = [chosen.anchor] + keys
    if chosen.before is not None:
        chain.append(chosen.before)
    elif chosen.new_gate:
        nodes.append({"node_key": None, "client_key": CLIENT_GATE, "node_type": "gate", "doc_type": None,
                      "palette_item_id": "confirm", "label": gate_label, "assignee": None})
        chain.append(CLIENT_GATE)
    for i, (source, target) in enumerate(zip(chain, chain[1:])):
        condition = removed.get("condition") if removed is not None and i == 0 else None
        edges.append({"edge_id": None, "client_key": f"{CLIENT_EDGE}{i}", "source": source, "target": target,
                      "condition": condition})
    return {"draft_version": draft_version, "nodes": nodes, "edges": edges}
