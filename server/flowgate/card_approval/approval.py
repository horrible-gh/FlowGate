"""Card approval (flowgate.v02.0056 T#1, 0002-CH turn 5~10): one person's approval for a whole card.

``POST /runs/{run_id}/nodes/{node_key}/approval`` approves every document of the card that is not approved
yet — its approval lines (``card_documents.slots.approval_slots``: N + NR, T / Custom + T + TR, T2 + TR2,
DS + design document, TS + TSR, the R / B requirement, the WP plan), only the documents that really exist
— a line document that was submitted, or one a person wrote in the editor and never submitted (it has a body
after its front matter: ``card_documents.lines.has_body``, the one body judgement of the v0.2 cards, by the
v0.1 approval's own rule); only the card's untouched empty draft (its front matter alone) is no document and
is skipped — through the v0.1 document approval, instruction first and the
node's result last. A written, never submitted document and a rejected result are first put back in review
by the card approval itself, under its own ``document.approve`` (``legacy_adapter._ready``). Each approval
reaches the Graph contact a person's approval reaches, so only the last one — the node's result — is a node
event: ``review_approved``, then the frontier propagates (or, while the run is stopped, the node is done and
its propagation waits). An instruction's approval binds nothing. A gate card keeps its own confirm / reject;
there is no card rejection.

- The card must be awaiting approval (``node_approval``, the judgement the run responses show as
  ``awaiting_approval``); otherwise 409 with the reason (``ai_running``, ``no_result``, ``already_approved``
  …). While another AI run holds the Flow's group AI lease the approval is refused like the v0.1 approve
  button (409 ``group_ai_running``).
- One card approval of a Flow runs at a time (an in-process lock per Flow), checks first and writes after.
  It cannot be one DB transaction: a TR2 approval opens its own (and changes the source), and the Graph
  contact runs the result binder in its own Flow guard. A refusal on the way stops it: the documents
  approved before stay approved — none of them is the node's result, so nothing moved on — and the next
  approval of the card goes on with the rest.
- There is no card approval record: a card is approved when its node is done, and its node is done only
  when every one of those documents is approved (T0004 §4 "역방향"). The Graph asks it through
  ``card_approval_ports.pending_documents`` (``pending_documents`` here, registered by ``integration``):
  while another document of the card is not approved, the result's approval — a person's v0.1 approval of
  the result first, or the card's automatic approval of an AI result — is no node event; the node stays in
  review, awaiting the card's approval, and the approval of the last document, in whichever order and
  through whichever entry point, moves it on.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from flowgate.card_approval import legacy_adapter as legacy
from flowgate.card_documents import legacy_adapter as card_docs
from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import slots

ACTIVE_RUN_STATUSES = ("running", "stopped")
APPROVED = ("approved", "wf_done")
# A submitted document of a line (an empty draft the card made has no review status yet).
SUBMITTED = ("pending_review", "revised", "rejected") + APPROVED
LOCK_WAIT_SEC = 30

_MESSAGES = {
    "node_not_found": ("실행에 이 카드가 없습니다.", "The run has no such card.", "実行にこのカードがありません。"),
    "run_not_active": ("진행 중이거나 멈춘 실행이 아닙니다.", "The run is neither running nor stopped.",
                       "進行中または停止中の実行ではありません。"),
    "node_is_gate": ("확인 카드는 확인 / 반려로 결정합니다.", "A confirm card is decided by confirm / reject.",
                     "確認カードは確認 / 差し戻しで決定します。"),
    "node_not_started": ("아직 시작하지 않은 카드입니다.", "The card has not started yet.", "まだ開始していないカードです。"),
    "no_result": ("카드에 승인할 결과가 아직 없습니다.", "The card has no result to approve yet.",
                  "カードに承認する結果がまだありません。"),
    "ai_running": ("AI가 아직 이 카드를 작업하거나 검수하고 있습니다.", "AI is still working on or reviewing this card.",
                   "AIがまだこのカードを作業またはレビューしています。"),
    "already_approved": ("이미 승인된 카드입니다.", "The card is already approved.", "すでに承認されたカードです。"),
    "node_not_in_review": ("승인 대기 상태가 아닌 카드입니다.", "The card is not awaiting approval.",
                           "承認待ちではないカードです。"),
    "result_changed": ("카드의 결과 문서가 바뀌었습니다. 다시 확인한 뒤 승인하세요.",
                       "The card's result changed. Check it again and approve.",
                       "カードの結果文書が変わりました。確認し直してから承認してください。"),
    "group_ai_running": ("이 Flow에서 AI 실행이 진행 중이라 지금은 승인할 수 없습니다.",
                         "An AI run is in progress in this flow, so it cannot be approved now.",
                         "このフローでAI実行が進行中のため、今は承認できません。"),
    "approval_forbidden": ("문서 승인 권한이 없습니다.", "You do not have permission to approve documents.",
                           "文書を承認する権限がありません。"),
    "document_approval_refused": ("카드 문서를 승인할 수 없습니다.", "A document of the card cannot be approved.",
                                  "カードの文書を承認できません。"),
    "card_approval_busy": ("이 Flow의 다른 카드 승인이 진행 중입니다.", "Another card approval of this flow is in progress.",
                           "このフローの別のカード承認が進行中です。"),
}
_LOCALES = ("ko", "en", "ja")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def message(code: str, locale: str) -> Optional[str]:
    texts = _MESSAGES.get(code)
    return texts[_LOCALES.index(locale) if locale in _LOCALES else 0] if texts else None


def _refuse(status: int, code: str, params: dict, locale: str) -> legacy.GraphApiError:
    return legacy.GraphApiError(status, code, params, message=message(code, locale))


@contextmanager
def _card_lock(flow_id: str, locale: str) -> Iterator[None]:
    with _locks_guard:
        lock = _locks.setdefault(flow_id, threading.Lock())
    if not lock.acquire(timeout=LOCK_WAIT_SEC):
        raise _refuse(409, "card_approval_busy", {"flow_id": flow_id}, locale)
    try:
        yield
    finally:
        lock.release()


def card_lines_of(flow_id: str, node: dict) -> list[dict]:
    """The card's lines of the kind the run's revision gives the node, each with its live document; a line
    whose document was never submitted also tells whether a person wrote it (``written``: a body after the
    front matter, ``card_documents.lines.has_body``)."""
    docs = {d["doc_id"]: d for d in card_docs.flow_documents(flow_id) if d.get("doc_id")}
    stored = card_lines.lines_by_node(flow_id, docs, [node["node_key"]]).get(node["node_key"], [])
    lines = slots.lines_of(node.get("node_type"), node.get("doc_type"), stored)
    for line in lines:
        if line.get("doc_id") and line.get("doc_review_status") not in SUBMITTED:
            line["written"] = card_lines.has_body(docs.get(line["doc_id"]) or legacy.document(line["doc_id"]))
    return lines


def is_target(line: dict) -> bool:
    """A line's document a card approval approves: submitted, or written and never submitted (pure)."""
    return bool(line.get("doc_id")) and (line.get("doc_review_status") in SUBMITTED or bool(line.get("written")))


