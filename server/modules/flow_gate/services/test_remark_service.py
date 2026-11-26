"""Remarks on scenario cases (F81): storage and the API shapes.

A remark belongs to one case of one TS. People and AIs write them into the same list; the
AI answers a remark in one line and resolves it, and anyone can open a resolved one again.
What a remark does to the card's stage (approved -> working) is not decided here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import test_case_remarks as db_remarks
from modules.flow_gate.services import test_spec_service


def _refuse(status_code: int, code: str, **payload) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": code, **payload})


def _ts_document(doc_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise _refuse(404, "doc_not_found", doc_id=doc_id)
    if str(doc.get("type_code") or "").upper() != "TS":
        raise _refuse(422, "not_ts", doc_id=doc_id)
    return doc


def _known_case_id(doc: dict, case_id: str) -> str:
    """The case id as the scenario writes it; 404 when the scenario has no such case."""
    from modules.flow_gate.services import test_run_service

    wanted = test_spec_service.case_id_key(case_id)
    cases = test_spec_service.parse_spec(test_run_service._read_doc_content_or_empty(doc))["cases"]
    found = next((case["case_id"] for case in cases
                  if test_spec_service.case_id_key(case["case_id"]) == wanted), None)
    if found is None:
        raise _refuse(404, "case_not_found", doc_id=doc["doc_id"], case_id=case_id)
    return found


def list_remarks(doc_id: str, case_id: Optional[str] = None) -> dict:
    """``{doc_id, remarks, counts}``: every remark oldest first and the per-case tally."""
    _ts_document(doc_id)
    rows = [test_spec_service.shape_remark(row) for row in db_remarks.list_by_doc(doc_id)]
    counts = counts_by_case(doc_id, rows)
    if case_id:
        wanted = test_spec_service.case_id_key(case_id)
        rows = [row for row in rows if test_spec_service.case_id_key(row["case_id"] or "") == wanted]
    return {"doc_id": doc_id, "remarks": rows, "counts": counts}


def counts_by_case(doc_id: str, shaped: Optional[list[dict]] = None) -> dict:
    """``{case key: {"open": n, "resolved": m}}`` (the key is ``case_id_key`` of the case id)."""
    rows = shaped if shaped is not None else db_remarks.list_by_doc(doc_id)
    return test_spec_service.remark_counts(rows)


def add_remark(doc_id: str, case_id: str, payload: dict, *, author_kind: str,
               author_name: str) -> dict:
    doc = _ts_document(doc_id)
    if not db_remarks.available():
        raise _refuse(503, "remarks_unavailable", doc_id=doc_id)
    try:
        remark = test_spec_service.normalize_remark(
            payload, author_kind=author_kind, author_name=author_name)
    except test_spec_service.SpecResultError as exc:
        raise _refuse(422, exc.code, message=exc.detail) from exc
    row = db_remarks.insert(
        doc_id=doc_id, case_id=_known_case_id(doc, case_id), author_kind=remark["author_kind"],
        author_name=remark["author_name"], phase=remark["phase"], body=remark["text"])
    from modules.flow_gate.services.test_card_changes import reopen
    reopen(doc, remark["phase"])
    return test_spec_service.shape_remark(row)


def update_remark(doc_id: str, remark_id: str, payload: dict, *, resolved_by: str = "") -> dict:
    """Resolve / open again (``state``) and / or set the AI's one-line answer (``ai_reply``)."""
    _ts_document(doc_id)
    current = db_remarks.get(remark_id)
    if current is None or current.get("doc_id") != doc_id:
        raise _refuse(404, "remark_not_found", doc_id=doc_id, remark_id=remark_id)
    state = payload.get("state")
    reply = payload.get("ai_reply")
    if state is None and reply is None:
        raise _refuse(422, "invalid_remark", message="state or ai_reply is required.")
    try:
        if state is not None:
            state = test_spec_service.normalize_remark_state(state)
    except test_spec_service.SpecResultError as exc:
        raise _refuse(422, exc.code, message=exc.detail) from exc
    if reply is not None:
        reply = test_spec_service.normalize_remark_reply(reply)
    if state == "resolved" and resolved_by:
        reply = test_spec_service.normalize_remark_reply(resolved_by)
    row = db_remarks.update(remark_id, state=state, ai_reply=reply)
    if state == "open":
        from modules.flow_gate.services.test_card_changes import reopen
        reopen(_ts_document(doc_id), current["phase"])
    return test_spec_service.shape_remark(row or current)
