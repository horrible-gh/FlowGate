"""Prompt copy service — §8-1 expansion (D017 r1 §5).

"Copy next-action prompt" feature:
- Automatically includes workflow context + related document links + role + expected output
- References i18n keys (D014)
- Records prompt_copied event (PM decision No.5)

Returns a server-assembled string.
Clipboard copy is handled on the frontend.
"""
from __future__ import annotations

import json
from typing import Any

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import workflow_sequences as db_ws
from modules.flow_gate.db import workflow_item_results as db_wir
from modules.flow_gate.db.document_type_labels import get_type_name
from modules.flow_gate.storage.paths import resolve_storage_path

from .event_logger import log_prompt_copied


# ── VR stage — locate the preceding V report path ─────────────────────────────

def _get_v_report_path(doc_id: str) -> str | None:
    """Return the absolute path of the preceding V report for a VR stage.

    1. Look up the sequence by doc_id
    2. Confirm the current head (type == 'VR')
    3. Find the V item in the same sequence with sort_order < VR
    4. Return the registered_path of the latest result

    Returns None if not a VR stage or the path cannot be found.
    """
    seq = db_ws.get_sequence_by_doc_id(doc_id)
    if not seq:
        return None
    head = db_ws.get_effective_head(seq["id"])
    if not head or head.get("type") != "VR":
        return None
    vr_sort = head.get("sort_order", 0)
    items = db_ws.get_sequence_items(seq["id"])
    v_item = next(
        (it for it in items if it.get("type") == "V" and it.get("sort_order", 0) < vr_sort),
        None,
    )
    if not v_item:
        return None
    result = db_wir.get_latest_result_by_item(v_item["id"])
    if not result:
        return None
    registered_path = result.get("registered_path")
    if not registered_path:
        return None
    # registered_path is persisted relative (L0054.0002); this function contracts
    # to return an absolute path that the worker can open, so resolve it.
    resolved = resolve_storage_path(registered_path, seq.get("project_id") or seq.get("project"))
    return str(resolved) if resolved is not None else registered_path


# i18n context labels (doc.type.* removed — T471: replaced by get_type_name())
_I18N_KO: dict[str, str] = {
    "workflow.context.project": "Project",
    "workflow.context.group": "Group",
    "workflow.context.step": "Current Step",
    "workflow.context.role_hint": "Role",
    "workflow.context.expected_output": "Expected Output",
    "workflow.context.next_actions": "Next Action Candidates",
}

# Expected output and next-action hints per document type
# NR102 §4-1 B designates these as "label branch targets", but the type name is
# embedded as part of a composite workflow description string, making a simple
# get_type_name() substitution impossible — full i18n scope is T474.
# (former constant name: NEXT_STEP_LABEL_MAP — renamed to _EXPECTED_OUTPUT in T471)
_EXPECTED_OUTPUT: dict[str, str] = {
    "R": "Review requirements definition, then write AC/RJ or Q",
    "DS": "Write design document (D)",
    "N": "Write investigation report (NR)",
    "T": "Write task report (TR)",
    "TS": "Write test report (TSR)",
    "Q": "Write response (A)",
    "D": "Review design document, then AC/RJ",
    "NR": "Review investigation report, then AC/RJ",
    "TR": "Review task report, then AC/RJ",
    "TSR": "Review test report, then AC/RJ",
}

# NR102 §4-1 B target — role description strings, no type names — T474 scope.
# (former constant name: ROLE_LABEL_MAP — renamed to _ROLE_HINT in T471)
_ROLE_HINT: dict[str, str] = {
    "R": "Manager or Worker",
    "DS": "Worker (Design)",
    "N": "Worker (Investigation)",
    "T": "Worker (Implementation)",
    "TS": "Worker (Testing)",
    "Q": "Manager or Expert",
    "D": "Manager (Review)",
    "NR": "Manager (Review)",
    "TR": "Manager (Review)",
    "TSR": "Manager (Review)",
}


def _label(key: str, locale: str = "ko") -> str:
    """Return a label by i18n key."""
    return _I18N_KO.get(key, key)


