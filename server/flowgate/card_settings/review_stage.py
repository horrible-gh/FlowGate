"""The card's review stage (flowgate.v02.0053 T#2): what follows a Graph node's AI result.

A Graph node's AI run writes the result and the result binder puts the node in review. The card then
decides what follows (``fields.after_result``), once that run has ended and released the Flow's group
AI lease (``ai_run_end_hooks``, registered by ``integration``):

- review count not 0: the card's review AI reviews the result with the v0.1 document review loop —
  the same engine the AI invoke dialog starts — with the card's review count and review basis, the
  rework going to the AI that runs the node (``fields.review_loop``). Every review hop, the first one
  included, is told that basis (``fields.review_basis_note``). The review AI is the card's, or
  the project's default reviewer when the card names none;
- when that review loop ends: passed -> the result is approved; any other end (hold, count spent,
  stalled, failure, stop) -> it waits for a person's approval of the card;
- no review -> the result is approved at once.

Both only follow a node's AI run that ended on its own (``ended_cleanly``: end reason ``exited``, no stop
code). A run that was cancelled, timed out, paused, ran out of providers or failed after its result was
bound starts no review and approves nothing: the result waits for a person's approval of the card. This is
the AI run's own end, not the Graph run's [다음 단계] stop — a node AI run that ends cleanly while the Graph
run is stopped is approved as ever (the node is done, only its propagation waits).

flowgate.v02.0056 T#1 (0002-CH turn 5~10): the after review setting (follow / human) is gone and what
follows is always the former follow; a card that stored human no longer stops here, as agreed.

An approval is the v0.1 approval (``legacy_adapter.approve``) followed by the same Graph contact a
person's approval reaches, so the node moves on exactly as if a person had approved it — while the run
is stopped the node is done and only its propagation waits (``frontier.resolve_node_event`` defer).
Only AI nodes read their card (as at dispatch). The card is locked while its review runs (``settings``).

``ai_working`` is the judgement the Graph asks (``card_settings_ports.ai_working``) whether a node in
review is still worked on by AI — its own AI run, or the card's review loop — and so not waiting for a
person's approval (the node's ``awaiting_approval`` and the card approval API share it).

flowgate.v02.0056 T#2 (0002-CH turn 5 / 10 O2): before its result, an AI card with its instruction used runs an
instruction stage (``node_dispatch``). When the AI run of that stage ends (``_after_instruction``):

- it ended on its own (``ended_cleanly``) and the card's instruction is written (``card_documents.lines.has_body``:
  the stage's recorded instruction, its instruction line's document, or an instruction the node's tokens
  registered) -> the instruction is approved at once, with no review — the card's review count is the
  result's alone — and the Graph starts the result stage with that instruction attached;
- otherwise (stopped, failed, nothing written) -> the stage failed: the node records it as an AI start
  failure (``ai_invoke_failed``) and the result stage does not start.

``approve_instruction`` is that automatic approval (also the Graph's ``card_settings_ports.approve_instruction``
handler, for an instruction found written when the stage starts): the card approval's own document approval
(``card_approval``: a written, never submitted instruction is submitted first), under the run issuer's
authority as the Flow's completion context, binding nothing. A refusal leaves the instruction to the card
approval; the result stage still starts.

flowgate.v02.0056 T#3: the card's review and rework hops open with the card's memos turned on for AI reading, as
its instruction and result stages do (``legacy_adapter.start_review_loop``, ``card_memo_hooks``). A single
per-document AI run (``action_scope`` edit, no review loop) on a card's document is no stage of the card: its end
starts no review and approves nothing (``on_run_ended`` follows only a node's instruction stage, a node's ``new``
run and a review loop). A node's ``new`` run reviews or approves only the result it registered itself
(``legacy.node_of_ai_run``): a result a person registered on the node while the run went on (the card line write)
is never reviewed or approved by the run's end; it waits for the card approval.
"""
from __future__ import annotations

import logging
from typing import Optional

from flowgate.card_approval import legacy_adapter as approvals
from flowgate.card_documents import lines as card_lines
from flowgate.card_settings import fields
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import repository, settings

_log = logging.getLogger(__name__)

PASSED = "review_passed"
EXITED = "exited"
APPROVED = ("approved", "wf_done")


def ended_cleanly(run: dict) -> bool:
    """The AI run ended on its own: the worker's end reason is ``exited`` (not cancelled, timeout,
    user_paused, all_providers_failed, worker_error, …) and finalization gave it no stop code (cancelled,
    timeout, user_paused, providers_exhausted, provider_failed, question_pending, an inbox stop, …). The
    outcome alone does not tell it: a run that bound its result and was then stopped still counts it."""
    return run.get("end_reason") == EXITED and not run.get("stop_code")


def on_run_ended(run: dict) -> None:
    """``ai_run_end_hooks.on_run_ended`` handler: a run of a Graph Flow ended and released its lease."""
    flow_id = run.get("group_id")
    if not flow_id or not repository.available() or not legacy.graph_owned(flow_id):
        return
    if run.get("document_review_loop"):
        _after_review(flow_id, run)
    elif run.get("mode") == "single" and run.get("action_scope") in ("new", "edit"):
        stage = legacy.instruction_node_of_ai_run(flow_id, run["run_id"])
        if stage is not None:
            _after_instruction(flow_id, run, *stage)
        elif run.get("action_scope") == "new":
            _after_work(flow_id, run)