def targets(node: dict, lines: list[dict], result_doc_id: Optional[str]) -> list[str]:
    """The documents a card approval approves, in order: the approval lines' documents that exist (submitted,
    or written), instruction first, and the node's result last (pure). A line's empty draft is skipped."""
    by_slot = {line["slot"]: line for line in lines}
    out: list[str] = []
    for slot in slots.approval_slots(node.get("node_type"), node.get("doc_type")):
        line = by_slot.get(slot.slot) or {}
        doc_id = line.get("doc_id")
        if doc_id not in out and is_target(line):
            out.append(doc_id)
    if result_doc_id:
        out = [d for d in out if d != result_doc_id] + [result_doc_id]
    return out


def pending_documents(flow_id: str, node: dict, result_doc_id: Optional[str]) -> list[str]:
    """``card_approval_ports.pending_documents`` handler: the card's documents other than its result
    ``result_doc_id`` that are not approved yet, in approval order (instruction first)."""
    if node.get("node_type") == "gate" or not node.get("node_key"):
        return []
    lines = card_lines_of(flow_id, node)
    waiting = {line["doc_id"] for line in lines
               if is_target(line) and line.get("doc_review_status") not in APPROVED}
    return [d for d in targets(node, lines, result_doc_id) if d != result_doc_id and d in waiting]


