"""Reuse boundary of the card completion (flowgate.v02.0119 T#2, T-1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed: the Flow's engine
ownership, the Flow guard (one DB transaction under the Flow lock), the active run and its node runs and active attempts,
a run step's context and its post-commit jobs, the run's policies, the result binder's per-node reconciliation (the one
path that turns a missing node event into ``resolve`` -> ``apply_decision``; the frontier ignores an invalid or repeated
event, L0009 E-20 / E-36), the document row and its review verdicts.

Listed for the directory move (WP 부록 A): ``graph_workflow.services.result_binder`` / ``run_apply`` / ``flow_guard``,
``graph_workflow.store.run_store``, ``db.documents``, ``db.document_reviews``, ``workflow_events`` (a submission's author),
``services.ai_invoke.runtime.get_run_record`` (the provider a review run actually ran on).

flowgate.v02.0119 T#4 (T-3): what the removed card approval (``flowgate.card_approval``) took from ``modules.flow_gate`` and
the completion still needs moved here — the card lines an ended attempt left behind (``stale_documents``, 0061 T#7) — with
the read-only gates the v0.1 approval asked (a readable, non-blank body; a TSR's test gate), which are now the v0.2
completion's own judgement (``card_completion.gates``), and the system's write of an approved instruction
(``write_instruction_approved``: no v0.1 review transition, no TR approval hook, no Graph contact). Listed for the directory
move (WP 부록 A): ``graph_workflow.projection.run_documents.registered_documents``, ``workflow.pipeline_service``
(``_require_document_body_for_approval`` / ``_require_test_gate_for_approval`` / ``approvable_text`` /
``AUTO_COMPLETE_TYPES`` / ``FILELESS_APPROVABLE_TYPES`` / ``TransitionError``), ``workflow.transition_rules.get_doc_rule``,
``db.events``.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.graph_workflow.domain.run_policies import policies_of
from modules.flow_gate.graph_workflow.projection.run_documents import registered_documents
from modules.flow_gate.graph_workflow.services import result_binder, run_apply
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.flow_guard import held_guard, with_flow_guard
from modules.flow_gate.graph_workflow.store import run_store

__all__ = ["graph_owned", "project_of", "active_run", "run_by_id", "node_run", "active_attempt_id", "document",
           "reviews_of", "guarded", "in_guard", "in_transaction", "transaction", "reconcile_node", "run_jobs",
           "current_attempt_id", "attempt_created_at", "review_run_provider", "submission_author", "stale_documents",
           "GateRefused", "require_body", "require_test_gate", "approvable_text", "fileless_type",
           "InstructionRevisionMoved", "write_instruction_approved"]

APPROVED = ("approved", "wf_done")
STOPPING = "stopping"


def graph_owned(flow_id: str) -> bool:
    return read_ownership(flow_id).state == "graph"


def project_of(flow_id: str) -> Optional[str]:
    return (db_groups.get_by_id(flow_id) or {}).get("project_id")


def active_run(flow_id: str) -> Optional[dict]:
    return run_store.active_run(flow_id)


def run_by_id(run_id: str) -> Optional[dict]:
    return run_store.run_by_id(run_id)


def node_run(run_id: str, node_key: str) -> Optional[dict]:
    return next((r for r in run_store.node_runs(run_id) if r["node_key"] == node_key), None)


def node_run_by_result(run_id: str, doc_id: str) -> Optional[dict]:
    return run_store.node_run_by_result(run_id, doc_id)


def active_attempt_id(run_id: str, node_key: str) -> Optional[str]:
    if not get_store().table_exists("graph_node_attempts"):
        return None
    return run_store.active_attempt_id(run_id, node_key)


def current_attempt_id(run_id: str, node_key: str) -> Optional[str]:
    """The node's active attempt, else its last ended one (None: no attempt since migration 151)."""
    if not get_store().table_exists("graph_node_attempts"):
        return None
    return run_store.current_attempts(run_id).get(node_key)


def attempt_created_at(attempt_id: Optional[str]) -> Optional[str]:
    if not attempt_id or not get_store().table_exists("graph_node_attempts"):
        return None
    row = get_store()._fetch_one("SELECT created_at FROM graph_node_attempts WHERE attempt_id = ?", [attempt_id])
    return str(row["created_at"]) if row and row.get("created_at") else None


