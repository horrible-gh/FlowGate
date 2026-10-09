"""Auto-placed coordinates for nodes without a saved position (L0009 §1.3 / §2.7).

Deterministic: the same input always yields the same coordinates. Auto coordinates are never
stored (P0008 §3); a saved coordinate always wins. Graph / draft nodes are placed in layers by
depth from the start nodes (``layered``); unknown projections fall back to one row ordered by
node key (L0009 §2.7 ``else``).
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

AUTO_X_STEP = 260
AUTO_STACK_DY = 140
FREE_DOC_ROW_Y_SEQUENCE = 180
DRAFT_ROW_Y_DOCUMENTS = 320
FREE_DOC_ROW_GAP_GRAPH = 200
COLLISION_BOX_W = 200
COLLISION_BOX_H = 100
COLLISION_DY = 120
COLLISION_MAX_TRIES = 20


class _Placer:
    def __init__(self, keys: Iterable[str], saved: Mapping[str, tuple[float, float]]):
        self.placed: dict[str, dict] = {}
        for key in keys:
            if key in saved:
                x, y = saved[key]
                self.placed[key] = {"x": x, "y": y, "source": "saved"}

    def auto(self, key: str, x: float, y: float) -> None:
        if key in self.placed:
            return
        tries = 0
        while tries < COLLISION_MAX_TRIES and self._collides(x, y):
            y += COLLISION_DY
            tries += 1
        self.placed[key] = {"x": x, "y": y, "source": "auto"}

    def _collides(self, x: float, y: float) -> bool:
        return any(
            abs(p["x"] - x) < COLLISION_BOX_W and abs(p["y"] - y) < COLLISION_BOX_H
            for p in self.placed.values()
        )


def layered_depths(keys: Sequence[str], edges: Iterable[tuple[str, str]]) -> dict[str, int]:
    """L0009 §2.7 ``layered`` depth: 0 for start nodes, else 1 + max depth of predecessors.

    The acyclic part is layered by longest path (topological order). A node inside a cycle keeps
    the depth of its first visit from an already layered predecessor (breadth-first, key order),
    so a cycle never loops; one no layered node reaches takes depth 0.
    """
    key_set = set(keys)
    succ: dict[str, list[str]] = {k: [] for k in keys}
    indeg = {k: 0 for k in keys}
    for s, t in edges:
        if s in key_set and t in key_set:
            succ[s].append(t)
            indeg[t] += 1
    depth: dict[str, int] = {}
    remaining = dict(indeg)
    ready = sorted(k for k in keys if indeg[k] == 0)
    for k in ready:
        depth[k] = 0
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in succ[cur]:
            depth[nxt] = max(depth.get(nxt, 0), depth[cur] + 1)
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    done = set(order)
    queue = sorted(done)
    i = 0
    while i < len(queue):
        cur = queue[i]
        i += 1
        for nxt in sorted(succ[cur]):
            if nxt not in done:
                done.add(nxt)
                depth[nxt] = depth[cur] + 1
                queue.append(nxt)
    for k in sorted(keys):
        if k not in done:
            depth[k] = 0
            done.add(k)
            queue = [k]
            while queue:
                cur = queue.pop(0)
                for nxt in sorted(succ[cur]):
                    if nxt not in done:
                        done.add(nxt)
                        depth[nxt] = depth[cur] + 1
                        queue.append(nxt)
    return depth


def _layered(placer: "_Placer", keys: Sequence[str], edges, origin_x: float, origin_y: float) -> None:
    depth = layered_depths(keys, edges)
    by_depth: dict[int, list[str]] = {}
    for k in keys:
        by_depth.setdefault(depth[k], []).append(k)
    for d in sorted(by_depth):
        for j, key in enumerate(sorted(by_depth[d])):
            placer.auto(key, origin_x + AUTO_X_STEP * d, origin_y + AUTO_STACK_DY * j)


def layout(
    projection: str,
    *,
    chain: Sequence[str] = (),
    free_docs: Sequence[str] = (),
    others: Sequence[str] = (),
    graph_nodes: Sequence[str] = (),
    graph_edges: Iterable[tuple[str, str]] = (),
    saved: Optional[Mapping[str, tuple[float, float]]] = None,
) -> dict[str, dict]:
    """Return ``{node_key: {"x", "y", "source"}}`` for every given key.

    - ``documents`` (unassigned): ``free_docs`` in document number order, one row at y=0;
      draft nodes (``graph_nodes``) in layers from (0, 320).
    - ``sequence`` (v0.1): ``chain`` = root + slots in sort order, each next to the previous
      node's effective position; ``free_docs`` in document order on y=180 from x=260.
    - ``graph``: ``graph_nodes`` in layers from (0, 0); ``free_docs`` on a row 200 below the
      lowest Graph node.
    - anything else: ``others`` ordered by node key on one row.
    """
    saved = saved or {}
    graph_edges = list(graph_edges)
    every = [*chain, *free_docs, *graph_nodes, *others]
    placer = _Placer(every, saved)
    if projection == "documents":
        for i, key in enumerate(free_docs):
            placer.auto(key, AUTO_X_STEP * i, 0)
        _layered(placer, graph_nodes, graph_edges, 0, DRAFT_ROW_Y_DOCUMENTS)
    elif projection == "sequence":
        prev: Optional[str] = None
        for key in chain:
            if prev is None:
                placer.auto(key, 0, 0)
            else:
                base = placer.placed[prev]
                placer.auto(key, base["x"] + AUTO_X_STEP, base["y"])
            prev = key
        for k, key in enumerate(free_docs, start=1):
            placer.auto(key, AUTO_X_STEP * k, FREE_DOC_ROW_Y_SEQUENCE)
    elif projection == "graph":
        _layered(placer, graph_nodes, graph_edges, 0, 0)
        bottom = max((placer.placed[k]["y"] for k in graph_nodes), default=0)
        for i, key in enumerate(free_docs):
            placer.auto(key, AUTO_X_STEP * i, bottom + FREE_DOC_ROW_GAP_GRAPH)
    else:
        for i, key in enumerate(sorted(every)):
            placer.auto(key, AUTO_X_STEP * i, 0)
    return placer.placed
