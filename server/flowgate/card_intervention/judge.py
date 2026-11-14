"""The ``card_settings_ports.intervention`` handler (flowgate.v02.0119 T#3, T-2).

The Graph's node judgement (``node_approval.intervention``) has already decided the card is a general card in review
with a result that no AI works on and no integration waits for — or (0119 T-2 rework) a card open in its instruction
stage whose instruction no AI works on; this answers which of the two states it is in, from the latest reason recorded
for the document of its current stage in its current attempt (``legacy_adapter.approval_wait_reason``: the instruction
in the instruction stage, else the result; the B-1 run events, read as they are stored).
"""
from __future__ import annotations

from flowgate.card_intervention import reasons


def intervention(flow_id: str, row: dict) -> dict:
    """``{"kind": needs_intervention | system_error, "reason": <recorded reason> | completion_missing}``.

    flowgate.v02gui.0015 T3: a card whose AI's answers require a new attempt nobody opened (``action_guard``) is
    ``needs_intervention`` with ``action_required:<requirement>`` first — its answers' step is held, whatever was recorded."""
    from flowgate.card_settings import action_guard  # noqa: PLC0415
    from flowgate.card_settings import legacy_adapter  # noqa: PLC0415 — the B-1 reader of the card stage

    held = action_guard.node_reason(flow_id, row)
    if held is not None:
        return {"kind": reasons.NEEDS_INTERVENTION, "reason": held}
    return reasons.state(legacy_adapter.approval_wait_reason(flow_id, row))
