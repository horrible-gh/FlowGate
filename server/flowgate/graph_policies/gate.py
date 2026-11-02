"""Gate judgement = a person's confirm or reject (flowgate.v02.0085 T#2, D0007 §3.1 / §7.4 ①).

The product rule of the ``gate_node`` slot is ``human_confirm_v1``: a confirm card is decided by a person only.
``confirm`` is ``confirmed`` and ``reject`` is ``rejected`` (the node events ``gate_confirmed`` / ``gate_rejected``);
nothing else is a decision. A run started before this rule was registered keeps its stored ``undecided`` snapshot
(registration never changes a stored snapshot); such a gate is still refused with ``run_policy_undecided`` until its
run adopts a new revision through the existing checks.

What a decision does besides the node event is the slot's ``intake`` (``flowgate.gate_decisions.intake``): the
decision intent is stored with its request key, a reject ends the gate's attempt at once, and a confirm whose Git
apply or archive is needed is only accepted (a ``gate_publish`` job) - the gate is done once that work succeeded, never
before (D0007 §3.3). The engine asks a run's own snapshot for it, so only a run that took this rule takes this intake.
Pure judgement otherwise; the validator side has nothing to refuse (a gate is an allowed structure).

flowgate.v02.0119 T#4 (T-3, 0004-CH seq 9 D-2 / T-3): a confirm card has no rejection any more — the Time Machine and a
memo stand in for it. ``judge`` refuses ``reject`` (``GateDecisionRemoved``, the intake's 409 ``gate_reject_removed``), so
no new ``gate_rejected`` node event is ever made; a gate's existing ``rejected`` decisions and events stay as history,
read and shown only (``gate_decisions.history``), never converted.
"""
from __future__ import annotations

HUMAN_CONFIRM = "human_confirm_v1"
IMPL_VERSION = "1"
OUTCOMES = {"confirm": "confirmed"}
REMOVED_DECISIONS = ("reject",)                 # flowgate.v02.0119 T#4 (T-3): no new gate rejection


class GateDecisionRemoved(ValueError):
    """A decision the product rule no longer takes (``reject``): refused before anything is written."""

    code = "gate_reject_removed"

    def __init__(self, decision: str):
        super().__init__(f"gate decision removed: {decision!r}")
        self.decision = decision


class HumanConfirm:
    key = HUMAN_CONFIRM

    def judge(self, _node_key, decision, _comment) -> str:
        if decision in REMOVED_DECISIONS:
            raise GateDecisionRemoved(decision)
        outcome = OUTCOMES.get(decision)
        if outcome is None:
            raise ValueError(f"not a gate decision: {decision!r}")
        return outcome

    def validate(self, _graph, _occurrence) -> list[dict]:
        return []

    @property
    def intake(self):
        """The decision intake of this rule (D0007 §7.2): ``decide`` / ``run_wire``. Imported on use only, so the
        policy registration at startup loads no Git or settings code."""
        from flowgate.gate_decisions import intake
        return intake


HUMAN_CONFIRM_V1 = HumanConfirm()
SLOTS = (("gate_node", HUMAN_CONFIRM, IMPL_VERSION, HUMAN_CONFIRM_V1),)