def approve(group: dict, run_id: str, node_key: str, expected_result: Optional[str], user_id: str,
            locale: str) -> dict:
    """-> 200 ``{"run": <run with node_runs>, "approval": {node_key, target_doc_ids, approved_doc_ids}}``.

    Order of the checks (all before any write):

    1. run missing -> 404 run_not_found; not running / stopped -> 409 run_not_active
    2. ownership not graph -> 409 (as stop / resume / gate decision)
    3. node not in the run -> 404 node_not_found
    4. not awaiting approval -> 409 node_is_gate / node_not_started / no_result / ai_running /
       already_approved / node_not_in_review
    5. ``result_doc_id`` given and not the node's result -> 409 result_changed
    6. another AI run holds the Flow's group AI lease -> 409 group_ai_running
    7. the approver lacks ``document.approve`` -> 403 approval_forbidden
    8. approve the targets in order; a refusal -> 403 approval_forbidden / 409 document_approval_refused
    """
    flow_id = group["group_id"]
    legacy.require_writable(flow_id)
    with _card_lock(flow_id, locale):
        run = legacy.run_by_id(run_id)
        if run is None or run["group_id"] != flow_id:
            raise legacy.GraphApiError(404, "run_not_found", {"run_id": run_id})
        if run["status"] not in ACTIVE_RUN_STATUSES:
            raise _refuse(409, "run_not_active", {"run_id": run_id, "status": run["status"]}, locale)
        legacy.require_graph_owned(flow_id)
        row = legacy.node_run(run_id, node_key)
        if row is None:
            raise _refuse(404, "node_not_found", {"run_id": run_id, "node_key": node_key}, locale)
        node = legacy.revision_node(run, node_key)
        reason = legacy.refusal(flow_id, row, node.get("node_type"))
        if reason is not None:
            raise _refuse(409, reason, {"run_id": run_id, "node_key": node_key, "status": row["status"]}, locale)
        result_doc_id = row["result_doc_id"]
        if expected_result is not None and expected_result != result_doc_id:
            raise _refuse(409, "result_changed", {"run_id": run_id, "node_key": node_key,
                                                  "result_doc_id": result_doc_id}, locale)
        holder = legacy.group_ai_run(flow_id)
        if holder:
            raise _refuse(409, "group_ai_running", {"flow_id": flow_id, "run_id": holder}, locale)
        permissions = legacy.permissions_of(user_id)
        if legacy.APPROVE_PERMISSION not in permissions:
            raise _refuse(403, "approval_forbidden", {"required": legacy.APPROVE_PERMISSION}, locale)
        order = targets(node, card_lines_of(flow_id, node), result_doc_id)
        approved = approve_documents(flow_id, order, result_doc_id, user_id, permissions, locale)
    return {"run": legacy.run_payload(run_id),
            "approval": {"node_key": node_key, "target_doc_ids": order, "approved_doc_ids": approved}}


def approve_documents(flow_id: str, order: list[str], result_doc_id: Optional[str], user_id: str,
                      permissions: set[str], locale: str) -> list[str]:
    """The writes of a card approval (after its checks): approve ``order`` one by one through the v0.1
    approval and its Graph contact; an approved document is skipped, except that an approved result still
    reaches the contact (its node follows it once the documents before it are approved). -> the doc ids
    approved here. A refusal raises with ``approved_doc_ids``: the documents approved before it stay
    approved and the next card approval goes on with the rest."""
    approved: list[str] = []
    for doc_id in order:
        doc = legacy.document(doc_id)
        if doc is None:
            continue
        if doc.get("doc_review_status") in APPROVED:
            if doc_id == result_doc_id:
                legacy.after_approved(flow_id, doc_id)         # approved already: the node follows it
            continue
        try:
            legacy.approve_document(flow_id, doc, user_id, permissions, locale)
        except legacy.GraphApiError as exc:
            exc.message = exc.message or message(exc.code, locale)
            exc.params = {**exc.params, "approved_doc_ids": list(approved)}
            raise
        approved.append(doc_id)
    return approved