def _after_instruction(flow_id: str, run: dict, node: dict, row: dict) -> None:
    """The instruction stage's AI run ended: approve the written instruction and start the result stage, or
    record the stage's failure (flowgate.v02.0056 T#2)."""
    key, issuer = node["node_key"], run.get("issued_to") or ""
    if not ended_cleanly(run):
        _log.info("card instruction stage failed flow=%s node=%s run=%s: run ended %s/%s",
                  flow_id, key, run.get("run_id"), run.get("end_reason"), run.get("stop_code"))
        legacy.instruction_stage_failed(flow_id, row["run_id"], key)
        return
    doc_id = written_instruction(flow_id, node, row)
    if doc_id is None:
        _log.info("card instruction stage failed flow=%s node=%s run=%s: no instruction written",
                  flow_id, key, run.get("run_id"))
        legacy.instruction_stage_failed(flow_id, row["run_id"], key)
        return
    approve_instruction(flow_id, node, doc_id, issuer)
    legacy.start_result_stage(flow_id, row["run_id"], key, doc_id, issuer)


def written_instruction(flow_id: str, node: dict, row: dict) -> Optional[str]:
    """The card's instruction the stage wrote, when it has a body: the one the stage recorded (the line's
    document it wrote into, or the one its tokens registered), its instruction line's document, or — after a
    lost registration contact — the latest instruction the node's tokens registered."""
    found = settings.instruction_document(flow_id, node)
    if not found.get("doc_type"):
        return None
    candidates = [row.get("instruction_doc_id"), found.get("doc_id")]
    candidates += [d["doc_id"] for d in legacy.registered_instructions(flow_id, row["run_id"], node["node_key"],
                                                                       found["doc_type"])]
    for doc_id in dict.fromkeys(c for c in candidates if c):
        if card_lines.has_body(approvals.document(doc_id)):
            return doc_id
    return None


def approve_instruction(flow_id: str, node: dict, doc_id: str, actor_user_id: str) -> bool:
    """Approve the card's instruction ``doc_id`` at once, with no review (``card_settings_ports.approve_instruction``):
    True when it is approved. Without the issuer's ``document.approve``, or on any refusal, it is left to the
    card approval (False)."""
    permissions = approvals.permissions_of(actor_user_id)
    if approvals.APPROVE_PERMISSION not in permissions:
        _log.info("card instruction left to the card approval flow=%s node=%s doc=%s: the issuer lacks %s",
                  flow_id, node.get("node_key"), doc_id, approvals.APPROVE_PERMISSION)
        return False
    doc = approvals.document(doc_id)
    if doc is None:
        return False
    if doc.get("doc_review_status") in APPROVED:
        return True
    try:
        approvals.approve_document(flow_id, doc, actor_user_id, permissions, legacy.LOCALE, system=True)
    except Exception:  # noqa: BLE001 — the instruction waits for the card approval
        _log.warning("card instruction approval refused flow=%s node=%s doc=%s", flow_id, node.get("node_key"),
                     doc_id, exc_info=True)
        return False
    return True


def _after_work(flow_id: str, run: dict) -> None:
    """The node's AI run ended: start the card's review, or approve at once."""
    found = legacy.node_of_ai_run(flow_id, run["run_id"])
    if found is None:
        return                                  # not a node's AI run, or no result of its own bound
    node, doc_id = found
    if not ended_cleanly(run):
        _log.info("card review stage skipped flow=%s node=%s run=%s: run ended %s/%s, awaiting approval",
                  flow_id, node["node_key"], run.get("run_id"), run.get("end_reason"), run.get("stop_code"))
        return                                  # stopped or failed: a person approves the card
    stored = repository.values(flow_id, node["node_key"])
    step = fields.after_result(fields.card_kind(node.get("node_type"), node.get("doc_type")), stored)
    if step == fields.REVIEW:
        project_id = legacy.project_of(flow_id)
        reviewer = stored.get("review_ai") or legacy.default_reviewer(project_id)
        reworker = stored.get("run_ai") or node.get("assignee_provider_id") or run.get("provider_id")
        if not reviewer or not reworker:
            _log.warning("card review not started flow=%s node=%s: no reviewer or rework AI",
                         flow_id, node["node_key"])
            return
        loop = fields.review_loop(stored, reviewer=reviewer, reworker=reworker)
        criteria = loop["review_criteria"]
        legacy.start_review_loop(project_id, flow_id, doc_id, run.get("issued_to") or "", loop,
                                 lambda last: fields.review_basis_note(criteria, last), node["node_key"])
    elif step == fields.APPROVE:
        legacy.approve(flow_id, doc_id, run.get("issued_to") or "")


def _after_review(flow_id: str, run: dict) -> None:
    """A review loop on a node's result ended: a passed result is approved."""
    doc_id = run.get("doc_ref")
    node = legacy.node_of_result(flow_id, doc_id)
    if node is None:
        return
    loop = legacy.review_loop_row(run["run_id"]) or run.get("document_review_loop") or {}
    if loop.get("stop_reason") != PASSED:
        return                                  # hold, count spent, stalled, failure: a person approves
    legacy.approve(flow_id, doc_id, run.get("issued_to") or "")


def review_running(_flow_id: str, _node_key: str, doc_id: Optional[str]) -> bool:
    """A review loop is still working on ``doc_id`` (the node's result)."""
    return bool(doc_id) and legacy.review_loop_active(doc_id)


def ai_working(flow_id: str, node_key: str, doc_id: Optional[str]) -> bool:
    """``card_settings_ports.ai_working`` handler: the node's own AI run has not ended yet (it still
    holds the Flow's group AI lease), or the card's review loop works on its result ``doc_id``."""
    return legacy.node_ai_run_active(flow_id, node_key) or review_running(flow_id, node_key, doc_id)