def _doc_type_label(type_code: str, locale: str = "en") -> str:
    """Return locale-aware display name for doc type code (T471)."""
    return get_type_name(type_code, locale)


def build_prompt(
    *,
    doc_id: str,
    actor_user_id: str,
    locale: str = "ko",
) -> dict[str, Any]:
    """Assemble the "next-action prompt" based on the document ID and record the event.

    Returns
    -------
    dict with keys:
        doc_id, prompt_text, expected_output_types, context
    """
    doc = db_docs.get_by_id(doc_id)
    if not doc:
        raise ValueError(f"Document not found: {doc_id}")

    group_id = doc.get("group_id")
    project_id = doc.get("project_id", "")
    type_code = doc.get("type_code", "")

    # Group information
    group = db_groups.get_by_id(group_id) if group_id else None
    group_label = f"{group_id} — {group['title']}" if group else (group_id or "")
    group_status = group.get("status", "") if group else ""

    # Retrieve related documents (documents in the same group)
    related_docs: list[dict] = []
    if group_id:
        all_docs = db_docs.list_documents(project_id=project_id, group_id=group_id)
        related_docs = [d for d in all_docs if d.get("doc_id") != doc_id]

    # Assemble the prompt
    lines: list[str] = []
    lines.append(f"[{_label('workflow.context.project')}]")
    lines.append(f"- {_label('workflow.context.project')}: {project_id}")
    lines.append(f"- {_label('workflow.context.group')}: {group_label}")
    lines.append(f"- {_label('workflow.context.step')}: {group_status}")
    lines.append(f"- {_label('workflow.context.role_hint')}: {_ROLE_HINT.get(type_code, 'Assignee')}")
    lines.append("")
    lines.append("[Current Document]")
    lines.append(f"- Document ID: {doc_id}")
    lines.append(f"- Type: {_doc_type_label(type_code, locale)}")
    lines.append(f"- File: {doc.get('file_path', '(none)')}")
    lines.append(f"- Title: {doc.get('title', '')}")
    lines.append(f"- Status: {doc.get('status', '')}")
    lines.append("")

    if related_docs:
        lines.append("[Related Documents]")
        for rd in related_docs[:20]:  # max 20 items
            rd_type = _doc_type_label(rd.get("type_code", ""), locale)
            lines.append(
                f"- [{rd_type}][{rd.get('doc_id')}]: {rd.get('title', '')} "
                f"(status: {rd.get('status', '')})"
            )
        lines.append("")

    expected = _EXPECTED_OUTPUT.get(type_code, "Proceed at assignee's discretion")
    lines.append(f"[{_label('workflow.context.expected_output')}]")
    lines.append(expected)
    lines.append("")
    lines.append(f"[{_label('workflow.context.next_actions')}]")
    _append_next_actions(lines, doc, group)

    # VR stage: automatically include the preceding V report path
    v_report_path = _get_v_report_path(doc_id)
    if v_report_path:
        lines.append("")
        lines.append("## Review Corrections")
        lines.append(f"Apply the review comments from {v_report_path}.")
        lines.append(f"Reference V report: {v_report_path}")

    # group 0022 §6 (R0001-7 / NR0003 risk 4): inject the document's query/answer data so
    # the user's decisions always ride along in the next AI context. Appended just before
    # finalize. Data provider = q_service.qa_bundle_by_doc (DB0006 §5.1).
    _append_qa_block(lines, doc_id)

    prompt_text = "\n".join(lines)

    # Expected output type list
    expected_output_types = _expected_output_types(type_code)

    # Record prompt_copied event
    log_prompt_copied(
        project_id=project_id,
        actor_user_id=actor_user_id,
        doc_id=doc_id,
        document_id=doc.get("id") or 0,
        group_id=group_id,
        template_type=type_code,
        action_context=group_status,
    )

    return {
        "doc_id": doc_id,
        "prompt_text": prompt_text,
        "expected_output_types": expected_output_types,
        "context": {
            "project_id": project_id,
            "group_id": group_id,
            "group_status": group_status,
            "related_docs": [
                {
                    "doc_id": rd.get("doc_id"),
                    "type_code": rd.get("type_code"),
                    "title": rd.get("title"),
                    "status": rd.get("status"),
                }
                for rd in related_docs[:20]
            ],
        },
    }


