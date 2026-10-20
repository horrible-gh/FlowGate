"""Reuse boundary of the work plan deploy (flowgate.v02.0074 T#4, D0006 §2 / §4).

Everything this package takes from ``modules.flow_gate`` goes through here, called only: the WP document row, its
plan body and pre-instruction attachments, the WP save lock, the Flow guard and the document row lock, the Graph's
ownership / run lock / draft save (``graph_service.write_draft``), the document body save rows
(``documents.stage_body_save``), the restorable file replacement, the v0.1 document state and history the document
protection reads, and the Graph API envelope. The card lines and card settings are this repository's own packages
(``flowgate.card_documents`` / ``flowgate.card_settings``).

``replace_file`` and ``before_commit`` are the two seams the failure tests reach (a file replacement that fails, a
failure after the last write): they do nothing else.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import after_commit, after_rollback, get_store
from modules.flow_gate.documents import document_service
from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.domain import draft_request as dr
from modules.flow_gate.graph_workflow.domain.canvas_mapping import is_alive
from modules.flow_gate.graph_workflow.domain.supersede import live_end
from modules.flow_gate.graph_workflow.projection.flow_snapshot import registered_doc_types
from modules.flow_gate.graph_workflow.services import canvas_service, graph_service, run_state_ports
from modules.flow_gate.graph_workflow.services.engine_router import read_ownership, require_graph_editable
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import (
    lock_document_row, with_flow_guard, with_read_snapshot,
)
from modules.flow_gate.graph_workflow.services.node_title_names import PALETTE_KEY_OF_DOC_TYPE, PALETTE_NAMES
from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.services import work_plan_attachment_service as wp_attach
from modules.flow_gate.services import work_plan_instruction_text as instruction_text
from modules.flow_gate.services import work_plan_service as wp
from modules.flow_gate.services.work_plan_locks import plan_save_lock
from modules.flow_gate.storage import files as storage_files
from modules.flow_gate.storage.body import body_path_state, body_read_state

_log = logging.getLogger(__name__)

__all__ = ["GraphApiError", "dr", "envelope", "instruction_text", "plan_save_lock", "registered_doc_types",
           "with_flow_guard", "with_read_snapshot", "lock_document_row", "read_ownership", "require_graph_editable",
           "wp", "wp_attach", "definition_store", "after_commit", "after_rollback", "is_alive", "live_end",
           "PALETTE_KEY_OF_DOC_TYPE", "PALETTE_NAMES"]

DEPLOY_SOURCE = "work_plan_deploy"           # the body save evidence of a line the deploy wrote


# ── the WP document and its Flow ─────────────────────────────────────────────

def document(doc_id: str) -> Optional[dict]:
    return db_documents.get_by_id(doc_id)


def flow_documents(flow_id: str) -> list[dict]:
    return list(db_documents.get_documents_by_group_id(flow_id) or [])


def load_flow(flow_id: str) -> dict:
    return canvas_service.load_flow(flow_id)


def group(flow_id: str) -> Optional[dict]:
    return db_groups.get_by_id(flow_id)


def require_writable(flow_id: str) -> None:
    """409 ``flow_disposed`` for a disposed Flow (the draft save's own check)."""
    graph_service.require_writable(flow_id)


def editable(doc: dict) -> Optional[str]:
    """None while the plan may change, else why not: ``final_approved`` (the group's final approval) or ``status``
    (closed, or a state the WP save refuses) — the WP save's own judgement."""
    final_approved = document_service.is_final_approved(doc)
    if final_approved:
        return "final_approved"
    if doc.get("status") == "closed" or not document_service.is_document_editable(doc, final_approved=False):
        return "status"
    return None


def require_no_group_ai_run(doc: dict) -> None:
    """423 while an AI run holds the group's lease (the body save's and the WP save's rule)."""
    from modules.flow_gate.services.mutation_policy import (  # noqa: PLC0415
        MutationPolicyError, assert_group_mutation_allowed, human_principal,
    )

    try:
        assert_group_mutation_allowed(doc.get("group_id"), human_principal(), "work plan deploy")
    except MutationPolicyError as exc:
        params = {k: v for k, v in exc.error.items() if k not in ("code", "message")}
        raise GraphApiError(exc.status_code, exc.error.get("code") or "group_ai_run_locked", params,
                            message=exc.error.get("message")) from exc


def require_run_editable(flow_id: str) -> None:
    """409 ``graph_run_locked`` (a run running / paused / stopping) or ``graph_time_machine_in_progress``."""
    run_state_ports.require_editable(flow_id)


# ── the plan body ────────────────────────────────────────────────────────────

def plan_path(doc: dict):
    return wp.plan_path_for_doc(doc)


def plan_bytes(doc: dict) -> bytes:
    return plan_path(doc).read_bytes()


def load_plan(doc: dict) -> dict:
    return wp.load_body(plan_path(doc), project_id=doc.get("project_id"), doc_id=doc.get("doc_id"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def card_type_of_step(step: dict) -> Optional[str]:
    return wp.card_type_of_step(step)


def attachment_text(wp_doc_id: str, reference: dict) -> str:
    """The text of a step's pre-instruction file; ``PreInstructionAttachmentError`` when the reference is not the
    registered file with its recorded digest (missing, changed, not text)."""
    code = wp_attach.validate_reference(wp_doc_id, reference)
    if code is not None:
        raise wp_attach.PreInstructionAttachmentError(code, wp_doc_id)
    return wp_attach.read_reference_text(reference)


# ── the Graph definition ─────────────────────────────────────────────────────

def draft(flow_id: str) -> Optional[dict]:
    return definition_store.draft(flow_id)


def latest_revision(flow_id: str) -> tuple[Optional[int], Optional[dict]]:
    latest_no = definition_store.latest_revision_no(flow_id)
    return latest_no, (definition_store.revision(flow_id, latest_no) if latest_no else None)


def write_draft(group_row: dict, user_id: Optional[str], req, registered: set[str]) -> dict:
    return graph_service.write_draft(group_row, user_id, req, None, registered)


def protected_nodes(flow_id: str) -> set[str]:
    """The nodes the Flow's current run keeps as they are (done / open / review)."""
    from flowgate.graph_run.editing import PROTECTED   # noqa: PLC0415

    run = run_store.current_run(flow_id)
    if not run:
        return set()
    return {r["node_key"] for r in run_store.node_runs(run["run_id"]) if r["status"] in PROTECTED}


def node_started(flow_id: str, node_key: str) -> bool:
    """The card ran: a node run of it went past pending in a run of the Flow, an attempt of it was opened, or it is
    open / in review with its AI review working now. Read errors propagate (the protection then holds)."""
    from flowgate.card_settings.legacy_adapter import node_running   # noqa: PLC0415

    store = get_store()
    if store.table_exists("graph_node_runs"):
        row = store._fetch_one(
            "SELECT 1 AS hit FROM graph_node_runs n JOIN graph_runs r ON r.run_id = n.run_id "
            "WHERE r.group_id = ? AND n.node_key = ? AND n.status <> 'pending' LIMIT 1", [flow_id, node_key])
        if row:
            return True
    if store.table_exists("graph_node_attempts"):
        row = store._fetch_one(
            "SELECT 1 AS hit FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
            "WHERE r.group_id = ? AND a.node_key = ? LIMIT 1", [flow_id, node_key])
        if row:
            return True
    return bool(node_running(flow_id, node_key))


# ── document facts for the protection (failures propagate) ──────────────────

def attachment_count(doc_id: str) -> int:
    from modules.flow_gate.documents.attachments.registry import registry_count   # noqa: PLC0415

    return registry_count(doc_id)


def review_count(doc_id: str) -> int:
    """AI reviews of the document (``document_reviews``). Unlike the card line read's display count, a failed read
    raises: an unreadable history protects the document."""
    row = get_store()._fetch_one("SELECT COUNT(*) AS n FROM document_reviews WHERE doc_id = ?", [doc_id])
    return int((row or {}).get("n") or 0)


def rejection_count(doc: dict) -> int:
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history   # noqa: PLC0415

    history = parse_rejection_history(doc.get("rejection_history"))
    return len([item for item in history if item])


_REVIEW_EVENT_MARKS = ("approv", "reject", "submit", "review", "revis", "transition", "state_changed",
                       "action_taken")


def review_event_count(doc: dict) -> int:
    """Approval / rejection / submission / review events of the document: the legacy per-document ``events`` rows and
    the ``workflow_events`` rows that name it."""
    store = get_store()
    count = 0
    for row in store._fetch_all("SELECT event_type FROM events WHERE doc_id = ?", [doc["doc_id"]]) or []:
        kind = str(row.get("event_type") or "").lower()
        if any(mark in kind for mark in _REVIEW_EVENT_MARKS):
            count += 1
    if doc.get("id") is not None:
        for row in store._fetch_all("SELECT event_type FROM workflow_events WHERE document_id = ?",
                                    [doc["id"]]) or []:
            kind = str(row.get("event_type") or "").lower()
            if any(mark in kind for mark in _REVIEW_EVENT_MARKS):
                count += 1
    return count


def body_save_sources(doc_id: str) -> list[Optional[str]]:
    """The ``source`` of each body save evidence of the document (``doc_body_saved``), oldest first."""
    rows = get_store()._fetch_all(
        "SELECT note FROM events WHERE doc_id = ? AND event_type = 'doc_body_saved' ORDER BY event_id", [doc_id])
    out: list[Optional[str]] = []
    for row in rows or []:
        try:
            note = json.loads(row.get("note") or "{}")
        except ValueError:
            note = {}
        out.append(note.get("source") if isinstance(note, dict) else None)
    return out


def body_text(doc: dict) -> tuple[str, Optional[str]]:
    """(read state, text) of the document's file; read errors propagate."""
    return body_read_state(doc)


def approvable_text(doc: dict, text: str) -> bool:
    from modules.flow_gate.workflow.pipeline_service import approvable_text as _approvable   # noqa: PLC0415

    return _approvable(doc, text)


def file_of(doc: dict):
    """The absolute path of the document's file, or None."""
    path, _state = body_path_state(doc)
    return path


def is_disposable_draft(doc: Optional[dict]) -> bool:
    from flowgate.card_documents.slots import is_disposable_draft as _disposable   # noqa: PLC0415

    return _disposable(doc)


# ── writes ───────────────────────────────────────────────────────────────────

file_identity = storage_files.file_identity
is_same_file_as = storage_files.is_same_file_as
FileChanged = storage_files.FileChanged
cleanup_runner = storage_files.cleanup_runner


def replace_file(path, data: bytes, identity: Optional[tuple]):
    """``storage.files.replace_restorably`` (the failure tests replace this seam)."""
    return storage_files.replace_restorably(path, data, identity)


def stage_body_save(doc: dict, relative: str, size: int, user_id: Optional[str]) -> dict:
    """The document row and the body save evidence (source ``work_plan_deploy``) of a line the deploy writes; the
    file itself is replaced later, restorably. No Canvas event of its own: the deploy announces one draft change."""
    from modules.flow_gate.documents.routers.documents import stage_body_save as _stage   # noqa: PLC0415

    return _stage(doc, relative=relative, size=size, actor_user_id=user_id or "", source=DEPLOY_SOURCE,
                  write_file=None, canvas_event=False)


def drop_deploy_save_evidence(doc_id: str) -> int:
    """Delete the body save evidence the deploy itself wrote for ``doc_id`` (inside the caller's transaction)."""
    rows = get_store()._fetch_all(
        "SELECT event_id, note FROM events WHERE doc_id = ? AND event_type = 'doc_body_saved'", [doc_id])
    dropped = 0
    for row in rows or []:
        try:
            note = json.loads(row.get("note") or "{}")
        except ValueError:
            continue
        if isinstance(note, dict) and note.get("source") == DEPLOY_SOURCE:
            dropped += get_store()._execute_affected("DELETE FROM events WHERE event_id = ?", [row["event_id"]])
    return dropped


def storage_relative(path, project_id: Optional[str]) -> str:
    from modules.flow_gate.storage import paths as storage_paths   # noqa: PLC0415

    return storage_paths.to_storage_relative(path, project_id)


def before_commit() -> None:
    """The last point inside the deploy's guard, after every write (a seam for the failure tests)."""


def run_after(callback: Callable[[], Any], *, on_commit: bool) -> None:
    """Queue ``callback`` after the commit (``on_commit``) or after a rollback of the current transaction."""
    queued = after_commit(callback) if on_commit else after_rollback(callback)
    if not queued:
        callback()


def log_warning(message: str, *args: Any) -> None:
    _log.warning(message, *args)
