"""Q&A service — document-bound query/answer container (group 0022 Q/A/V rework).

Queries and answers are treated as sub-data hung off a document (`documents.doc_id`)
(DB0006 §3, L0007 §3).
- One container per document (`questions.doc_id` UNIQUE), `q_id := doc_id`.
- A query (`question_items`) has a title, body, and asker (`asker_kind` human|ai).
- An answer (`answers`) is bidirectional human/AI (`author_kind`/`author_id`; AI has author_id NULL).

sqloader rule: no inline SQL. Go through queries.json + the db module.
"""
from __future__ import annotations

import sqlite3
from typing import Optional, Union

from fastapi import HTTPException

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import questions as db_questions
from modules.flow_gate.db import question_items as db_question_items
from modules.flow_gate.db import answers as db_answers
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.api.v1.events.event_types import EventType
from modules.flow_gate.api.v1.events.publisher import FlowEvent, publish_event_threadsafe

# Reserved system user (DB0006 §4.1 seed). Used as the container created_by for paths
# with no human subject (AI registration / artifact-accompanied) to satisfy the
# created_by FK and NOT NULL constraints (L0007 §3.1).
AI_SYSTEM_USER = "u-system"

QuestionInput = Union[str, dict]


# ── SSE notifications ────────────────────────────────────────────────────────────

def _notify_q_registered(
    audience: Optional[str], doc_id: str, project_id: Optional[str], titles: list[str]
) -> None:
    """Notice event announcing a query registration to the console/UI (L0007 §4).

    Conveys only the fact that something was 'registered', with no choice menu,
    recommendation, or default selection. A delivery failure does not affect
    registration success.
    """
    if not audience:
        return
    event = FlowEvent(
        event_type=EventType.QNA_Q_REGISTERED,
        payload={"doc_id": doc_id, "project_id": project_id, "titles": titles},
        audience=audience,
        project=project_id,
        doc_id=doc_id,
    )
    # add_questions runs inside a sync FastAPI route (POST /q/{doc_id}/questions),
    # i.e. a worker thread with no running event loop. The old asyncio.get_event_loop()
    # path raised RuntimeError there and was swallowed, so this event was never emitted
    # and the worker's Q stayed invisible until F5 (0059 B0001). publish_event_threadsafe
    # schedules onto the captured main loop — the same mechanism the working
    # workflow-decision broadcast uses.
    publish_event_threadsafe(event)


# ── Input normalization ──────────────────────────────────────────────────────────

def _normalize_questions(questions: list[QuestionInput]) -> list[tuple[Optional[str], str]]:
    """[{title?, body}|str, ...] → [(title, body), ...]. body is required."""
    out: list[tuple[Optional[str], str]] = []
    for q in questions:
        if isinstance(q, str):
            title, body = None, q
        elif isinstance(q, dict):
            title = (q.get("title") or None)
            body = q.get("body") or ""
        else:
            raise HTTPException(status_code=400, detail="question must be a string or {title, body}")
        if not body or not body.strip():
            raise HTTPException(status_code=400, detail="question body must not be empty")
        out.append((title, body))
    return out


# ── Lazy container creation (keyed by doc_id, L0007 §3.1) ────────────────────────

def ensure_container(
    doc_id: str,
    project_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict:
    """Return the document's query container (lazily creating it if absent). Idempotent and race-safe.

    When created_by is unspecified, fill it with the reserved system user ('u-system')
    (the AI path). The human path passes that user's user_id from the caller. A
    UNIQUE(doc_id) race raises IntegrityError → re-SELECT.
    """
    existing = db_questions.get_container_by_doc(doc_id)
    if existing is not None:
        return existing

    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} does not exist")

    proj = project_id if project_id is not None else doc.get("project_id")
    title = doc.get("title") or doc_id
    creator = created_by or AI_SYSTEM_USER

    store = get_store()
    try:
        with store.transaction():
            if db_questions.get_container_by_doc(doc_id) is None:
                db_questions.insert_container_for_doc(
                    doc_id=doc_id, project_id=proj, title=title, created_by=creator
                )
    except sqlite3.IntegrityError:
        # Concurrent first-query race — another transaction created it first. Absorb via re-SELECT.
        pass

    container = db_questions.get_container_by_doc(doc_id)
    if container is None:
        raise HTTPException(status_code=500, detail="Failed to create question container")
    return container


# ── Question anchor correction (B0001 / NR0003, group 0059) ──────────────────────

def resolve_question_anchor(doc_id: str) -> str:
    """Determine the 'current work-context' document to anchor an AI worker's question.

    A worker token is bound to the workflow spine (the R/B root that owns the sequence),
    so questions registered with that token pile up on a far-upstream spine (e.g.
    0044.0001-R) that the console user never looks at (B0001). Per NR0003 §8, correct the
    question's location to the user's current work-context document:
      (a) if the current head stage already has an artifact, use it (the TR/NR being written),
          otherwise
      (b) the immediately preceding instruction document (the T/N this stage reports on).
    For the first stage with neither (no artifact yet) or a document with no sequence,
    fall back to the spine itself.

    This uses the same sequence helper as the predecessor resolution in Section 1
    'Document information' (the ment), so the displayed work-context document and the
    question anchor always match.
    """
    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is None:
        return doc_id
    head = db_wfseq.get_effective_head(seq["id"])
    if head is None:
        return doc_id
    # (a) if the head stage already has an artifact (e.g. a TR awaiting review), anchor to it.
    head_result = head.get("result_doc_id")
    if head_result:
        return head_result
    # (b) no artifact yet — anchor to the most recently produced instruction document (T/N).
    pred = db_wfseq.get_predecessor_result_doc_id(seq["id"], head.get("id"))
    return pred or doc_id


