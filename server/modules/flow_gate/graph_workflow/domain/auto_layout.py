"""Auto-placed coordinates for nodes without a saved position (L0009 §1.3 / §2.7).

Deterministic: the same input always yields the same coordinates. Auto coordinates are never
stored (P0008 §3); a saved coordinate always wins. The Graph projection's layered placement
belongs to S3 and is not implemented here; unknown projections fall back to one row ordered
by node key (L0009 §2.7 ``else``).
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

AUTO_X_STEP = 260
AUTO_STACK_DY = 140
FREE_DOC_ROW_Y_SEQUENCE = 180
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


def layout(
    projection: str,
    *,
    chain: Sequence[str] = (),
    free_docs: Sequence[str] = (),
    others: Sequence[str] = (),
    saved: Optional[Mapping[str, tuple[float, float]]] = None,
) -> dict[str, dict]:
    """Return ``{node_key: {"x", "y", "source"}}`` for every given key.

    - ``documents`` (unassigned): ``free_docs`` in document number order, one row at y=0.
    - ``sequence`` (v0.1): ``chain`` = root + slots in sort order, each next to the previous
      node's effective position; ``free_docs`` in document order on y=180 from x=260.
    - anything else: ``others`` ordered by node key on one row.
    """
    saved = saved or {}
    every = [*chain, *free_docs, *others]
    placer = _Placer(every, saved)
    if projection == "documents":
        for i, key in enumerate(free_docs):
            placer.auto(key, AUTO_X_STEP * i, 0)
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
    else:
        for i, key in enumerate(sorted(every)):
            placer.auto(key, AUTO_X_STEP * i, 0)
    return placer.placed
