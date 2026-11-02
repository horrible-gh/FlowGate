"""Reuse boundary of the card completion (flowgate.v02.0119 T#2, T-1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed: the Flow's engine
ownership, the Flow guard (one DB transaction under the Flow lock), the active run and its node runs and active attempts,
a run step's context and its post-commit jobs, the run's policies, the result binder's per-node reconciliation (the one
path that turns a missing node event into ``resolve`` -> ``apply_decision``; the frontier ignores an invalid or repeated
event, L0009 E-20 / E-36), the document row and its review verdicts.

Listed for the directory move (WP 부록 A): ``graph_workflow.services.result_binder`` / ``run_apply`` / ``flow_guard``,
``graph_workflow.store.run_store``, ``db.documents``, ``db.document_reviews``, ``workflow_events`` (a submission's author),
``services.ai_invoke.runtime.get_run_record`` (the provider a review run actually ran on).
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.graph_workflow.domain.run_policies import policies_of
from modules.flow_gate.graph_workflow.services import result_binder, run_apply
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
from modules.flow_gate.graph_workflow.services.flow_guard import held_guard, with_flow_guard
from modules.flow_gate.graph_workflow.store import run_store

__all__ = ["graph_owned", "project_of", "active_run", "run_by_id", "node_run", "active_attempt_id", "document",
           "reviews_of", "guarded", "in_guard", "in_transaction", "transaction", "reconcile_node", "run_jobs",
           "current_attempt_id", "attempt_created_at", "review_run_provider", "submission_author"]

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
