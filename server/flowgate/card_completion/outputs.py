"""The required outputs of a card's completion (flowgate.v02.0119 T#4, T-3; 0004-CH seq 9 T-3 · seq 11 교체 3).

A node's ``review -> done`` looks at the card's required outputs only: its result — the node's completion record
(``completion``) — and, when the card uses an instruction, that instruction, approved. Any other document of the card's
lines (a requirement / plan line, a line document a person added beside the outputs) never holds the node: it stays as the
card's history. This replaces the removed card approval's ``pending_documents`` (every approval line's document approved).

    pending_outputs   ``card_completion_ports.pending_outputs`` (result binding / reconciliation, inside the Flow guard):
                      the required outputs of the card of ``node`` other than its result ``result_doc_id`` that are not
                      complete yet — the instruction the card uses when it exists (submitted, or written and never
                      submitted) and is not approved. A line's untouched empty draft is no output, nor a document an ended
                      attempt of the node left behind (``stale``, 0061 T#7).
                      flowgate.v02.0119 T#4 rework (rej_01M6DQW6YF0SCQ5D P2): when the card uses an instruction and no
                      live document of it exists — no instruction line, only its empty draft, only an ended attempt's
                      document — the instruction is missing and the card is not complete: ``MISSING + <type>`` is
                      answered (a required output with no document yet). Only a card that uses no separate instruction is
                      exempt: the instruction omitted (``instruction_type`` None), or a one-stage node whose result is
                      itself of the instruction's type, or a node run with no stage (its attempt ran no instruction
                      stage: a pre-feature person card, an AI card run opened before migration 148 — ``_one_stage``).
    instruction_type  the instruction type the card uses (``card_settings.settings.required_instruction``: the assigned
                      attempt's plan, else the card's stored instruction use), or None

``card_lines_of`` / ``is_target`` / the ``stale`` lines are the removed card approval's line reads, moved here unchanged
(approval-free reads; T-3: "승인 무관 함수는 삭제하지 않고 이전").
"""
from __future__ import annotations

from typing import Optional

from flowgate.card_completion import legacy_adapter as legacy
from flowgate.card_documents import legacy_adapter as card_docs
from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import slots

APPROVED = ("approved", "wf_done")
# A submitted document of a line (an empty draft the card made has no review status yet).
SUBMITTED = ("pending_review", "revised", "rejected") + APPROVED
# flowgate.v02.0119 T#4 rework (P2): a required instruction with no live document — "missing:<type>"
MISSING = "missing:"


def card_lines_of(flow_id: str, node: dict) -> list[dict]:
    """The card's lines of the kind the run's revision gives the node, each with its live document; a line whose
    document was never submitted also tells whether a person wrote it (``written``: a body after the front matter,
    ``card_documents.lines.has_body``), and a document an ended attempt left behind is ``stale`` (0061 T#7)."""
    docs = {d["doc_id"]: d for d in card_docs.flow_documents(flow_id) if d.get("doc_id")}
    stored = card_lines.lines_by_node(flow_id, docs, [node["node_key"]]).get(node["node_key"], [])
    lines = slots.lines_of(node.get("node_type"), node.get("doc_type"), stored)
    stale = legacy.stale_documents(flow_id, node.get("node_key"))
    for line in lines:
        if line.get("doc_id") and line.get("doc_review_status") not in SUBMITTED:
            line["written"] = card_lines.has_body(docs.get(line["doc_id"]) or legacy.document(line["doc_id"]))
        if line.get("doc_id") in stale and line.get("doc_review_status") not in APPROVED:
            line["stale"] = True
    return lines


def is_target(line: dict) -> bool:
    """A line's document exists as an output: submitted, or written and never submitted (pure) — never an ended attempt's
    document (``stale``)."""
    if not line.get("doc_id") or line.get("stale"):
        return False
    return line.get("doc_review_status") in SUBMITTED or bool(line.get("written"))


def instruction_type(flow_id: str, node: dict) -> Optional[str]:
    from flowgate.card_settings import settings  # noqa: PLC0415 — card_settings imports this package on use

    found = settings.required_instruction(flow_id, node)
    return str(found).strip().upper() if found else None


def _one_stage(flow_id: str, node: dict, result_doc_id: Optional[str], wanted: str) -> bool:
    """No separate instruction stands beside the node's result: the result is itself of the instruction's type (a
    card that registers its own type, e.g. a T card's T), or the node run has no stage — its attempt never ran an
    instruction stage: a pre-feature person card, whose person registers the node's result directly
    (``node_dispatch.dispatch_document`` sets a stage for an AI card only), or an AI card run opened before migration 148,
    whose AI was told to write the node's own type (``fields.result_doc_type`` ``one_stage``). An AI card through its
    instruction stage (``instruction`` / ``result``) and an assigned attempt (``stage_assignment`` sets its first stage)
    always have one."""
    result = legacy.document(result_doc_id) if result_doc_id else None
    if result is not None and str(result.get("type_code") or "").strip().upper() == wanted:
        return True
    run = legacy.active_run(flow_id)
    row = legacy.node_run(run["run_id"], node["node_key"]) if run else None
    return row is not None and not row.get("stage")


def pending_outputs(flow_id: str, node: dict, result_doc_id: Optional[str]) -> list[str]:
    """``card_completion_ports.pending_outputs`` handler: the required outputs other than the result not complete yet —
    the live instruction documents the card uses that are not approved, or ``MISSING + <type>`` when the card uses an
    instruction and none of it lives (flowgate.v02.0119 T#4 rework P2: a missing, empty or stale required instruction
    never lets the result complete the card)."""
    if node.get("node_type") == "gate" or not node.get("node_key"):
        return []
    wanted = instruction_type(flow_id, node)
    if not wanted:
        return []                                              # no instruction used: the result alone
    out: list[str] = []
    live = False
    for line in card_lines_of(flow_id, node):
        if line.get("slot") != slots.INSTRUCTION or str(line.get("doc_type") or "").strip().upper() != wanted:
            continue
        doc_id = line.get("doc_id")
        if not doc_id or doc_id == result_doc_id or not is_target(line):
            continue                                           # no document, the result itself, an empty draft, stale
        live = True
        if line.get("doc_review_status") not in APPROVED and doc_id not in out:
            out.append(doc_id)
    if live or _one_stage(flow_id, node, result_doc_id, wanted):
        return out
    return [MISSING + wanted]                                  # the instruction the card uses has no document yet


__all__ = ["APPROVED", "SUBMITTED", "MISSING", "card_lines_of", "is_target", "instruction_type", "pending_outputs"]
