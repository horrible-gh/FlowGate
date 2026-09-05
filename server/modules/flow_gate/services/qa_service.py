"""QA service — A registration + Q state transition + new token issuance (D022 §4-3).

Handles the business logic for the A registration endpoint (qa_routes.py).
"""
from __future__ import annotations

import json
import pathlib
from typing import Optional

from fastapi import HTTPException

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.numbering import numbering_service
from modules.flow_gate.numbering.id_formatter import parse_doc_code
from modules.flow_gate.services import token_service
from modules.flow_gate.storage.paths import document_path


# ── Q state validation ────────────────────────────────────────────────────────

def get_q_for_answer(q_id: str) -> dict:
    """Fetch a Q document + validate its state.

    Returns:
        q_doc dict

    Raises:
        HTTPException 404 — Q document does not exist
        HTTPException 409 — already in closed state
    """
    q_doc = db_docs.get_by_id(q_id)
    if q_doc is None:
        raise HTTPException(status_code=404, detail=f"Q document {q_id} does not exist")
    if q_doc.get("type_code", "").upper() != "Q":
        raise HTTPException(status_code=404, detail=f"Document {q_id} is not of type Q")
    if q_doc.get("status") == "closed":
        raise HTTPException(status_code=409, detail="This Q is already closed; no more answers can be added.")
    return q_doc


# ── A document creation ───────────────────────────────────────────────────────

def create_answer_doc(
    q_doc: dict,
    answer_body: str,
    actor_user_id: str,
    module: str = "none",
) -> tuple[str, str]:
    """Reserve an ID for an A document, store it, and register it in the DB.

    Args:
        q_doc: Q document record (dict)
        answer_body: A body markdown text
        actor_user_id: registrant user_id
        module: module identifier

    Returns:
        (a_doc_id, stored_path_str)
    """
    group_id: str = q_doc["group_id"]
    project_id: str = q_doc["project_id"]

    # Reserve an ID
    try:
        doc_code = numbering_service.reserve_document(
            group_id=group_id,
            doc_type="A",
            module=module,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Document numbering lock exceeded. Please retry shortly: {exc}")

    a_doc_id = f"{group_id}.{doc_code}"
    _, seq = parse_doc_code(doc_code)

    # Storage path
    stored_path: pathlib.Path = document_path(
        project_id=project_id,
        group_code=group_id,
        doc_code=doc_code,
        filename="document.md",
        module=module,
    )

    try:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_text(answer_body, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"An error occurred while processing storage: {exc}")

    # DB registration
    now = now_iso()
    try:
        db_docs.create({
            "doc_id": a_doc_id,
            "project_id": project_id,
            "module": module,
            "group_id": group_id,
            "type_code": "A",
            "seq": seq,
            "title": a_doc_id,
            "file_path": str(stored_path),
            "status": "open",
            "owner_id": actor_user_id,
            "triggered_by": q_doc["doc_id"],
            "revision_no": 0,
            "created_at": now,
            "updated_at": now,
        })
    except Exception as exc:
        try:
            stored_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"An error occurred during DB registration: {exc}")

    # T823: transition A doc to pending_review immediately after creation (no NULL doc_review_status).
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    transition_document_review(
        doc_id=a_doc_id,
        action="submit",
        actor_user_id=actor_user_id,
        user_permissions={"document.update"},
    )

    return a_doc_id, str(stored_path)


# ── Q state transition ────────────────────────────────────────────────────────

def transition_q_to_answered(q_id: str, a_doc_id: str, actor_user_id: str) -> None:
    """Q.status open → answered (CAS — D017 §8-3).

    Raises:
        HTTPException 409 — concurrent update conflict
    """
    store = get_store()
    q_doc = db_docs.get_by_id(q_id)
    if q_doc is None:
        return

    project_id: str = q_doc["project_id"]
    group_id: str = q_doc.get("group_id", "")
    now = now_iso()

    store._execute(
        "UPDATE documents SET status = 'answered', updated_at = ? "
        "WHERE doc_id = ? AND status = 'open'",
        [now, q_id],
    )

    refreshed = db_docs.get_by_id(q_id)
    if refreshed is None or refreshed.get("status") != "answered":
        raise HTTPException(status_code=409, detail="Q state transition conflict occurred. Please retry.")

    # workflow_events: qna_answered
    db_events.create({
        "event_type": "qna_answered",
        "project_id": project_id,
        "group_id": group_id,
        "document_id": None,
        "actor_user_id": actor_user_id,
        "from_state": "open",
        "to_state": "answered",
        "metadata": json.dumps({
            "q_doc_id": q_id,
            "a_doc_id": a_doc_id,
        }),
    })


# ── New token issuance ────────────────────────────────────────────────────────

def issue_followup_token(
    q_doc: dict,
    a_doc_id: str,
    actor_user_id: str,
    dispatch_mode: str,
    ai_run_id: Optional[str] = None,
) -> dict:
    """Issue a new token after A registration (D022 §4-3-4).

    Context binding:
      action_scope = edit
      doc_ref = Q.prev_doc_id (parent work item)

    Returns:
        Result dict from token_service.issue() (raw_token, token_id, expires_at, scratch_dir)
    """
    project_id: str = q_doc["project_id"]
    group_id: Optional[str] = q_doc.get("group_id")
    prev_doc_id: Optional[str] = q_doc.get("triggered_by")

    result = token_service.issue(
        project=project_id,
        group_id=group_id,
        action_scope="edit",
        doc_ref=prev_doc_id,
        issued_to=actor_user_id,
        ai_run_id=ai_run_id,
    )

    # workflow_events: qna_token_reissued
    db_events.create({
        "event_type": "qna_token_reissued",
        "project_id": project_id,
        "group_id": group_id,
        "document_id": None,
        "actor_user_id": actor_user_id,
        "from_state": None,
        "to_state": None,
        "metadata": json.dumps({
            "q_doc_id": q_doc["doc_id"],
            "a_doc_id": a_doc_id,
            "token_id": result["token_id"],
            "dispatch_mode": dispatch_mode,
        }),
    })

    return result


# ── ment_copy prompt generation ───────────────────────────────────────────────

def build_ment_text(
    q_doc_id: str,
    a_doc_id: str,
    scratch_dir: str,
    prev_doc_id: Optional[str],
    api_base_url: str,
    raw_token: str = "",
) -> str:
    """Build the ment_copy mode prompt body (M020-compliant).

    Excluded by the M020 prohibited-items rules:
      - FlowGate API base URL
      - token expiration information
      - action scope line
      - body line
      - direct raw_token exposure (use the Authorization: Bearer <token> format)
    """
    base = api_base_url.rstrip("/")
    lines: list[str] = [
        "[Q/A follow-up] An answer has been posted for the Q document.",
        f"Q document ID: {q_doc_id}",
        f"A document ID: {a_doc_id}",
        "",
        "Use the information below to resume the previous work.",
        "",
    ]
    if prev_doc_id:
        doc_path = prev_doc_id.replace("-", "/", 3)
        lines += [
            f"- Referenced document: {prev_doc_id}",
            f"- Fetch referenced document: GET {base}/document/{doc_path}",
        ]
    token_str = raw_token if raw_token else "<token>"
    lines += [
        "",
        f"Submit modified deliverable: POST {base}/inbox",
        f"  Authorization: Bearer {token_str}",
    ]
    return "\n".join(lines)
