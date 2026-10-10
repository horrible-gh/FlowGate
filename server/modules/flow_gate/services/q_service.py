"""Q&A service — document-bound query/answer container (group 0022 Q/A/V rework).

Queries and answers are treated as sub-data hung off a document (`documents.doc_id`)
(DB0006 §3, L0007 §3).
- One container per document (`questions.doc_id` UNIQUE), `q_id := doc_id`.
- A query (`question_items`) has a title, body, and asker (`asker_kind` human|ai).
- An answer (`answers`) is bidirectional human/AI (`author_kind`/`author_id`; AI has author_id NULL).

sqloader rule: no inline SQL. Go through queries.json + the db module.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional, Union

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

logger = logging.getLogger(__name__)

QuestionInput = Union[str, dict]

# ── Option limits (L0008 §1) ─────────────────────────────────────────────────────
# question_items.options / answers.selected_options carry no DB-level CHECK — JSON
# validation has no common syntax across the three dialects (DB0007 §2) — so the
# validation below is the ONLY enforcement of every invariant in DB0007 §5.
MAX_OPTIONS = 10          # hard cap on options per question item (abuse guard)
MAX_OPTION_LABEL = 200    # max label length (after strip); long proposals go in the question body
MAX_SELECTED = 1          # v1 is single-select


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


def _notify_q_answered(
    audience: Optional[str],
    doc_id: str,
    project_id: Optional[str],
    item_id: int,
    unanswered_count: int,
) -> None:
    """Tell every tab for the answering user that server-side Q state changed."""
    if not audience:
        return
    publish_event_threadsafe(FlowEvent(
        event_type=EventType.QNA_ANSWER_REGISTERED,
        payload={
            "doc_id": doc_id,
            "project_id": project_id,
            "item_id": item_id,
            "unanswered_count": unanswered_count,
        },
        audience=audience,
        project=project_id,
        doc_id=doc_id,
    ))


def _notify_responder_state(
    audience: Optional[str],
    doc_id: str,
    project_id: Optional[str],
    item_id: int,
    *,
    state: str,
    error_code: Optional[str],
    message: Optional[str],
    run_id: Optional[str],
) -> None:
    """The automatic responder left this item unanswered — refresh + toast (0661 T0004 F3/F5).

    Carries the state so the toast can tell a technical failure from an explicit
    hand-off, but the panel re-reads the item (`fg:qa_refresh`) for the durable truth.
    """
    if not audience:
        return
    publish_event_threadsafe(FlowEvent(
        event_type=EventType.QNA_RESPONDER_STATE_CHANGED,
        payload={
            "doc_id": doc_id,
            "project_id": project_id,
            "item_id": item_id,
            "state": state,
            "error_code": error_code,
            "message": message,
            "run_id": run_id,
        },
        audience=audience,
        project=project_id,
        doc_id=doc_id,
    ))


# ── Input normalization ──────────────────────────────────────────────────────────

def _validate_options(raw_options: Any) -> list[str]:
    """Validate a query item's raw options payload → list of stripped labels (L0008 §2.2).

    The request carries plain label strings; ids are always server-assigned
    (_assign_option_ids), so the request surface never accepts one.
    """
    if not isinstance(raw_options, list):
        raise HTTPException(status_code=400, detail="options must be an array of strings")
    if len(raw_options) > MAX_OPTIONS:
        raise HTTPException(
            status_code=400, detail=f"options must contain at most {MAX_OPTIONS} items"
        )
    labels: list[str] = []
    for item in raw_options:
        if not isinstance(item, str):
            raise HTTPException(status_code=400, detail="each option must be a string label")
        label = item.strip()
        if not label:
            raise HTTPException(status_code=400, detail="option label must not be empty")
        if len(label) > MAX_OPTION_LABEL:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"option label must be {MAX_OPTION_LABEL} characters or fewer; "
                    "describe a long alternative in the question body instead"
                ),
            )
        if label in labels:
            raise HTTPException(status_code=400, detail=f"duplicate option label: {label}")
        labels.append(label)
    return labels


def _assign_option_ids(labels: list[str]) -> list[dict]:
    """Labels → [{"id": "o1", "label": ...}, ...] (L0008 §2.3).

    An id only has to be unique within its item (an answer references it in item scope),
    and the 1-based position makes that structural. Options are immutable once registered
    — v1 has no edit/delete API — so ids are never reassigned and selected_options keeps
    referential integrity over time (DB0007 §5).
    """
    return [{"id": f"o{n}", "label": label} for n, label in enumerate(labels, start=1)]


def _dump_json(value: Any) -> str:
    """Serialize to compact JSON, keeping non-ASCII as-is so a stored label matches its
    original text byte for byte (L0008 §2.4)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalize_questions(
    questions: list[QuestionInput],
) -> list[tuple[Optional[str], str, list[dict]]]:
    """[{title?, body, options?}|str, ...] → [(title, body, options), ...]. body is required.

    Options are normalized HERE rather than at the call site: the tuple this returns is the
    only thing add_questions writes, so an option normalized anywhere else would be dropped
    silently instead of raising (NR0004 §6). A plain-string question (legacy worker payload)
    normalizes to options=[] — byte-identical behaviour to before this extension.
    """
    out: list[tuple[Optional[str], str, list[dict]]] = []
    for q in questions:
        if isinstance(q, str):
            title, body, raw_options = None, q, []
        elif isinstance(q, dict):
            title = (q.get("title") or None)
            body = q.get("body") or ""
            raw_options = q.get("options") or []
        else:
            raise HTTPException(
                status_code=400, detail="question must be a string or {title, body, options}"
            )
        if not body or not body.strip():
            raise HTTPException(status_code=400, detail="question body must not be empty")
        out.append((title, body, _assign_option_ids(_validate_options(raw_options))))
    return out