def review_run_provider(review_run_id: Optional[str]) -> Optional[str]:
    """The provider a review run actually ran on (its run record's ``provider_id``, the evidence the review row's
    ``actual_provider_id`` is taken from), or None when the run or its provider is not on record."""
    if not review_run_id:
        return None
    try:
        from modules.flow_gate.services.ai_invoke.runtime import get_run_record  # noqa: PLC0415 — the AI stack on use

        record = get_run_record(str(review_run_id)) or {}
    except Exception:  # noqa: BLE001 — no evidence: the provider is not known
        return None
    provider = record.get("provider_id")
    return str(provider) if provider else None


def _events(group_id: Optional[str], event_type: str) -> list[dict]:
    if not group_id:
        return []
    return [dict(r) for r in get_store()._fetch_all(
        "SELECT id, actor_user_id, document_id, metadata FROM workflow_events WHERE group_id = ? AND event_type = ? "
        "ORDER BY id DESC", [group_id, event_type]) or []]


def _meta(row: dict) -> dict:
    try:
        data = json.loads(row.get("metadata") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def submission_author(doc: Optional[dict]) -> Optional[str]:
    """Who wrote the document's current revision, from its own records — never the run's issuer or the card's assignee:
    the edit event that made the revision (``doc_edited``, ``revision_no``) for a revision after the first, the
    creation event (``doc_created``) or the row's ``owner_id`` for the first. None when nothing on record names one."""
    if not doc:
        return None
    revision = int(doc.get("revision_no") or 0)
    group = doc.get("group_id")
    if revision > 0:
        for event in _events(group, "doc_edited"):
            data = _meta(event)
            if data.get("doc_id") == doc.get("doc_id") and str(data.get("revision_no")) == str(revision) \
                    and event.get("actor_user_id"):
                return str(event["actor_user_id"])
        return None
    for event in _events(group, "doc_created"):
        if doc.get("id") is not None and event.get("document_id") == doc.get("id") and event.get("actor_user_id"):
            return str(event["actor_user_id"])
    owner = doc.get("owner_id")
    return str(owner) if owner else None


def document(doc_id: Optional[str]) -> Optional[dict]:
    return db_documents.get_by_id(doc_id) if doc_id else None


def reviews_of(doc_id: str) -> list[dict]:
    """The document's review verdicts, newest first."""
    return [dict(r) for r in db_reviews.list_by_doc(doc_id) or []]


def guarded(flow_id: str, body: Callable[[], object]):
    return with_flow_guard(flow_id, body)


def in_guard(flow_id: str) -> bool:
    return held_guard() == ("flow", flow_id)


def transaction():
    return get_store().transaction()


def reconcile_node(run: dict, node_key: str, jobs: list) -> None:
    """Inside the Flow guard: the node's missing events, one at a time, through the binder's reconciliation."""
    ctx = run_apply.load_context(run, project_of(run["group_id"]), None, jobs)
    result_binder.reconcile_node(ctx, node_key, policies_of(run_store.policy_entries(ctx.run_id)))


def run_jobs(jobs: list) -> None:
    from modules.flow_gate.graph_workflow.services import run_service  # noqa: PLC0415 — the run stack on use

    run_service.run_jobs(jobs)


# ── flowgate.v02.0119 T#4 (T-3): moved from the removed card approval, and the completion's own gates ──────────────

def stale_documents(flow_id: str, node_key: Optional[str]) -> set[str]:
    """flowgate.v02.0061 T#7 (0017-TR question 440, moved from ``card_approval.legacy_adapter`` by T-3): the card's documents
    an ended attempt of the node left behind — registered (new or edit) with the tokens of the node's invalidated attempts
    in the Flow's active run (a Stop, a lost lease, a failed worktree) and with none of its live tokens. They stay as the
    card's history and are no output the node's completion waits for."""
    if not node_key or not get_store().table_exists("graph_node_attempts"):
        return set()
    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return set()
    old_tokens = run_store.invalidated_token_ids(run_row["run_id"], node_key)
    if not old_tokens:
        return set()
    old = {d["doc_id"] for d in registered_documents(flow_id, old_tokens)}
    live_tokens = run_store.token_ids(run_row["run_id"], node_key)
    live = {d["doc_id"] for d in registered_documents(flow_id, live_tokens)} if live_tokens and old else set()
    return old - live


class GateRefused(ValueError):
    """The document does not pass a read-only gate of the completion (the v0.1 approval's ``TransitionError``)."""


def require_body(doc: dict, locale: str = "ko") -> None:
    """A readable, non-blank body after the front matter (the rule the v0.1 approval asked); raises ``GateRefused``."""
    from modules.flow_gate.workflow import pipeline_service  # noqa: PLC0415 — v0.1 stack on use, called only

    try:
        pipeline_service._require_document_body_for_approval(doc, locale)
    except pipeline_service.TransitionError as exc:
        raise GateRefused(str(exc)) from exc


def require_test_gate(doc: dict, locale: str = "ko") -> None:
    """A TSR's test gate (the rule the v0.1 approval asked); raises ``GateRefused``."""
    from modules.flow_gate.workflow import pipeline_service  # noqa: PLC0415

    try:
        pipeline_service._require_test_gate_for_approval(doc, locale)
    except pipeline_service.TransitionError as exc:
        raise GateRefused(str(exc)) from exc


def approvable_text(doc: dict, text: str) -> bool:
    """A body text already read passes the body gate (the same rule as ``require_body``)."""
    from modules.flow_gate.workflow import pipeline_service  # noqa: PLC0415

    return bool(pipeline_service.approvable_text(doc, text))


def fileless_type(kind: Optional[str]) -> bool:
    """A type the body gate never asked a file of (the auto-complete / fileless approvable types)."""
    from modules.flow_gate.workflow import pipeline_service  # noqa: PLC0415

    kind = str(kind or "").upper()
    return kind in pipeline_service.AUTO_COMPLETE_TYPES or kind in pipeline_service.FILELESS_APPROVABLE_TYPES


class InstructionRevisionMoved(RuntimeError):
    """The instruction's revision is not the one its approval was asked for (its write rolled back)."""


INSTRUCTION_APPROVED_EVENT = "card_instruction_approved"
SUBMIT_ACTION = "submit"


def write_instruction_approved(doc_id: str, revision: int, requested_by: Optional[str]) -> bool:
    """The card instruction's approved display, written by the system in one transaction with its revision checked before
    and after the write: review status ``approved`` (a written, never submitted draft also opens as an inbox submission
    would: the v0.1 rule names the next document state) and a document event naming the system as the actor and the run's
    issuer as ``requested_by``. False when it reads approved already (nothing written); ``InstructionRevisionMoved`` when
    the revision moved. No v0.1 review transition, no TR approval hook, no Graph contact."""
    from modules.flow_gate.db import events as db_events  # noqa: PLC0415
    from modules.flow_gate.db.connection import now_iso  # noqa: PLC0415
    from modules.flow_gate.workflow.transition_rules import get_doc_rule  # noqa: PLC0415

    store = get_store()

    def current() -> Optional[dict]:
        return store._fetch_one("SELECT revision_no, status, doc_review_status FROM documents WHERE doc_id = ?",
                                [doc_id])

    with store.transaction():
        row = current()
        if row is None or int(row["revision_no"] or 0) != int(revision):
            raise InstructionRevisionMoved(doc_id)
        if row.get("doc_review_status") in APPROVED:
            return False
        fields = {"doc_review_status": "approved", "updated_at": now_iso()}
        if not row.get("doc_review_status"):
            rule = get_doc_rule(str(row.get("status") or ""), SUBMIT_ACTION)
            if rule is not None:
                fields["status"] = rule.next_state                 # draft -> open, as an inbox submission
        if not db_documents.update(doc_id, fields):
            raise InstructionRevisionMoved(doc_id)
        db_events.insert_event(doc_id, INSTRUCTION_APPROVED_EVENT, note=json.dumps({
            "actor": "system", "actor_kind": "system", "requested_by": requested_by, "revision_no": int(revision)},
            ensure_ascii=False))
        again = current()
        if again is None or int(again["revision_no"] or 0) != int(revision):
            raise InstructionRevisionMoved(doc_id)
    return True
