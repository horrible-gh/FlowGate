"""Run API request checks (P0008 §7 S5-2 / S5-4 / S5-5 / S6-1, L0009 §1.4) — pure, no I/O.

Each returns ``(value, errors)``; ``errors`` uses the P0008 §1.3 ``{"field", "reason"}`` shape.
A missing or empty body is allowed where every field is optional.
"""
from __future__ import annotations

from typing import Any, Optional

RUN_REASON_MAX_LEN = 500
RUN_LIST_LIMIT_MIN = 1
RUN_LIST_LIMIT_MAX = 100
GATE_DECISIONS = ("confirm", "reject")
RUN_MODES = ("step", "full")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _body(body: Any) -> tuple[Optional[dict], list[dict]]:
    if body is None:
        return {}, []
    if not isinstance(body, dict):
        return None, [{"field": "body", "reason": "not_an_object"}]
    return body, []


def parse_start(body: Any) -> tuple[Optional[int], list[dict]]:
    """``{"revision_no": n | null}``; absent means the latest revision."""
    raw, errors = _body(body)
    if raw is None:
        return None, errors
    value = raw.get("revision_no")
    if value is not None and (not _is_int(value) or value < 1):
        return None, [{"field": "revision_no", "reason": "not_positive_integer_or_null"}]
    return value, []


def parse_mode(body: Any) -> tuple[Optional[str], list[dict]]:
    """flowgate.v02.0061 T#2: ``{"mode": "step" | "full" | null}`` of a run command (start / resume); absent
    means the run's own command (a new run: ``full``)."""
    raw, errors = _body(body)
    if raw is None:
        return None, errors
    value = raw.get("mode")
    if value is not None and value not in RUN_MODES:
        return None, [{"field": "mode", "reason": "not_one_of:" + ",".join(RUN_MODES)}]
    return value, []


def parse_reason(body: Any) -> tuple[Optional[str], list[dict]]:
    """``{"reason": "…"}`` optional, at most ``RUN_REASON_MAX_LEN`` characters."""
    raw, errors = _body(body)
    if raw is None:
        return None, errors
    value = raw.get("reason")
    if value is None:
        return None, []
    if not isinstance(value, str):
        return None, [{"field": "reason", "reason": "not_a_string"}]
    if len(value) > RUN_REASON_MAX_LEN:
        return None, [{"field": "reason", "reason": "too_long"}]
    return value, []


def parse_gate_decision(body: Any) -> tuple[tuple[Optional[str], Optional[str]], list[dict]]:
    """``{"decision": "confirm" | "reject", "comment": "…"}`` (P0008 S6-1). ``comment`` is optional
    and has the stop / resume ``reason`` limit (``RUN_REASON_MAX_LEN``)."""
    raw, errors = _body(body)
    if raw is None:
        return (None, None), errors
    decision = raw.get("decision")
    if decision is None:
        errors.append({"field": "decision", "reason": "required"})
    elif decision not in GATE_DECISIONS:
        errors.append({"field": "decision", "reason": "not_one_of:" + ",".join(GATE_DECISIONS)})
    comment = raw.get("comment")
    if comment is not None and not isinstance(comment, str):
        errors.append({"field": "comment", "reason": "not_a_string"})
    elif comment is not None and len(comment) > RUN_REASON_MAX_LEN:
        errors.append({"field": "comment", "reason": "too_long"})
    if errors:
        return (None, None), errors
    return (decision, comment), []


def parse_page(limit: Optional[str], offset: Optional[str]) -> tuple[tuple[int, int], list[dict]]:
    """``limit`` 1~100 (default 20), ``offset`` ≥ 0 (default 0)."""
    errors: list[dict] = []
    values = []
    for name, raw, default, low, high in (("limit", limit, 20, RUN_LIST_LIMIT_MIN, RUN_LIST_LIMIT_MAX),
                                          ("offset", offset, 0, 0, None)):
        if raw is None:
            values.append(default)
            continue
        text = str(raw).strip()
        if not (text.isascii() and text.isdigit()) or int(text) < low or (high is not None and int(text) > high):
            errors.append({"field": name, "reason": f"out_of_range:{low}-{high}" if high else "negative_or_not_integer"})
            values.append(default)
            continue
        values.append(int(text))
    return (values[0], values[1]), errors
