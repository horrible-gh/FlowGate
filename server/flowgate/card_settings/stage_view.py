"""The stage assignments a run read shows (flowgate.v02.0113 T#3, L-2 §2.12, P-1 §6). Reads only.

``wire`` adds three keys to a node run of the run payload (``run_service.node_run_wire``): ``wait_reason`` (L-2
``node_wait_reason``), ``stage_assignments`` (the attempt's assignments, ordered instruction → instruction review →
result → result review, ``seq`` ascending, the current one marked) and ``approval_wait_submission`` (the submission mark
of the B-1 row ``approval_wait_reason`` showed). No attempt / the feature off / a read error: null; a pre-feature attempt:
``[]`` and null. ``waits_on_person`` is the run's idle judgement of a node of an assigned attempt (L-2 §2.12 table).
Nothing here resolves a role, writes an assignment or restarts a wait. flowgate.v02.0113 T#3 rework: a pre-feature
attempt's instruction stage whose written instruction nothing judges any more waits for a person (``_unjudged_instruction``).
"""
from __future__ import annotations

import logging
from typing import Optional

from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import stage_assignment as sa

store = sa.store                                   # reads raise ``AssignmentUnreadable`` (L-2 §2.14)
UNREADABLE = "unreadable"                          # ``card_settings_ports.STAGE_UNREADABLE``

_log = logging.getLogger(__name__)

MANUAL_WRITE_WAIT, AI_SETUP_REQUIRED, AI_UNAVAILABLE = "manual_write_wait", "ai_setup_required", "ai_unavailable"
_ORDER = {sa.INSTRUCTION: 0, sa.INSTRUCTION_REVIEW: 1, sa.RESULT: 2, sa.RESULT_REVIEW: 3}
_NULL = {"wait_reason": None, "stage_assignments": None, "approval_wait_submission": None}


def _same(a: Optional[dict], b: Optional[dict]) -> bool:
    return bool(a) and bool(b) and (a["attempt_id"], a["stage"], a["seq"]) == (b["attempt_id"], b["stage"], b["seq"])


def assignment_wire(a: dict, current: bool) -> dict:
    plan = {"instruction_type": a.get("plan_instruction_type"), "result_type": a.get("plan_result_type")} \
        if a.get("plan_result_type") else None
    role_value = {"kind": a["role_value_kind"], "provider_id": a.get("role_value_provider_id")} \
        if a.get("role_value_kind") else None
    intent = {"kind": a["intent_kind"], "exec_id": a.get("intent_exec_id")} if a.get("intent_kind") else None
    return {"stage": a["stage"], "seq": int(a["seq"]), "current": current, "role": a["role"],
            "actor": a["actor"], "actor_reason": a.get("actor_reason"),
            "provider_id": a.get("provider_id"), "provider_name": a.get("provider_name"),
            "role_value": role_value, "source": a["source"], "cause": a.get("cause"), "plan": plan,
            "reused_doc_id": a.get("reused_doc_id"),
            "review_rounds": int(a["review_rounds"]) if a.get("review_rounds") is not None else None,
            "rework_provider_id": a.get("rework_provider_id"),
            "author": a.get("author"), "author_provider_id": a.get("author_provider_id"),
            "basis": sa.basis_of(a), "decided_at": a["decided_at"], "stage_state": a["stage_state"],
            "fail_cause": a.get("fail_cause"), "verdict": a.get("verdict"), "intent": intent}


def node_wait_reason(row: dict, a) -> Optional[dict]:
    """L-2 §2.12 for the current stage assignment ``a`` of the node run ``row``."""
    if row.get("status") not in ("open", "review") or not isinstance(a, dict):
        return None

    def reason(code: str, role: str, detail: Optional[str] = None) -> dict:
        return {"code": code, "role": role, "stage": a["stage"], "seq": int(a["seq"]), "detail": detail}

    if a["stage"] == sa.INSTRUCTION and a.get("reused_doc_id"):
        return None
    if a["stage"] in sa.REVIEW_STAGES and a.get("verdict") == sa.ISSUES:
        return reason(MANUAL_WRITE_WAIT, "execution" if a["stage"] == sa.RESULT_REVIEW else "instruction")
    if a["actor"] == sa.PERSON and a["stage_state"] == sa.WAITING:
        return reason(MANUAL_WRITE_WAIT, a["role"])
    if a["actor"] == sa.WAIT_SETUP and a["stage_state"] == sa.WAITING:
        return reason(AI_SETUP_REQUIRED, a["role"], a.get("source"))
    if a["actor"] == sa.WAIT_UNAVAILABLE and a["stage_state"] == sa.WAITING:
        return reason(AI_UNAVAILABLE, a["role"], a.get("cause"))
    if a["stage_state"] == sa.FAILED and a.get("fail_cause") not in (sa.TOKEN_ISSUE_FAILED, sa.REVIEW_NOT_STARTED):
        return reason(AI_UNAVAILABLE, a["role"], a.get("fail_cause"))
    return None


