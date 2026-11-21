"""Review verdicts a document review loop leaves out of its review_count (A2, migration 201).

A-3: "waiting time and rejections caused by questions do not count toward the review budget". A row here is the explicit cause
marker of one review verdict (``review_id``): it was caused by a question wait (``wait_id``, ``question_loop_waits``)
and is therefore not counted against the loop's review_count. The review row itself is never changed or deleted -- it
stays the verdict of its round for the rejection, the rework, the stall check and the history; the exclusion is read
by the count only (``review._document_loop_counted_rounds``). Insert-only and idempotent on ``review_id``.
"""
from __future__ import annotations

import json
import secrets
from typing import Iterable, Optional

from .connection import get_store, now_iso

TABLE = "review_count_exclusions"
CAUSE_QUESTION_WAIT = "question_wait"
BASIS_ASKED_WITH_VERDICT = "asked_with_verdict"      # the reviewer asked and rejected in the hop that parked
BASIS_ANSWER_CONTRADICTED = "answer_contradicted"    # the first review after a rework that submitted while asking
BASES = (BASIS_ASKED_WITH_VERDICT, BASIS_ANSWER_CONTRADICTED)

_COLUMNS = (
    "exclusion_id", "review_id", "doc_ref", "review_run_id", "revision_no", "cause", "wait_id", "basis",
    "question_item_ids", "created_at",
)


def available() -> bool:
    return bool(get_store().table_exists(TABLE))


def _decode(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    row = dict(row)
    try:
        row["question_item_ids"] = [int(i) for i in json.loads(row.get("question_item_ids") or "[]")]
    except (TypeError, ValueError):
        row["question_item_ids"] = []
    return row


def get_by_review(review_id: int) -> Optional[dict]:
    return _decode(get_store()._fetch_one("SELECT * FROM review_count_exclusions WHERE review_id = ?",
                                          [int(review_id)]))


def insert(*, review_id: int, doc_ref: str, wait_id: str, basis: str, question_item_ids: Iterable[int],
           review_run_id: Optional[str] = None, revision_no: Optional[int] = None) -> dict:
    """Record that ``review_id`` was caused by the question wait ``wait_id``; the stored row is returned.

    A review that already has its exclusion keeps the first one (one cause per review): the call returns it and
    writes nothing."""
    if basis not in BASES:
        raise ValueError("unknown review count exclusion basis: " + str(basis))
    if not doc_ref or not wait_id:
        raise ValueError("review count exclusion needs doc_ref and wait_id")
    existing = get_by_review(review_id)
    if existing is not None:
        return existing
    values = {
        "exclusion_id": "rce_" + secrets.token_hex(12), "review_id": int(review_id), "doc_ref": doc_ref,
        "review_run_id": review_run_id, "revision_no": None if revision_no is None else int(revision_no),
        "cause": CAUSE_QUESTION_WAIT, "wait_id": wait_id, "basis": basis,
        "question_item_ids": json.dumps(sorted({int(i) for i in question_item_ids})), "created_at": now_iso(),
    }
    get_store()._execute(
        "INSERT INTO review_count_exclusions (" + ",".join(_COLUMNS) + ") VALUES ("
        + ",".join("?" for _ in _COLUMNS) + ")", [values[c] for c in _COLUMNS])
    return get_by_review(review_id)


def list_by_doc(doc_ref: str) -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM review_count_exclusions WHERE doc_ref = ? ORDER BY review_id", [doc_ref]) or []
    return [_decode(r) for r in rows]


def review_ids_by_doc(doc_ref: Optional[str]) -> set[int]:
    """The ids of ``doc_ref``'s reviews a loop does not count (raises on a read failure; the caller decides)."""
    if not doc_ref:
        return set()
    return {int(r["review_id"]) for r in list_by_doc(doc_ref)}