def _parse_options(item: dict) -> list[dict]:
    """Stored options JSON → [{"id", "label"}]. Unparseable → [] (L0008 §5).

    Unreachable on the live path (the single write gate validates before storing), but a
    malformed row must not take down a read.
    """
    raw = item.get("options")
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


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

def _resolve_non_ch_predecessor(
    sequence_id: int,
    exclude_item_id: Optional[int],
) -> Optional[str]:
    """Return the newest produced document that can visibly host Q&A.

    Workflow predecessor helpers intentionally retain CH results for mention and UI context.
    Question anchoring has the narrower contract, so it filters only in this service.
    """
    items = db_wfseq.get_sequence_items(sequence_id)
    candidates = sorted(items, key=lambda item: item.get("sort_order") or 0, reverse=True)
    for item in candidates:
        if item.get("id") == exclude_item_id:
            continue
        result_doc_id = item.get("result_doc_id")
        if not result_doc_id:
            continue
        result_doc = db_documents.get_by_id(result_doc_id)
        if result_doc is not None and result_doc.get("type_code") != "CH":
            return result_doc_id
    return None


def resolve_question_anchor(doc_id: str) -> str:
    """Determine the visible work-context document for an AI worker's question.

    CH remains valid workflow and predecessor context, but it has no Q&A surface. A CH
    candidate is therefore skipped here in favour of the newest earlier non-CH result,
    with the original workflow spine as the final fallback.
    """
    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is None:
        return doc_id
    head = db_wfseq.get_effective_head(seq["id"])
    if head is None:
        return doc_id

    head_result = head.get("result_doc_id")
    if head_result:
        result_doc = db_documents.get_by_id(head_result)
        if result_doc is not None and result_doc.get("type_code") != "CH":
            return head_result

    predecessor = _resolve_non_ch_predecessor(seq["id"], head.get("id"))
    return predecessor or doc_id


# ── Add question (human [+query] / AI registration §4 / AI artifact-accompanied §5) ──

def add_questions(
    doc_id: str,
    questions: list[QuestionInput],
    asker_kind: str = "human",
    created_by: Optional[str] = None,
    project_id: Optional[str] = None,
    notify_audience: Optional[str] = None,
    asker_provenance: Optional[dict] = None,
) -> dict:
    """Add N queries to the document's container (creating the container if absent).

    asker_kind: 'human'([+query]) | 'ai' (§3.3/§3.4 AI registration). A done container
    reverts to pending.
    ``asker_provenance`` (0582 T0005 §D) is the AI run/provider snapshot the caller
    already resolved via ai_invoke.provenance.resolve_run_provenance — ignored for a
    human query, and legitimately {} for an AI query whose token carried no bound run.
    Returns {"doc_id", "added_item_ids": [...]}.
    """
    if not questions:
        raise HTTPException(status_code=400, detail="questions must contain at least one item")
    if asker_kind not in ("human", "ai"):
        raise HTTPException(status_code=400, detail="asker_kind must be 'human' or 'ai'")
    normalized = _normalize_questions(questions)
    _provenance = asker_provenance if asker_kind == "ai" and asker_provenance else {}

    target_doc = db_documents.get_by_id(doc_id)
    if target_doc is not None and target_doc.get("type_code") == "CH":
        raise HTTPException(
            status_code=400,
            detail=(
                "This is a conversation (CH) document and has no Q container. "
                "Ask the question directly in the conversation instead."
            ),
        )

    container = ensure_container(doc_id, project_id=project_id, created_by=created_by)
    qpk: int = container["id"]
    max_seq = db_question_items.get_max_seq(qpk)

    store = get_store()
    with store.transaction():
        for offset, (title, body, options) in enumerate(normalized, start=1):
            db_question_items.insert(
                question_pk=qpk, seq=max_seq + offset, body=body,
                title=title, asker_kind=asker_kind, options=_dump_json(options),
                asker_ai_run_id=_provenance.get("ai_run_id"),
                asker_actual_provider_id=_provenance.get("actual_provider_id"),
                asker_actual_provider_name=_provenance.get("actual_provider_name"),
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
            # Options are deliberately absent from the notice — it announces only that a
            # query was registered, never the choices themselves (D0006 §3, 0022 rule A).
            titles=[t or b[:40] for t, b, _ in normalized],
        )

    return {"doc_id": doc_id, "added_item_ids": added_ids}