def wire(flow_id: str, row: dict, attempt_id: Optional[str], approval_wait_reason: Optional[str] = None) -> dict:
    """The three keys of P-1 §6.1 for the node run ``row`` of ``attempt_id`` (``run_store.current_attempts``)."""
    try:
        if not sa.enabled() or not attempt_id:
            return dict(_NULL)
        rows = store.for_node(row["run_id"], row["node_key"], attempt_id)
        if not rows:
            return {"wait_reason": None, "stage_assignments": [], "approval_wait_submission": None}
        current = sa.current_stage_assignment(attempt_id, row)
        ordered = sorted(rows, key=lambda r: (_ORDER.get(r["stage"], 9), int(r["seq"])))
        shown = [assignment_wire(r, _same(r, current if isinstance(current, dict) else None)) for r in ordered]
        return {"wait_reason": node_wait_reason(row, current), "stage_assignments": shown,
                "approval_wait_submission": approval_wait_submission(row, attempt_id) if approval_wait_reason else None}
    except Exception:  # noqa: BLE001 — the run read goes on (the 0105 convention)
        _log.warning("stage assignments read failed flow=%s node=%s", flow_id, row.get("node_key"), exc_info=True)
        return dict(_NULL)


def approval_wait_submission(row: dict, attempt_id: Optional[str]) -> Optional[dict]:
    """The mark of the latest B-1 row of the node run's result — the very row ``approval_wait_reason`` reads."""
    doc_id = row.get("result_doc_id")
    if not doc_id:
        return None
    active = legacy.active_attempt_id(row["run_id"], row["node_key"]) or attempt_id
    found = [r for r in legacy.approval_wait_rows(row["run_id"], row["node_key"], active)
             if r.get("doc_id") == doc_id and isinstance(r.get("reason"), str) and r["reason"]]
    latest = found[-1] if found else None
    mark = (latest or {}).get("submission_mark")
    return dict(mark) if isinstance(mark, dict) else None


def _unjudged_instruction(flow_id: str, row: dict) -> bool:
    """flowgate.v02.0113 T#3 rework (0084: 0004-T ``pending_review`` with no review and no approval, the run idle
    ``no_targets``): a pre-feature attempt's node open in its instruction stage whose instruction is written and not
    approved while nothing works on it — no AI run of the node, no review of it, no run-end hand-off
    (``review_stage.ai_working``). Its run-end step is over or lost and the run read's reconciliation took nothing
    (``review_stage._reconcile_instruction``: a B-1 recorded, a run that did not end on its own, …): a person judges the
    instruction, so the run waits for a person, never "nothing to run". Reads only; a failed read: no."""
    if row.get("stage") != sa.INSTRUCTION or not row.get("instruction_doc_id"):
        return False
    try:
        from flowgate.card_approval import legacy_adapter as approvals  # noqa: PLC0415
        from flowgate.card_documents import lines as card_lines  # noqa: PLC0415
        from flowgate.card_settings import review_stage  # noqa: PLC0415 — review_stage imports this package

        doc = approvals.document(row["instruction_doc_id"])
        if doc is None or doc.get("doc_review_status") in review_stage.APPROVED or not card_lines.has_body(doc):
            return False
        return not review_stage.ai_working(flow_id, row["node_key"], row["instruction_doc_id"])
    except Exception:  # noqa: BLE001 — the run read goes on with the current judgement
        _log.warning("instruction wait read failed flow=%s node=%s", flow_id, row.get("node_key"), exc_info=True)
        return False


def waits_on_person(flow_id: str, row: dict):
    """L-2 §2.12 run idle judgement of a node of an assigned attempt; None: the current judgement (a pre-feature
    attempt, the feature off, not a role card). A read error is ``UNREADABLE`` — told apart from a plain no: no person's
    wait, and the node's error family (``settle._failed``, idle reason ``error``), the run read itself going on
    (L-2 §2.14, T §4.6)."""
    try:
        if not sa.enabled():
            return None
        attempt_id = legacy.active_attempt_id(row["run_id"], row["node_key"])
        a = sa.current_stage_assignment(attempt_id, row)
    except Exception:  # noqa: BLE001
        _log.warning("stage assignment wait read failed flow=%s node=%s", flow_id, row.get("node_key"), exc_info=True)
        return UNREADABLE
    if a == sa.LEGACY and row.get("status") == "open" and _unjudged_instruction(flow_id, row):
        return True
    if a == sa.LEGACY or a is None:
        return None
    if row.get("status") == "open":
        if a["stage"] == sa.INSTRUCTION and a.get("reused_doc_id"):
            return False
        if a["stage"] in sa.REVIEW_STAGES and a.get("verdict") == sa.ISSUES:
            return True
        if a["actor"] in (sa.PERSON, sa.WAIT_SETUP) and a["stage_state"] == sa.WAITING:
            return True
        return False
    if row.get("status") == "review":
        return True if a["actor"] in (sa.WAIT_SETUP, sa.WAIT_UNAVAILABLE) else None
    return None
