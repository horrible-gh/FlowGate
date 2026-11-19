"""The one place that tells a card's 검증 limits — flowgate.v02test.0053 0003-T (1-A).

``resolve_verify_limits(flow_id, node_key) -> VerifyLimits(max_runs, run_timeout_sec)``: the card's own value, else the
system default (``legacy_adapter.verify_defaults`` -> ``modules.flow_gate.settings.verify_policy``: 3 runs, 180 seconds
unless a person changed them in the system settings). Cards 1-C (count / time applied to the self-check) and 1-D (the card document's 검증 line) must
read the limits through this function and nowhere else.

- The card's values are its run settings ``verify_max_runs`` (a whole number of 1 or more) and ``verify_run_minutes``
  (1..30), stored by ``flowgate.card_settings`` like every card setting. A value that is absent, ``null`` or not
  valid (a hand-edited row) is "not set" and the system default stands.
- Only a run card (research / work / design / custom: ``fields.RUN``) has them. A test card (TS), a gate, a requirement
  card and a plan card are never read for them: the system default is answered whatever a row says (the TS path,
  ``test_run_service``, is not a self-check path and takes no card limit).
- One verification never runs longer than ``RUN_TIMEOUT_CEILING_SEC`` (1800 seconds, the self-check run's own ceiling,
  ``tr_self_check_service``): a stored value above it is cut to it.
- ``flow_id`` is the Flow's (group's) id: card settings are kept per Flow and node key, the way
  ``card_settings.repository`` keeps them. A card the Flow does not have gets the system default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flowgate.card_settings import fields, repository
from flowgate.card_settings import legacy_adapter as legacy

RUN_TIMEOUT_CEILING_SEC = 1800


@dataclass(frozen=True)
class VerifyLimits:
    max_runs: int
    run_timeout_sec: int


def _whole(value: object, low: int, high: Optional[int] = None) -> Optional[int]:
    """``value`` when it is a whole number of ``low`` or more (and ``high`` or less when there is a ``high``), else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
        return None
    return value


def system_limits() -> VerifyLimits:
    """The system defaults alone (what a card with no value of its own has)."""
    runs, timeout_sec = legacy.verify_defaults()
    return VerifyLimits(max_runs=runs, run_timeout_sec=min(timeout_sec, RUN_TIMEOUT_CEILING_SEC))


def resolve_verify_limits(flow_id: str, node_key: str) -> VerifyLimits:
    """The card's ``(max_runs, run_timeout_sec)``: its own values, else the system defaults (see the module head)."""
    default = system_limits()
    node = repository.card_node(flow_id, node_key)
    if node is None or fields.card_kind(node.get("node_type"), node.get("doc_type")) != fields.RUN:
        return default
    stored = repository.values(flow_id, node_key)
    runs = _whole(stored.get(fields.VERIFY_MAX_RUNS.name), fields.VERIFY_RUNS_MIN)
    minutes = _whole(stored.get(fields.VERIFY_RUN_MINUTES.name), fields.VERIFY_MINUTES_MIN, fields.VERIFY_MINUTES_MAX)
    return VerifyLimits(
        max_runs=default.max_runs if runs is None else runs,
        run_timeout_sec=default.run_timeout_sec if minutes is None else min(minutes * 60, RUN_TIMEOUT_CEILING_SEC))