# ── Last-answer → paused-chain continuation (flowgate.default.0551 T#2) ────────

def auto_resume_answered_chain(
    *,
    doc_id: str,
    api_base_url: str,
    locale: str = "ko",
    responder_run: Optional[dict] = None,
) -> Optional[dict]:
    """Resume the durable question_pending chain once the group has no open Q.

    This is orchestration only: ai_invoke_service.resume_chain remains the one
    resume engine and its group lock + paused-row compare-and-swap remain the one
    exactly-once boundary. The helper is deliberately best-effort because an answer
    that was committed must never be rolled back or reported as failed merely because
    the continuation lost a race to cancel, another answer, or another process.

    It is called both from the answer write path (human answers and restart recovery)
    and after run finalization (an AI responder still owns the group lease while it
    POSTs its answer, so that first call is expected to defer until finalization).
    """
    try:
        doc = db_documents.get_by_id(doc_id)
        group_id = (doc or {}).get("group_id")
        if not group_id:
            return None

        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
        from modules.flow_gate.db import ai_invoke_runs as db_runs
        from modules.flow_gate.services import ai_invoke_service

        row = db_paused.get_by_group(group_id)
        if (
            row is None
            or (row.get("stop_kind") or "user") != "system"
            or row.get("stop_code") != "question_pending"
        ):
            return None
        # A container can be done while another document in the same group still has
        # an unanswered item. The group-wide query is the final gate.
        if db_questions.list_open_doc_ids_by_group(group_id):
            return None

        requester_run_id = row.get("stop_run_id")
        requester_provider_id = None
        if requester_run_id:
            try:
                requester = db_runs.get(requester_run_id)
                requester_provider_id = (requester or {}).get("provider_id")
            except Exception:
                logger.warning(
                    "question auto-resume requester lookup failed for %s",
                    requester_run_id,
                    exc_info=True,
                )

        trace = {
            "requester_run_id": requester_run_id,
            "requester_provider_id": requester_provider_id,
            "responder_run_id": (responder_run or {}).get("run_id"),
            "responder_provider_id": (responder_run or {}).get("provider_id"),
            "paused_chain_id": row.get("chain_id"),
            "resumed_run_id": None,
            "resumed_chain_id": None,
        }
        try:
            resumed = ai_invoke_service.resume_chain(
                group_id=group_id,
                # The paused chain belongs to its original requester, not necessarily
                # to the human/responder that supplied the final answer.
                user_id=row.get("paused_by"),
                api_base_url=api_base_url,
                locale=locale or "ko",
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            logger.info(
                "question auto-resume deferred group_id=%s code=%s "
                "requester_run_id=%s responder_run_id=%s chain_id=%s",
                group_id,
                detail.get("code") or exc.status_code,
                trace["requester_run_id"],
                trace["responder_run_id"],
                trace["paused_chain_id"],
            )
            return None

        trace["resumed_run_id"] = resumed.get("run_id")
        trace["resumed_chain_id"] = resumed.get("chain_id")
        resumed["question_resume_trace"] = trace
        # Keep the correlation on the live run too, so GET status exposes the same
        # evidence as the immediate answer response. The structured log remains the
        # durable audit trail after this process exits.
        resumed_record = ai_invoke_service.get_run_record(trace["resumed_run_id"])
        if resumed_record is not None:
            resumed_record["question_resume_trace"] = dict(trace)
        logger.info(
            "question auto-resumed group_id=%s requester_run_id=%s "
            "requester_provider_id=%s responder_run_id=%s responder_provider_id=%s "
            "paused_chain_id=%s resumed_run_id=%s resumed_chain_id=%s",
            group_id,
            trace["requester_run_id"],
            trace["requester_provider_id"],
            trace["responder_run_id"],
            trace["responder_provider_id"],
            trace["paused_chain_id"],
            trace["resumed_run_id"],
            trace["resumed_chain_id"],
        )
        return trace
    except Exception:
        logger.warning(
            "question auto-resume failed for %s (answer remains committed)",
            doc_id,
            exc_info=True,
        )
        return None


# ── Register answer (human/AI bidirectional, atomicity L0007 §3.2/§3.3) ──────────

def register_answer(
    doc_id: str,
    item_id: int,
    body: str,
    author_kind: str = "human",
    author_id: Optional[str] = None,
    selected_option_ids: Optional[list[str]] = None,
    notify_audience: Optional[str] = None,
    author_provenance: Optional[dict] = None,
    auto_resume_api_base_url: Optional[str] = None,
    auto_resume_locale: str = "ko",
    writer_ai_run_id: Optional[str] = None,
) -> dict:
    """Register an answer to a query item and transition the container status (atomic).

    Wraps the four writes (insert answer → increment answer_count → status transition)
    in a single transaction to block partial commits. When author_kind='ai', author_id
    is NULL. When every item has answer_count≥1, status=done.

    An answer may pick an option, write freely, or do both (L0008 §4). Picking alone fills
    body with the chosen option's label, so answers.body stays non-blank and every existing
    body-only reader (ment assembly, the answer list) works unchanged (DB0007 §5).

    ``writer_ai_run_id`` (0661 T0004 F6) is the server-verified run bound to the AI
    token that is answering — NOT a request field. The run that registered the question
    (``question_items.asker_ai_run_id``) may not close its own question with it: a worker
    answering itself would mark the Q done and auto-resume the chain it parked, which is
    exactly the contract B0001 ruled out. A human session passes None. An AI token with
    no bound run (the [Copy Mention] hand-off) also passes None and is not checked — it
    has no run identity to compare, and that path is already shown as external/unconfirmed.
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
    if (
        author_kind == "ai"
        and writer_ai_run_id
        and item.get("asker_ai_run_id")
        and item.get("asker_ai_run_id") == writer_ai_run_id
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "An AI run may not answer the question it registered itself. Leave it "
                "for the assigned responder or a user; the chain resumes once the answer "
                "lands."
            ),
        )
    # Review rej_01M4HRWSVP3M0BSH findings 3/4: the run dispatched as this item's responder
    # answers at most once, only while nobody else has, and never after it handed the item
    # to a user (escalate). The checks here give the precise refusal; the CAS increment in
    # the transaction below is what actually holds against a concurrent human answer.
    responder_writer = bool(
        author_kind == "ai"
        and writer_ai_run_id
        and item.get("responder_run_id") == writer_ai_run_id
    )
    if responder_writer:
        if item.get("responder_state") == db_question_items.RESPONDER_USER_DECISION:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This question was handed to a user (user decision required); the "
                    "responder that escalated it may not answer it."
                ),
            )
        if int(item.get("answer_count") or 0) > 0:
            raise HTTPException(status_code=409, detail="This question already has an answer.")
    elif item.get("responder_state") and int(item.get("answer_count") or 0) > 0:
        # TR0005 rev1 review: an item the automatic responder took on takes one answer from
        # anyone — a person answering after the responder's answer landed is refused, not
        # stacked as a second answer. The guarded increment below holds the same rule
        # against a read that is already stale.
        raise HTTPException(status_code=409, detail="This question already has an answer.")

    selected = list(selected_option_ids or [])
    if len(selected) > MAX_SELECTED:
        raise HTTPException(status_code=400, detail="only one option may be selected")
    options = _parse_options(item)
    labels_by_id = {o.get("id"): o.get("label", "") for o in options if isinstance(o, dict)}
    for oid in selected:
        # Covers items with no options at all — every id is unknown there.
        if oid not in labels_by_id:
            raise HTTPException(status_code=400, detail=f"unknown option id: {oid}")

    if not body or not body.strip():
        if not selected:
            raise HTTPException(status_code=400, detail="body must not be empty")
        # Picked-only: the label verbatim becomes the body (BODY_FILL, L0008 §1).
        body = labels_by_id[selected[0]]
    # A body that is already written stays as written: submitting a pick alongside prose
    # keeps the prose as the body and records the pick in selected_options only.

    _provenance = author_provenance if author_kind == "ai" and author_provenance else {}
    q_status = container["status"]
    store = get_store()
    with store.transaction():
        if responder_writer and not db_question_items.increment_answer_count_for_responder(
            item_id, writer_ai_run_id,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This question was answered, escalated or re-claimed concurrently; the "
                    "responder's answer was not registered."
                ),
            )
        if not responder_writer and not db_question_items.increment_answer_count(pk=item_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This question was answered concurrently by its AI responder; the "
                    "answer was not registered."
                ),
            )
        db_answers.insert(
            question_item_id=item_id, body=body,
            author_kind=author_kind, author_id=author_id,
            selected_options=_dump_json(selected),
            author_ai_run_id=_provenance.get("ai_run_id"),
            author_actual_provider_id=_provenance.get("actual_provider_id"),
            author_actual_provider_name=_provenance.get("actual_provider_name"),
            author_requested_provider_id=_provenance.get("requested_provider_id"),
            author_provider_source=_provenance.get("provider_source"),
            author_fallback_used=_provenance.get("fallback_used"),
        )
        if item.get("responder_state"):
            # 0661 T0004 F2/F3: whoever answered (the dispatched AI responder, a manual
            # [AI 답변 요청] run or a person), the item is no longer waiting on a responder.
            db_question_items.set_responder_state(
                item_id, db_question_items.RESPONDER_ANSWERED, None, None,
            )
        unanswered = db_question_items.list_unanswered(container["id"])
        if not unanswered and q_status != "done":
            db_questions.update_status(doc_id, "done")
            q_status = "done"

    all_answers = db_answers.list_by_question_item(item_id)
    answer_id: Optional[int] = all_answers[-1]["id"] if all_answers else None

    _notify_q_answered(
        audience=notify_audience,
        doc_id=doc_id,
        project_id=container.get("project_id"),
        item_id=item_id,
        unanswered_count=len(unanswered),
    )

    result = {
        "doc_id": doc_id,
        "item_id": item_id,
        "answer_id": answer_id,
        "author_kind": author_kind,
        "status": q_status,
    }
    if not unanswered and auto_resume_api_base_url:
        trace = auto_resume_answered_chain(
            doc_id=doc_id,
            api_base_url=auto_resume_api_base_url,
            locale=auto_resume_locale,
        )
        if trace is not None:
            result["question_resume_trace"] = trace
    return result


# ── Automatic responder outcomes (flowgate.default.0661 T0004 F3/F5) ────────────────
#
# Three outcomes of a responder run are kept apart on purpose (T0004 "실패 ≠ 정상 답변 ≠
# 사용자 판단 필요"): an answer row (register_answer above), a TECHNICAL failure
# (mark_responder_failed — the item stays open and can be re-dispatched from the panel),
# and the responder's EXPLICIT hand-off to a person (escalate_to_user — nothing retries
# it; a human answers). None of them touches the container status or the parked chain:
# `auto_resume_answered_chain`'s group-wide open-Q gate keeps the original run parked
# until the LAST item carries an answer.

RESPONDER_ERROR_USER_DECISION = "user_decision_required"
_RESPONDER_MESSAGE_MAX = 2000


def _clip_message(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text = str(text).strip()
    return text[:_RESPONDER_MESSAGE_MAX] if text else None


def _announce_responder_state(
    container: dict,
    item: dict,
    *,
    state: str,
    error_code: Optional[str],
    message: Optional[str],
    run_id: Optional[str],
    actor_user_id: Optional[str],
    notify_audience: Optional[str],
) -> None:
    """SSE refresh/toast + the durable notification-feed row. Best-effort on both."""
    doc_id = container.get("doc_id")
    project_id = container.get("project_id")
    try:
        _notify_responder_state(
            notify_audience or container.get("pm_id") or container.get("created_by"),
            doc_id, project_id, item["id"],
            state=state, error_code=error_code, message=message, run_id=run_id,
        )
    except Exception:
        logger.warning("responder state notice failed for %s/%s", doc_id, item.get("id"),
                       exc_info=True)
    try:
        from modules.flow_gate.workflow import event_logger

        doc = db_documents.get_by_id(doc_id) if doc_id else None
        event_logger.log_question_responder_event(
            event_type=(
                event_logger.EVT_QNA_USER_DECISION_REQUIRED
                if state == db_question_items.RESPONDER_USER_DECISION
                else event_logger.EVT_QNA_RESPONDER_FAILED
            ),
            project_id=project_id or (doc or {}).get("project_id") or "",
            actor_user_id=actor_user_id or container.get("created_by") or AI_SYSTEM_USER,
            group_id=(doc or {}).get("group_id"),
            document_id=(doc or {}).get("id"),
            doc_id=doc_id,
            item_id=item.get("id"),
            item_seq=item.get("seq"),
            run_id=run_id,
            error_code=error_code,
            message=message,
            requested_provider_id=item.get("responder_requested_provider_id"),
        )
    except Exception:
        logger.warning("responder state event failed for %s/%s", doc_id, item.get("id"),
                       exc_info=True)


def mark_responder_failed(
    item_id: int,
    error_code: str,
    message: Optional[str],
    *,
    run_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    notify_audience: Optional[str] = None,
) -> bool:
    """Record a TECHNICAL responder failure on the item (state 'failed').

    Never flags an item that already carries an answer, and with ``run_id`` only the
    owning run's finalization can write it (its own 'dispatched' claim). Without
    ``run_id`` (an admission refusal: this attempt never claimed the item) it never
    touches a 'dispatched' claim or a 'user_decision' hand-off at all -- a claim is
    written inside the issue builder BEFORE the run is registered live, so a liveness
    probe cannot tell a dead claim from one that is just starting (TR0005 rev2 review).
    The reads below only pick the early exits; the write itself is one CAS
    (``fail_responder``: answer_count = 0, plus the owner's 'dispatched' claim with
    ``run_id``, or the very state/run the caller read without it), so an answer or a new
    claim committed after these reads is never relabelled 'failed'. Returns whether the
    state changed (and was announced).
    """
    item = db_question_items.get_by_pk(item_id)
    if item is None or int(item.get("answer_count") or 0) > 0:
        return False
    observed_state = item.get("responder_state")
    if run_id is None and observed_state in (
        db_question_items.RESPONDER_DISPATCHED, db_question_items.RESPONDER_USER_DECISION,
    ):
        return False
    changed = db_question_items.fail_responder(
        item_id, error_code, _clip_message(message), run_id=run_id,
        observed_state=observed_state, observed_run_id=item.get("responder_run_id"),
    )
    if not changed:
        return False
    container = db_questions.get_by_pk(item["question_id"]) or {}
    _announce_responder_state(
        container, item,
        state=db_question_items.RESPONDER_FAILED, error_code=error_code,
        message=_clip_message(message), run_id=run_id or item.get("responder_run_id"),
        actor_user_id=actor_user_id, notify_audience=notify_audience,
    )
    return True


def escalate_to_user(
    doc_id: str,
    item_id: int,
    reason: str,
    *,
    writer_ai_run_id: Optional[str],
    actor_user_id: Optional[str] = None,
    notify_audience: Optional[str] = None,
) -> dict:
    """The assigned AI responder says a HUMAN must decide this question (0661 T0004 F5).

    A separate signal from an answer POST: no answer row is written, answer_count stays 0,
    the container stays 'pending' and the parked chain stays parked. Only the responder
    run that was dispatched for this very item (``question_items.responder_run_id``) may
    send it — a work AI's own token, a copied mention or a session cannot — and an item
    that already has an answer cannot be escalated. Idempotent for a repeated signal
    from the same run.
    """
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason must not be empty")
    container = db_questions.get_container_by_doc(doc_id)
    if container is None:
        raise HTTPException(status_code=404, detail=f"Question container for {doc_id} does not exist")
    item = db_question_items.get_by_pk(item_id)
    if item is None or item["question_id"] != container["id"]:
        raise HTTPException(
            status_code=404,
            detail=f"question_item {item_id} does not belong to document {doc_id}",
        )
    if not writer_ai_run_id or item.get("responder_run_id") != writer_ai_run_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only the AI responder run dispatched for this question may hand it to a "
                "user."
            ),
        )
    if int(item.get("answer_count") or 0) > 0:
        raise HTTPException(status_code=409, detail="This question already has an answer.")
    message = _clip_message(reason)
    if item.get("responder_state") == db_question_items.RESPONDER_USER_DECISION:
        return {
            "doc_id": doc_id, "item_id": item_id,
            "state": db_question_items.RESPONDER_USER_DECISION, "status": container["status"],
            "already": True,
        }
    # One CAS (TR0005 rev1 review): only this run's 'dispatched' claim on a still
    # unanswered item becomes 'user_decision' — a person's answer that committed after the
    # checks above wins and the hand-off is refused instead of overwriting 'answered'.
    changed = db_question_items.escalate_responder(
        item_id, writer_ai_run_id, RESPONDER_ERROR_USER_DECISION, message,
    )
    if not changed:
        raise HTTPException(
            status_code=409,
            detail=(
                "This question was answered or its responder state changed concurrently; it "
                "was not handed to a user."
            ),
        )
    _announce_responder_state(
        container, item,
        state=db_question_items.RESPONDER_USER_DECISION,
        error_code=RESPONDER_ERROR_USER_DECISION, message=message, run_id=writer_ai_run_id,
        actor_user_id=actor_user_id, notify_audience=notify_audience,
    )
    return {
        "doc_id": doc_id, "item_id": item_id,
        "state": db_question_items.RESPONDER_USER_DECISION, "status": container["status"],
        "already": False,
    }


def is_question_responder_run(run_id: Optional[str]) -> bool:
    """Was ``run_id`` dispatched as an answer-only responder for some question item?

    Durable (question_items.responder_run_id), so it holds across a restart and for a
    manual [AI 답변 요청] run alike. False on lookup failure — the caller uses it to
    deny a responder a second kind of write, and an unknown run keeps the ordinary rules.
    """
    if not run_id:
        return False
    try:
        return bool(db_question_items.list_by_responder_run(run_id))
    except Exception:
        logger.warning("responder run lookup failed for %s", run_id, exc_info=True)
        return False


def startup_recover_question_responders() -> int:
    """Settle responder items whose run died with the previous process (0661 T0004 F3).

    At startup nothing is live, so every item still 'dispatched' belongs to a run that
    never finalized. An item that received an answer meanwhile is closed as 'answered';
    the rest become a visible 'failed / interrupted' the panel can re-dispatch — instead
    of a 'dispatched' that would block the next dispatch forever. Returns the number of
    rows settled.
    """
    try:
        rows = db_question_items.list_dispatched()
    except Exception:
        logger.warning("question responder startup sweep could not read items", exc_info=True)
        return 0
    settled = 0
    for row in rows:
        run_id = row.get("responder_run_id")
        try:
            from modules.flow_gate.services.ai_invoke import runtime as ai_runtime

            if run_id and ai_runtime.is_run_live(run_id):
                continue
            if int(row.get("answer_count") or 0) > 0:
                db_question_items.set_responder_state(
                    row["id"], db_question_items.RESPONDER_ANSWERED, None, None, run_id=run_id,
                )
                settled += 1
                continue
            if mark_responder_failed(
                row["id"], "interrupted",
                f"Responder run {run_id or '(unknown)'} was lost to a server restart before it "
                "registered an answer. Use [AI 답변 요청] on the question to dispatch it again.",
                run_id=run_id,
            ):
                settled += 1
        except Exception:
            logger.warning("question responder startup sweep failed for item %s",
                           row.get("id"), exc_info=True)
    if settled:
        logger.warning("[q_service] startup settled %d interrupted question responder(s)", settled)
    return settled


# ── Lookup ───────────────────────────────────────────────────────────────────────

def _parse_selected_options(answer: dict) -> list[str]:
    """Stored selected_options JSON → list of option ids. Unparseable → [] (L0008 §5)."""
    raw = answer.get("selected_options")
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [o for o in parsed if isinstance(o, str)] if isinstance(parsed, list) else []


def _provider_view(run_id: Any, provider_id: Any, provider_name: Any) -> Optional[dict]:
    """Nest one AI run/provider snapshot into the canonical public shape (0582 T0005 §6).

    None when there is no evidence at all — a human item, or an AI item whose token
    carried no bound run (legacy row / [Copy Mention] hand-off) — so the UI can render
    an explicit "external/unconfirmed" label instead of a fabricated provider name.
    """
    if not run_id and not provider_id and not provider_name:
        return None
    return {"ai_run_id": run_id, "ai_provider_id": provider_id, "ai_provider_name": provider_name}


def _responder_view(item_dict: dict) -> Optional[dict]:
    """Pop the migration-142 responder columns into one nested block (0661 T0004 F3/F5).

    None when no in-app responder ever touched the item (legacy rows, human-only Q&A), so
    the UI shows nothing rather than an empty state machine.
    """
    state = item_dict.pop("responder_state", None)
    run_id = item_dict.pop("responder_run_id", None)
    requested = item_dict.pop("responder_requested_provider_id", None)
    source = item_dict.pop("responder_provider_source", None)
    code = item_dict.pop("responder_error_code", None)
    message = item_dict.pop("responder_error_message", None)
    attempts = item_dict.pop("responder_attempts", None)
    updated_at = item_dict.pop("responder_updated_at", None)
    if not state:
        return None
    return {
        "state": state,
        "run_id": run_id,
        "requested_provider_id": requested,
        "provider_source": source,
        "error_code": code,
        "error_message": message,
        "attempts": int(attempts or 0),
        "updated_at": updated_at,
    }


def _answer_provenance_view(answer_dict: dict) -> Optional[dict]:
    """Pop the requested-provider half of an AI answer's evidence (0661 T0004 F6).

    `author_provider` (above) keeps its 0582 shape — the ACTUAL provider. This sibling
    says what was ASKED for and whether a fallback ran, so a row can prove on its own
    that the assigned reviewer (or not) answered. None when there is no evidence.
    """
    requested = answer_dict.pop("author_requested_provider_id", None)
    source = answer_dict.pop("author_provider_source", None)
    fallback = answer_dict.pop("author_fallback_used", None)
    if requested is None and source is None and fallback is None:
        return None
    return {
        "requested_provider_id": requested,
        "provider_source": source,
        "fallback_used": None if fallback is None else bool(fallback),
    }


def get_qa_detail(doc_id: str) -> dict:
    """The document's query container + items + answers tree. Empty structure if no container.

    Returns {doc_id, status, items: [{...item, options: [{id, label}], answers: [...]}]}.
    options / selected_options are handed to the UI parsed, never as the stored JSON text.
    Each AI item (asker_kind/author_kind='ai') nests its raw asker_*/author_* columns
    into ``asker_provider``/``answer.provider`` (0582 T0005 §D) — the same
    {ai_run_id, ai_provider_id, ai_provider_name} shape every other AI-provenance
    surface uses, rather than exposing the raw column names to the API.
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
        item_dict["options"] = _parse_options(item)
        item_dict["asker_provider"] = _provider_view(
            item_dict.pop("asker_ai_run_id", None),
            item_dict.pop("asker_actual_provider_id", None),
            item_dict.pop("asker_actual_provider_name", None),
        )
        item_dict["responder"] = _responder_view(item_dict)
        answers = []
        for answer in db_answers.list_by_question_item(item["id"]):
            answer_dict = dict(answer)
            answer_dict["selected_options"] = _parse_selected_options(answer)
            answer_dict["author_provider"] = _provider_view(
                answer_dict.pop("author_ai_run_id", None),
                answer_dict.pop("author_actual_provider_id", None),
                answer_dict.pop("author_actual_provider_name", None),
            )
            answer_dict["author_provenance"] = _answer_provenance_view(answer_dict)
            answers.append(answer_dict)
        item_dict["answers"] = answers
        result["items"].append(item_dict)
    return result


def get_answers_for_document(doc_id: str) -> list[dict]:
    """Array of Q&A pairs for document doc_id (legacy document_routes compatibility).

    Returns [{"Q": item_body, "A": latest_answer_body_or_null}, ...] / [] if none.
    """
    # 0288 NR0003 finding 5 / recommendation 3: this was 2 + N queries (container, items, then
    # one answers SELECT per item) on the GET /document path, which every worker
    # hits. question_items.qa_bundle_by_doc is the same data as one LEFT JOIN
    # already ordered by (seq, answer created_at), so the whole thing is one
    # query regardless of item count. Rows for an unanswered item carry
    # answer_body = NULL, which is exactly the "A": None this returned before.
    result: list[dict] = []
    for row in db_question_items.qa_bundle_by_doc(doc_id):
        seq = row.get("seq")
        if result and result[-1]["_seq"] == seq:
            # Later row for the same item = later answer (ORDER BY a.created_at).
            result[-1]["A"] = row.get("answer_body")
        else:
            result.append({"_seq": seq, "Q": row.get("body"), "A": row.get("answer_body")})
    return [{"Q": r["Q"], "A": r["A"]} for r in result]


def open_item_snapshot(project_id: Optional[str] = None) -> list[dict]:
    """Read the shared open-question row snapshot, including document title enrichment."""
    return db_questions.list_open_items(project_id)


def project_open_items(rows: list[dict]) -> list[dict]:
    """Project a snapshot to the stable public /q item shape."""
    keys = ("doc_id", "seq", "title", "type_code")
    return [{key: row.get(key) for key in keys} for row in rows]


def list_open_items(project_id: Optional[str] = None) -> list[dict]:
    """Aggregate of 'open queries' (items being answered, D0005 §3.7). project_id=None → all."""
    return project_open_items(open_item_snapshot(project_id))


def qa_bundle_by_doc(doc_id: str) -> list[dict]:
    """Q&A bundle for ment assembly (L0007 §6)."""
    return db_question_items.qa_bundle_by_doc(doc_id)
