"""The deploy chain after a WP card (flowgate.v02.0074 T#4, D0006 §3.6.2) — pure, no storage.

The chain is the path from the WP card along edges that have no condition, are their source's only outgoing edge and
their target's only incoming edge, and lead to a card deployed from this plan. Its last card is the tail; the node
the tail's one outgoing edge leads to, when it is not a deployed card, is the outside next node.

The chain is sound when every deployed card of the plan is on it and the tail has no outgoing edge or one without a
condition. Only a sound chain is edited (new cards after the tail, removed cards bridged over); any other edge or
condition is read and left exactly as it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chain:
    wp_key: str
    cards: list = field(default_factory=list)       # deployed node keys on the chain, in order
    sound: bool = True
    outside_next: Optional[str] = None             # the node after the tail that is not a deployed card
    tail_edge: Optional[dict] = None               # the tail's outgoing edge to ``outside_next``
    off_chain: list = field(default_factory=list)  # deployed node keys the chain does not reach
    break_edges: list = field(default_factory=list)  # edge ids that make the chain unsound

    @property
    def tail(self) -> str:
        return self.cards[-1] if self.cards else self.wp_key


def _index(edges: list[dict]) -> tuple[dict, dict]:
    out: dict[str, list] = {}
    inc: dict[str, list] = {}
    for e in edges:
        out.setdefault(e["source"], []).append(e)
        inc.setdefault(e["target"], []).append(e)
    return out, inc


def walk(wp_key: str, edges: list[dict], deployed: set[str]) -> Chain:
    out, inc = _index(edges)
    chain = Chain(wp_key)
    seen = {wp_key}
    current = wp_key
    while True:
        outs = out.get(current, [])
        if len(outs) != 1 or outs[0].get("condition") is not None:
            break
        nxt = outs[0]["target"]
        if nxt not in deployed or nxt in seen or len(inc.get(nxt, [])) != 1:
            break
        chain.cards.append(nxt)
        seen.add(nxt)
        current = nxt
    chain.off_chain = sorted(deployed - set(chain.cards))
    if chain.off_chain:
        chain.sound = False
        chain.break_edges = sorted({e["edge_id"] for k in chain.off_chain
                                    for e in out.get(k, []) + inc.get(k, [])})
    tail_outs = out.get(chain.tail, [])
    if chain.cards:
        if len(tail_outs) > 1 or (tail_outs and tail_outs[0].get("condition") is not None):
            chain.sound = False
            chain.break_edges = sorted(set(chain.break_edges) | {e["edge_id"] for e in tail_outs})
        elif tail_outs and tail_outs[0]["target"] in deployed:
            chain.sound = False
            chain.break_edges = sorted(set(chain.break_edges) | {tail_outs[0]["edge_id"]})
        elif tail_outs:
            chain.outside_next, chain.tail_edge = tail_outs[0]["target"], tail_outs[0]
    # any other edge touching a chain card (a branch, a merge, an incoming edge from outside) breaks it
    for key in chain.cards:
        extra = [e for e in out.get(key, []) + inc.get(key, [])
                 if not _on_chain(e, chain)]
        if extra:
            chain.sound = False
            chain.break_edges = sorted(set(chain.break_edges) | {e["edge_id"] for e in extra})
    return chain


def _on_chain(edge: dict, chain: Chain) -> bool:
    path = [chain.wp_key] + chain.cards
    pairs = set(zip(path, path[1:]))
    if (edge["source"], edge["target"]) in pairs and edge.get("condition") is None:
        return True
    return chain.tail_edge is not None and edge["edge_id"] == chain.tail_edge["edge_id"]


def rebuild(edges: list[dict], chain: Chain, removed: list[str], new_keys: list[str]) -> list[dict]:
    """The edges of the final chain, in one step: the WP card, the chain cards that stay, then ``new_keys`` (client
    keys, plan order), then the outside next node.

    The edges of ``removed`` cards go; consecutive nodes of the final path are joined with one unconditioned edge
    (an existing one is kept); the tail's edge to the outside next node keeps its id and leaves the path's last node.
    So a cleanup that also adds never chains a new card after a removed tail, and every edge ends on a node that
    stays. Any other edge is kept exactly as it is."""
    gone = set(removed)
    path = [k for k in [chain.wp_key] + chain.cards if k not in gone] + list(new_keys)
    tail_id = chain.tail_edge["edge_id"] if chain.tail_edge is not None else None
    result: list[dict] = []
    for e in edges:
        if tail_id is not None and e["edge_id"] == tail_id:
            result.append(dict(e, source=path[-1]))      # to the outside next node, from the last node
        elif e["source"] not in gone and e["target"] not in gone:
            result.append(dict(e))
    present = {(e["source"], e["target"]) for e in result}
    for i, (a, b) in enumerate(zip(path, path[1:])):
        if (a, b) not in present:
            result.append({"edge_id": None, "client_key": f"tmp_wpd_e{i}", "source": a, "target": b,
                           "condition": None})
    return result


def successor(chain: Chain, key: str, removing: set[str]) -> Optional[str]:
    """The node the chain card ``key`` will be joined to once the cards in ``removing`` are gone."""
    path = chain.cards + ([chain.outside_next] if chain.outside_next else [])
    try:
        i = path.index(key)
    except ValueError:
        return None
    for nxt in path[i + 1:]:
        if nxt not in removing:
            return nxt
    return None
