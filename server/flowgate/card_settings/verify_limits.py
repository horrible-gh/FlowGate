"""The one place that tells a card's verification limits — the earlier change the earlier implementation (1-A).

``resolve_verify_limits(flow_id, node_key) -> VerifyLimits(max_runs, run_timeout_sec)``: the card's own value, else the
fixed card default (``fields.DEFAULT_VERIFY_MAX_RUNS`` / ``DEFAULT_VERIFY_RUN_MINUTES``: 10 runs, 1 minute). There is no
system setting any more (verification is managed per card only; a value an older
server stored in the system settings is left in place and no longer read). Cards 1-C (count / time applied to the
self-check) and 1-D (the card document's verification line) must read the limits through this function and nowhere else.

- The card's values are its run settings ``verify_max_runs`` (a whole number of 1 or more) and ``verify_run_minutes``
  (1..30), stored by ``flowgate.card_settings`` like every card setting. A value that is absent, ``null`` or not
  valid (a hand-edited row) is "not set" and the card default stands.
- Only a run card (research / work / design / custom: ``fields.RUN``) has them. A test card (TS), a gate, a requirement
  card and a plan card are never read for them: the card default is answered whatever a row says (the TS path,
  ``test_run_service``, is not a self-check path and takes no card limit).
- One verification never runs longer than ``RUN_TIMEOUT_CEILING_SEC`` (1800 seconds, the self-check run's own ceiling,
  ``tr_self_check_service``): a stored value above it is cut to it.
- ``flow_id`` is the Flow's (group's) id: card settings are kept per Flow and node key, the way
  ``card_settings.repository`` keeps them. A card the Flow does not have gets the card default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flowgate.card_settings import fields, repository

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


def default_limits() -> VerifyLimits:
    """The fixed card defaults alone (what a card with no value of its own has): 10 runs, 1 minute."""
    return VerifyLimits(max_runs=fields.DEFAULT_VERIFY_MAX_RUNS,
                        run_timeout_sec=min(fields.DEFAULT_VERIFY_RUN_MINUTES * 60, RUN_TIMEOUT_CEILING_SEC))


def resolve_verify_limits(flow_id: str, node_key: str) -> VerifyLimits:
    """The card's ``(max_runs, run_timeout_sec)``: its own values, else the card defaults (see the module head)."""
    default = default_limits()
    node = repository.card_node(flow_id, node_key)
    if node is None or fields.card_kind(node.get("node_type"), node.get("doc_type")) != fields.RUN:
        return default
    stored = repository.values(flow_id, node_key)
    runs = _whole(stored.get(fields.VERIFY_MAX_RUNS.name), fields.VERIFY_RUNS_MIN)
    minutes = _whole(stored.get(fields.VERIFY_RUN_MINUTES.name), fields.VERIFY_MINUTES_MIN, fields.VERIFY_MINUTES_MAX)
    return VerifyLimits(
        max_runs=default.max_runs if runs is None else runs,
        run_timeout_sec=default.run_timeout_sec if minutes is None else min(minutes * 60, RUN_TIMEOUT_CEILING_SEC))
