"""The automatic approval of a card's instruction (flowgate.v02.0119 T#4, T-3; 0004-CH seq 9 T-3 · seq 11 교체 3).

An instruction is a required output of its card's completion (``outputs.pending_outputs``), never a node's result: its
approval records no completion row and binds no node event. It replaces the removed card approval's document approval
(``card_approval.legacy_adapter.approve_document``, the v0.1 review transition under the run issuer's name, the TR approval
hook and the Graph contact): the v0.2 writes the instruction's approved display itself, by the system
(``legacy_adapter.write_instruction_approved``) —

- the instruction first passes the read-only gates the v0.1 approval asked (``gates``): an empty or unreadable body is
  refused (``InstructionRefused`` ``body_refused``), and a TS's specification contract too — a contract v2 TS with
  validation errors is refused (``test_gate_refused``); nothing is written;
- the write checks the revision before and after it in one transaction (``expected_revision``: the revision a review
  passed — none given, the revision read here); a revision moved meanwhile writes nothing (``RevisionMoved``);
- a written, never submitted instruction opens as an inbox submission would (the v0.1 rule names the next state);
- the event names the system as the actor and the Graph run's issuer as ``requested_by`` only; no person is recorded as
  an approver, no ``transition_document_review``, no ``tr_commit_service.on_document_approved``, no ``after_review_decided``.

flowgate.v02.0119 T#4 rework (rej_01M6DQW6YF0SCQ5D P1): a TS instruction (a test card's) has an effect of its own the v0.1
approval carried — the admission of its test run: its automated Cases' asset check, its run queued and its TSR opened
(``spec_execution_service.admit_on_approval``). The v0.2 runs it here explicitly (``own_effects.run`` — the same TS effect a
TS result's completion runs): in one transaction under the test admission lock, the approved display (the ``write``) and
the admission commit together or not at all, so there is never an approved TS without its run nor a run without its
approval. A missing or unsafe test asset (``ts_automation_assets``), a run in progress / a failure awaiting its origin
(``ts_run_blocked``) refuse it: nothing is written, and the caller starts no result stage (a system error of the
instruction stage, retried by the reconciliation). Once approved, an approval told again reads it approved
(``ALREADY``) and admits nothing more; two approvals at once are serialized by that lock, the second finding it approved.
A TS with no specification contract (a legacy body) admits nothing and is only approved. Never the v0.1 transition.
"""
from __future__ import annotations

from typing import Optional

from flowgate.card_completion import gates, own_effects
from flowgate.card_completion import legacy_adapter as legacy

APPROVED = ("approved", "wf_done")
ALREADY, WRITTEN = "already", "written"
SYSTEM_RUNNER = "system"                # the TS run's runner when the Graph run names no issuer


class InstructionRefused(RuntimeError):
    def __init__(self, code: str, detail: Optional[str] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


RevisionMoved = legacy.InstructionRevisionMoved


def approve(doc_id: str, *, requested_by: Optional[str], expected_revision: Optional[int] = None) -> str:
    """Approve the card's instruction ``doc_id`` by the system: ``WRITTEN``, or ``ALREADY`` when it reads approved.
    Raises ``InstructionRefused`` (no document, a gate, a TS's admission) or ``RevisionMoved``."""
    doc = legacy.document(doc_id)
    if doc is None:
        raise InstructionRefused("document_missing")
    if doc.get("doc_review_status") in APPROVED:
        return ALREADY
    revision = int(doc.get("revision_no") or 0)
    if expected_revision is not None and int(expected_revision) != revision:
        raise RevisionMoved(doc_id)
    try:
        gates.require_body(doc)
    except gates.GateRefused as exc:
        raise InstructionRefused("body_refused", str(exc)) from exc
    try:
        gates.require_test_gate(doc)                     # a TS's specification contract (no-op for other types)
    except gates.GateRefused as exc:
        raise InstructionRefused("test_gate_refused", str(exc)) from exc

    def write() -> bool:
        return legacy.write_instruction_approved(doc_id, revision, requested_by)

    if own_effects.type_of(doc) != own_effects.TS:
        return WRITTEN if write() else ALREADY
    try:
        written = own_effects.run(doc, flow_id=str(doc.get("group_id") or ""), issuer=requested_by or SYSTEM_RUNNER,
                                  permissions=(), revision=revision, write=write)
    except own_effects.EffectRevisionMoved as exc:
        raise RevisionMoved(doc_id) from exc
    except own_effects.EffectRefused as exc:
        raise InstructionRefused(exc.code, str(exc)) from exc
    return WRITTEN if written else ALREADY


__all__ = ["APPROVED", "ALREADY", "WRITTEN", "InstructionRefused", "RevisionMoved", "approve"]