# ── Add question (human [+query] / AI registration §4 / AI artifact-accompanied §5) ──

def add_questions(
    doc_id: str,
    questions: list[QuestionInput],
    asker_kind: str = "human",
    created_by: Optional[str] = None,
    project_id: Optional[str] = None,
    notify_audience: Optional[str] = None,
) -> dict:
    """Add N queries to the document's container (creating the container if absent).

    asker_kind: 'human'([+query]) | 'ai' (§3.3/§3.4 AI registration). A done container
    reverts to pending.
    Returns {"doc_id", "added_item_ids": [...]}.
    """
    if not questions:
        raise HTTPException(status_code=400, detail="questions must contain at least one item")
    if asker_kind not in ("human", "ai"):
        raise HTTPException(status_code=400, detail="asker_kind must be 'human' or 'ai'")
    normalized = _normalize_questions(questions)

    container = ensure_container(doc_id, project_id=project_id, created_by=created_by)
    qpk: int = container["id"]
    max_seq = db_question_items.get_max_seq(qpk)

    store = get_store()
    with store.transaction():
        for offset, (title, body) in enumerate(normalized, start=1):
            db_question_items.insert(
                question_pk=qpk, seq=max_seq + offset, body=body,
                title=title, asker_kind=asker_kind,
            )
        # Re-query: revert done → pending (consistent with D0005 §4 "re-query = new item")
        if container.get("status") == "done":
            db_questions.update_status(doc_id, "pending")

    all_items = db_question_items.list_by_question(qpk)
    added_ids = [it["id"] for it in all_items if it["seq"] > max_seq]

    if asker_kind == "ai":
        _notify_q_registered(
            audience=notify_audience or container.get("pm_id"),
            doc_id=doc_id,
            project_id=container.get("project_id"),
            titles=[t or b[:40] for t, b in normalized],
        )

    return {"doc_id": doc_id, "added_item_ids": added_ids}


# ── Register answer (human/AI bidirectional, atomicity L0007 §3.2/§3.3) ──────────

def register_answer(
    doc_id: str,
    item_id: int,
    body: str,
    author_kind: str = "human",
    author_id: Optional[str] = None,
) -> dict:
    """Register an answer to a query item and transition the container status (atomic).

    Wraps the four writes (insert answer → increment answer_count → status transition)
    in a single transaction to block partial commits. When author_kind='ai', author_id
    is NULL. When every item has answer_count≥1, status=done.
    """
    if author_kind not in ("human", "ai"):
        raise HTTPException(status_code=400, detail="author_kind must be 'human' or 'ai'")
    if author_kind == "human" and not author_id:
        raise HTTPException(status_code=400, detail="author_id is required for human answers")
    if author_kind == "ai":
        author_id = None

    container = db_questions.get_container_by_doc(doc_id)
    if container is None:
        raise HTTPException(status_code=404, detail=f"Question container for {doc_id} does not exist")

    item = db_question_items.get_by_pk(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"question_item {item_id} does not exist")
    if item["question_id"] != container["id"]:
        raise HTTPException(
            status_code=404,
            detail=f"question_item {item_id} does not belong to document {doc_id}",
        )

    q_status = container["status"]
    store = get_store()
    with store.transaction():
        db_answers.insert(
            question_item_id=item_id, body=body,
            author_kind=author_kind, author_id=author_id,
        )
        db_question_items.increment_answer_count(pk=item_id)
        unanswered = db_question_items.list_unanswered(container["id"])
        if not unanswered and q_status != "done":
            db_questions.update_status(doc_id, "done")
            q_status = "done"

    all_answers = db_answers.list_by_question_item(item_id)
    answer_id: Optional[int] = all_answers[-1]["id"] if all_answers else None

    return {
        "doc_id": doc_id,
        "item_id": item_id,
        "answer_id": answer_id,
        "author_kind": author_kind,
        "status": q_status,
    }


# ── Lookup ───────────────────────────────────────────────────────────────────────

def get_qa_detail(doc_id: str) -> dict:
    """The document's query container + items + answers tree. Empty structure if no container.

    Returns {doc_id, status, items: [{...item, answers: [...]}]}.
    """
    container = db_questions.get_container_by_doc(doc_id)
    if container is None:
        return {"doc_id": doc_id, "status": None, "items": []}

    items = db_question_items.list_by_question(container["id"])
    result = dict(container)
    result["doc_id"] = doc_id
    result["items"] = []
    for item in items:
        item_dict = dict(item)
        item_dict["answers"] = db_answers.list_by_question_item(item["id"])
        result["items"].append(item_dict)
    return result


def get_answers_for_document(doc_id: str) -> list[dict]:
    """Array of Q&A pairs for document doc_id (legacy document_routes compatibility).

    Returns [{"Q": item_body, "A": latest_answer_body_or_null}, ...] / [] if none.
    """
    container = db_questions.get_container_by_doc(doc_id)
    if container is None:
        return []
    items = db_question_items.list_by_question(container["id"])
    result = []
    for item in items:
        answers = db_answers.list_by_question_item(item["id"])
        latest_a = answers[-1]["body"] if answers else None
        result.append({"Q": item["body"], "A": latest_a})
    return result


def list_open_items(project_id: Optional[str] = None) -> list[dict]:
    """Aggregate of 'open queries' (items being answered, D0005 §3.7). project_id=None → all."""
    return db_questions.list_open_items(project_id)


def qa_bundle_by_doc(doc_id: str) -> list[dict]:
    """Q&A bundle for ment assembly (L0007 §6)."""
    return db_question_items.qa_bundle_by_doc(doc_id)
