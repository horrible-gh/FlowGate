"""Shape check of ``PUT /flows/{id}/canvas/layout`` (P0008 §4 S2-3, L0009 §1.4 / E-14·E-15)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

LAYOUT_POSITIONS_MAX = 500
# P0008 §1.6 / L0009 §1.4: a node key is at most 120 characters (E-15 ``too_long``). The stored
# column is wider (DB0010 DB-D06, 200); the public contract, not the column, is the limit.
NODE_KEY_MAX_LEN = 120


@dataclass
class LayoutRequest:
    positions: list[dict] = field(default_factory=list)   # [{node_key, x, y}]
    viewport: Optional[dict] = None                         # {x, y, zoom} or None = unchanged
    errors: list[dict] = field(default_factory=list)        # [{field, reason}]

    @property
    def only_empty(self) -> bool:
        return [e["reason"] for e in self.errors] == ["empty_with_null_viewport"]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_layout(body: Any) -> LayoutRequest:
    req = LayoutRequest()
    if not isinstance(body, dict):
        req.errors.append({"field": "body", "reason": "not_an_object"})
        return req
    raw_positions = body.get("positions", [])
    if raw_positions is None:
        raw_positions = []
    if not isinstance(raw_positions, list):
        req.errors.append({"field": "positions", "reason": "not_a_list"})
        raw_positions = []
    elif len(raw_positions) > LAYOUT_POSITIONS_MAX:
        req.errors.append({"field": "positions", "reason": "too_many_items"})
        raw_positions = []
    for i, item in enumerate(raw_positions):
        where = f"positions[{i}]"
        if not isinstance(item, dict):
            req.errors.append({"field": where, "reason": "not_an_object"})
            continue
        key = item.get("node_key")
        ok = True
        if not isinstance(key, str) or not key:
            req.errors.append({"field": f"{where}.node_key", "reason": "required"})
            ok = False
        elif len(key) > NODE_KEY_MAX_LEN:
            req.errors.append({"field": f"{where}.node_key", "reason": "too_long"})
            ok = False
        coords = {}
        for axis in ("x", "y"):
            coords[axis] = _number(item.get(axis))
            if coords[axis] is None:
                req.errors.append({"field": f"{where}.{axis}", "reason": "not_a_number"})
                ok = False
        if ok:
            req.positions.append({"node_key": key, "x": coords["x"], "y": coords["y"]})
    raw_viewport = body.get("viewport")
    if raw_viewport is not None:
        if not isinstance(raw_viewport, dict):
            req.errors.append({"field": "viewport", "reason": "not_an_object"})
        else:
            values = {axis: _number(raw_viewport.get(axis)) for axis in ("x", "y", "zoom")}
            for axis, value in values.items():
                if value is None:
                    req.errors.append({"field": f"viewport.{axis}", "reason": "not_a_number"})
            if values["zoom"] is not None and values["zoom"] <= 0:
                req.errors.append({"field": "viewport.zoom", "reason": "not_positive"})
            if all(v is not None for v in values.values()) and values["zoom"] > 0:
                req.viewport = values
    if not req.errors and not req.positions and req.viewport is None:
        req.errors.append({"field": "positions", "reason": "empty_with_null_viewport"})
    return req
