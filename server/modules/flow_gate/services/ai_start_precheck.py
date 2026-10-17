"""AI start body precheck (0064 T-B1e: D0004 §3.6, L0005 2.14 / 2.15, P0006 §2.9 ① / §2.10, DB0007 §2.4).

Before a Flow node AI or a Q&A answer AI starts, the server checks that the documents it hands over can be read:
the start document, the instruction / target document, the reference documents and the documents the card's lines
show (a Q&A answer: the document the question is on, its target in the same group, and the lines of the cards that
show that document -- the cards whose memos the answer reads). Every target is checked, ``PRECHECK_BATCH`` rows at a
time; none is dropped for a limit.

- All readable (or nothing to check): the mention is returned as it is, byte for byte -- no section, no event, no
  Canvas notification.
- Something unreadable: the unreadable documents section (``SECTION_HEADER``, every unreadable document with its
  reason, then the unread-body rules ``mention_service.BODY_UNREADABLE_RULES``) goes in front of the mention,
  behind the card memo section the caller adds afterwards: [memos] [unreadable documents] [mention].
  ``ai_start_unreadable_docs`` is recorded once per AI run (every time without a run: a copied mention) and the
  Flow's Canvas is told (``documents``), so its cards warn through the line read's ``body_state``. Nothing is
  repaired here.
- One document's read failure is that document's ``read_error``; the rest are still checked. A failure of the
  whole check (or of its event / notification) is logged and never stops the AI start: the mention goes out
  without the section.

The callers (``ai_node_starter._issue``, ``q_answer_invoke_service.issue_answer_token``) call this once per token
issue; a retry / reissue of the same run gets the section again but no second event.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterable, Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db.events import insert_event
from modules.flow_gate.graph_workflow.services import card_document_ports
from modules.flow_gate.services.card_memo_hooks import SECTION_SEPARATOR
from modules.flow_gate.storage.body import body_read_state

_log = logging.getLogger(__name__)

PRECHECK_BATCH = 200                         # rows read at a time; every target is checked (L0005 1.1)
EVENT_TYPE = "ai_start_unreadable_docs"      # L0005 1.4 / P0006 §2.10
KIND_NODE, KIND_QNA = "node", "qna"
SECTION_HEADER = "[읽을 수 없는 문서]"
READABLE, READ_ERROR = "readable", "read_error"
#: The server mention wording of each reason (P0006 §1.3).
REASON_TEXT = {
    "file_path_missing": "본문 파일 경로가 기록되지 않음",
    "file_not_found": "본문 파일이 없음",
    "path_unresolved": "본문 파일 위치를 해석할 수 없음",
    READ_ERROR: "본문을 읽는 중 오류",
}


# ── targets ──────────────────────────────────────────────────────────────────

def _distinct(ids: Iterable[Optional[str]]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


def node_targets(job: dict) -> list[str]:
    """A Flow node AI: the start document (the Flow's root), the document it writes into, the reference documents
    and every document the card's lines show (its instruction line included). No limit."""
    return _distinct([job.get("doc_ref"), job.get("target_doc_id"), *(job.get("ref_doc_ids") or []),
                      *card_document_ports.card_line_doc_ids(job.get("flow_id"), node_key=job.get("node_key"))])


def qna_targets(doc: dict) -> list[str]:
    """A Q&A answer AI: the document the question is on, its target document when that is in the same group, and
    every line document of the cards showing it (none for a document no card shows)."""
    doc_id, group_id, target_id = doc.get("doc_id"), doc.get("group_id"), doc.get("target_id")
    ids = [doc_id]
    if target_id and group_id and target_id != doc_id:
        target = db_documents.get_by_id(target_id)
        if target is not None and target.get("group_id") == group_id:
            ids.append(target_id)
    return _distinct([*ids, *card_document_ports.card_line_doc_ids(group_id, doc_id=doc_id)])


# ── the check ────────────────────────────────────────────────────────────────

def _rows(doc_ids: list[str]) -> dict[str, dict]:
    placeholders = ",".join("?" for _ in doc_ids)
    rows = get_store()._fetch_all(f"SELECT * FROM documents WHERE doc_id IN ({placeholders})", list(doc_ids))
    return {row["doc_id"]: row for row in rows}


def precheck(doc_ids: Iterable[str]) -> dict:
    """``{checked, unreadable: [{doc_id, title, reason}]}`` over every target, in their order. A document that is
    gone is not checked; a document whose read fails is ``read_error`` and the check goes on."""
    ids = list(doc_ids)
    checked, unreadable = 0, []
    for start in range(0, len(ids), PRECHECK_BATCH):
        batch = ids[start:start + PRECHECK_BATCH]
        rows = _rows(batch)
        for doc_id in batch:
            doc = rows.get(doc_id)
            if doc is None:
                continue
            checked += 1
            try:
                state, _text = body_read_state(doc)
            except Exception:  # noqa: BLE001 — one document's failure is that document's read_error
                _log.warning("AI start precheck: body read failed %s", doc_id, exc_info=True)
                state = READ_ERROR
            if state != READABLE:
                unreadable.append({"doc_id": doc_id, "title": doc.get("title") or "", "reason": state})
    return {"checked": checked, "unreadable": unreadable}


def unreadable_section(result: dict) -> str:
    """The section for ``precheck``'s result: every unreadable document, then the rules; "" when all are readable."""
    if not result["unreadable"]:
        return ""
    from modules.flow_gate.services.mention_service import BODY_UNREADABLE_RULES   # noqa: PLC0415

    lines = [SECTION_HEADER]
    lines += [f"- {u['doc_id']} ({u['title']}): {REASON_TEXT.get(u['reason'], u['reason'])}"
              for u in result["unreadable"]]
    return "\n".join([*lines, *BODY_UNREADABLE_RULES])


def prepend_section(mention: str, section: str) -> str:
    if not section:
        return mention
    return section + SECTION_SEPARATOR + mention if mention else section


# ── entries ──────────────────────────────────────────────────────────────────

def node_mention(mention: str, job: dict, ai_run_id: Optional[str]) -> str:
    """``ai_node_starter._issue``: the node AI's mention with the unreadable section in front (or as it is)."""
    return _checked(mention, lambda: node_targets(job), kind=KIND_NODE, start_doc_id=job.get("doc_ref"),
                    flow_id=job.get("flow_id"), project_id=job.get("project_id"), ai_run_id=ai_run_id,
                    key=("node_key", job.get("node_key")))


def qna_mention(mention: str, doc: dict, item: dict, ai_run_id: Optional[str]) -> str:
    """``q_answer_invoke_service.issue_answer_token`` (the run and [copy mention] alike): the answer mention with
    the unreadable section in front (or as it is)."""
    return _checked(mention, lambda: qna_targets(doc), kind=KIND_QNA, start_doc_id=doc.get("doc_id"),
                    flow_id=doc.get("group_id"), project_id=doc.get("project_id"), ai_run_id=ai_run_id,
                    key=("item_id", item.get("id")))


def _checked(mention: str, targets: Callable[[], list[str]], *, kind: str, start_doc_id: Optional[str],
             flow_id: Optional[str], project_id: Optional[str], ai_run_id: Optional[str], key: tuple) -> str:
    try:
        result = precheck(targets())
        section = unreadable_section(result)
    except Exception:  # noqa: BLE001 — the check never stops an AI start
        _log.warning("AI start precheck failed kind=%s doc=%s (ignored)", kind, start_doc_id, exc_info=True)
        return mention
    if not section:
        return mention
    _record(result, kind=kind, start_doc_id=start_doc_id, ai_run_id=ai_run_id, key=key)
    _notify(flow_id, project_id)
    return prepend_section(mention, section)


def _already_recorded(start_doc_id: str, ai_run_id: str) -> bool:
    rows = get_store()._fetch_all("SELECT note FROM events WHERE doc_id = ? AND event_type = ?",
                                  [start_doc_id, EVENT_TYPE])
    for row in rows:                                   # the note JSON is read here, never matched with LIKE
        try:
            if json.loads(row.get("note") or "{}").get("ai_run_id") == ai_run_id:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _record(result: dict, *, kind: str, start_doc_id: Optional[str], ai_run_id: Optional[str], key: tuple) -> None:
    """``ai_start_unreadable_docs`` on the start document, once per AI run (DB0007 §2.4: the note JSON)."""
    if not start_doc_id:
        return
    try:
        if ai_run_id and _already_recorded(start_doc_id, ai_run_id):
            return
        note = {"ai_run_id": ai_run_id, "kind": kind, key[0]: key[1], "checked": result["checked"],
                "unreadable": [f"{u['doc_id']}:{u['doc_id'].rsplit('.', 1)[-1]}" for u in result["unreadable"]]}
        insert_event(start_doc_id, EVENT_TYPE, note=json.dumps(note, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — a lost record never stops the start
        _log.warning("AI start precheck: event not recorded doc=%s run=%s", start_doc_id, ai_run_id, exc_info=True)


def _notify(flow_id: Optional[str], project_id: Optional[str]) -> None:
    """The Flow's Canvas reads its lines again (``documents``): the cards warn through the lines' ``body_state``."""
    if not flow_id:
        return
    try:
        from modules.flow_gate.graph_workflow.services.canvas_events import (   # noqa: PLC0415
            CANVAS_REASON_DOCUMENTS, publish_canvas_changed,
        )

        publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason=CANVAS_REASON_DOCUMENTS,
                               actor_user_id=None, origin_client_id=None)
    except Exception:  # noqa: BLE001
        _log.warning("AI start precheck: Canvas notification failed flow=%s", flow_id, exc_info=True)
