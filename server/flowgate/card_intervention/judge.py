"""The ``card_settings_ports.intervention`` handler.

The Graph's node judgement (``node_approval.intervention``) has already decided the card is a general card in review
with a result that no AI works on and no integration waits for — or (0119 T-2 rework) a card open in its instruction
stage whose instruction no AI works on; this answers which of the two states it is in, from the latest reason recorded
for the document of its current stage in its current attempt (``legacy_adapter.approval_wait_reason``: the instruction
in the instruction stage, else the result; the B-1 run events, read as they are stored).
"""
from __future__ import annotations

import logging

from flowgate.card_intervention import reasons

_log = logging.getLogger(__name__)


def intervention(flow_id: str, row: dict) -> dict:
    """``{"kind": needs_intervention | system_error, "reason": <recorded reason> | completion_missing}``.

    Current-stage/current-attempt loop questions win first (``question_pending``);
    then a card whose AI's answers require a new attempt nobody opened (``action_guard``) is
    ``needs_intervention`` with ``action_required:<requirement>`` first — its answers' step is held, whatever was recorded."""
    from flowgate.card_settings import action_guard  # noqa: PLC0415
    from flowgate.card_settings import legacy_adapter  # noqa: PLC0415 — the B-1 reader of the card stage

    if question_pending(flow_id, row):
        return {"kind": "question_pending", "reason": "question_pending"}
    held = action_guard.node_reason(flow_id, row)
    if held is not None:
        return {"kind": reasons.NEEDS_INTERVENTION, "reason": held}
    recorded = legacy_adapter.approval_wait_reason(flow_id, row)
    found = reasons.state(recorded)
    detail = _loop_stop_detail(row, recorded)
    if detail:
        found[reasons.STOP_DETAIL] = detail     # F98-4: the closed loop's failure cause, an added field
    return found


def question_pending(flow_id: str, row: dict) -> bool:
    """Only confirmed unanswered questions of the latest loop in this attempt/stage.

    Include stopped loops: their verdict consumption waits for answers too. Unknown
    identity or failed reads do not hide an existing failure. This is read-only.
    """
    from flowgate.card_settings import legacy_adapter as legacy, question_wait

    if row.get("status") not in ("open", "review") or not row.get("run_id") or not row.get("node_key"):
        return False
    try:
        attempt = (legacy.run_store.active_attempt_id(row["run_id"], row["node_key"])
                   or legacy.run_store.current_attempts(row["run_id"]).get(row["node_key"]))
        if not attempt:
            return False
        doc = legacy.stage_document(row)
        loops = legacy.review_loops(doc) if doc else []
        loop = loops[0] if loops else None
        if (not loop or loop.get("group_id") != flow_id or not loop.get("run_id")
                or legacy._loop_attempt(loop["run_id"]) != attempt):
            return False
        return bool(question_wait.open_questions(flow_id, loop["run_id"]))
    except Exception:
        _log.warning("card loop question read failed node=%s", row.get("node_key"), exc_info=True)
        return False


def _loop_stop_detail(row: dict, recorded) -> "str | None":
    """F98-4: the ``stop_detail`` (failure cause, e.g. ``model_not_found``) of the closed review loop that left the card
    in intervention, or None. Only for ``review_not_passed:<stop reason>``, and only from the latest loop of the stage
    document when it is stopped with that very stop reason (an older loop's cause is never shown for a newer stop).
    A failed read tells none: the intervention itself is still answered."""
    stop = reasons.loop_stop_reason(recorded)
    if stop is None:
        return None
    from flowgate.card_settings import legacy_adapter  # noqa: PLC0415

    try:
        doc_id = legacy_adapter.stage_document(row)
        loops = legacy_adapter.review_loops(doc_id) if doc_id else []
    except Exception:  # noqa: BLE001 — the intervention is answered without its cause
        _log.warning("card intervention stop detail read failed node=%s", row.get("node_key"), exc_info=True)
        return None
    latest = next((loop for loop in loops if loop.get("current_stage") == "stopped"), None)
    if latest is None or latest.get("stop_reason") != stop:
        return None
    return reasons.stop_detail_text(latest.get("stop_detail"))