def _append_qa_block(lines: list[str], doc_id: str) -> None:
    """Append the User Q&A block (D0005 §3.5) for the document, if any.

    Answered = question title + answer body; in-progress = (undecided). Human/AI distinction
    is text-only in the ment. qa_bundle rows: {seq, title, body, asker_kind, options,
    author_kind, answer_body, answer_selected_options}; a question with
    multiple answers yields multiple rows (one per answer), so group by seq.

    An unanswered query with options gets an options line listing them WITH their ids, so a
    worker answering via the ai-request path can echo an id straight back in
    selected_option_ids. An answered one needs no such line — the pick is already in the
    answer body as its label. The machine-readable answer_selected_options stays out of the
    ment entirely: the ment is a human-readable surface (L0008 §2.5).
    """
    try:
        from modules.flow_gate.services import q_service
        rows = q_service.qa_bundle_by_doc(doc_id)
    except Exception:
        return
    if not rows:
        return

    # Group rows by seq (preserve order), collecting answer bodies.
    grouped: dict[int, dict] = {}
    order: list[int] = []
    for r in rows:
        seq = r.get("seq")
        if seq not in grouped:
            grouped[seq] = {
                "title": r.get("title") or r.get("body") or "",
                "options": _parse_ment_options(r.get("options")),
                "answers": [],
            }
            order.append(seq)
        ans = r.get("answer_body")
        if ans:
            grouped[seq]["answers"].append(ans)

    lines.append("")
    lines.append("## 사용자 질의응답")
    for seq in order:
        info = grouped[seq]
        label = info["title"]
        if info["answers"]:
            lines.append(f"- [답변완료] Q{seq} {label}")
            for ans in info["answers"]:
                lines.append(f"    답: {ans}")
        else:
            lines.append(f"- [답변중]   Q{seq} {label}            (미정)")
            if info["options"]:
                shown = " / ".join(
                    f"[{o.get('id')}] {o.get('label')}" for o in info["options"]
                )
                lines.append(f"    보기: {shown}")


def _parse_ment_options(raw: object) -> list[dict]:
    """Stored options JSON → [{"id", "label"}] for the ment. Anything unparseable → [].

    A malformed row (unreachable while the single write gate holds) then just renders in the
    pre-options format rather than costing the whole Q&A block: this is a supplementary block
    already wrapped in a try/except, and failing to assemble it must not block the ment.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [o for o in parsed if isinstance(o, dict) and o.get("id")]


def _append_next_actions(lines: list[str], doc: dict, group: dict | None) -> None:
    """Append next-action candidates based on document type and status."""
    type_code = doc.get("type_code", "")
    status = doc.get("status", "")
    candidates: list[str] = []

    if status == "draft":
        candidates.append("submit — publish to transition to open status")
    elif status == "open":
        candidates.append("approve — approve (requires document.approve permission)")
        candidates.append("reject — reject (requires document.reject permission, reason required)")
        candidates.append("cancel — cancel")
        if type_code in ("R", "B"):
            candidates.append("Copy prompt → send to external AI → register M/Q")
        if type_code in ("DS", "N", "T", "TS"):
            candidates.append("Copy prompt → send to external AI → register output")
    elif status == "rejected":
        candidates.append("resubmit — resubmit (back to open status)")
        candidates.append("redraft — redraft (back to draft status)")
    elif status == "approved":
        candidates.append("close — final closure")

    for c in candidates:
        lines.append(f"- {c}")


def _expected_output_types(type_code: str) -> list[str]:
    """Return the list of expected output type codes for the given document type."""
    mapping: dict[str, list[str]] = {
        "R": ["M", "Q", "DS", "N", "T", "TS"],
        "DS": ["D"],
        "N": ["NR"],
        "T": ["TR"],
        "TS": ["TSR"],
        "Q": ["A"],
        "D": [],
        "NR": [],
        "TR": [],
        "TSR": [],
    }
    return mapping.get(type_code, [])
