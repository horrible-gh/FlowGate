"""0428 T#1: shared read-only slot completion contract.

SQL projects a decision only after matching its complete validity key. Validate
that key here too so snapshots/mocks cannot complete a slot using a stale id.
This module has no approval, test-result, source-apply or resume side effects.
"""
from __future__ import annotations


def completion_reason(item: dict) -> str:
    if item.get("result_doc_id") is None:
        return "pending"
    if item.get("result_doc_review_status") == "approved":
        return "approved"
    if item.get("type") == "M":
        return "auto_completed"
    if (
        item.get("type") in {"TSR", "TR2"}
        and item.get("completion_decision_id")
        and item.get("decision_kind") == "proceeded_unapproved"
        and item.get("decision_invalidated_at") is None
        and all(
            item.get(current) is not None
            and item.get(current) == item.get(decided)
            for current, decided in (
                ("sequence_id", "decision_sequence_id"),
                ("id", "decision_slot_id"),
                ("slot_generation", "decision_slot_generation"),
                ("result_doc_id", "decision_result_doc_id"),
                ("result_revision_no", "decision_document_revision"),
            )
        )
    ):
        return "proceeded_unapproved"
    return "review_pending"


def slot_completed(item: dict) -> bool:
    return completion_reason(item) in {"approved", "auto_completed", "proceeded_unapproved"}


def slot_status(item: dict) -> str:
    if item.get("result_doc_id") is None:
        return "pending"
    return "done" if slot_completed(item) else "in_progress"


def completion_fields(item: dict) -> dict:
    reason = completion_reason(item)
    valid = reason == "proceeded_unapproved"
    return {
        "slot_completed": slot_completed(item),
        "completion_reason": reason,
        "slot_generation": item.get("slot_generation", 0),
        "result_doc_id": item.get("result_doc_id"),
        "result_doc_review_status": item.get("result_doc_review_status"),
        "result_revision_no": item.get("result_revision_no"),
        "completion_decision_id": item.get("completion_decision_id") if valid else None,
        "decision_actor_user_id": item.get("decision_actor_user_id") if valid else None,
        "decision_at": item.get("decision_at") if valid else None,
    }
