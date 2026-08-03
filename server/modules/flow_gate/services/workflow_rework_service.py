"""Workflow rewind domain service shared by HTTP and automatic test rework."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Optional

from modules.flow_gate import process_service
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.documents.constants import AUTO_COMPLETE_TYPES
from modules.flow_gate.services import git_service
from modules.flow_gate.services.mutation_policy import (
    MutationPrincipal,
    assert_group_mutation_allowed,
    system_principal,
)
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.workflow import event_logger

logger = logging.getLogger(__name__)

WORKFLOW_ROOT_TYPES = {"R", "B"}
_RETURN_POINT_NON_RESTORE_TYPES = tuple(
    sorted(WORKFLOW_ROOT_TYPES | {"Q", "AC"} | AUTO_COMPLETE_TYPES)
)
_FINGERPRINT_IGNORED_FRONTMATTER = {
    "doc_review_status",
    "updated_at",
    "created_at",
    "revision_no",
}


def _actor_user_id(actor: str | dict | None) -> str:
    if isinstance(actor, dict):
        return str(actor.get("user_id") or actor.get("id") or "system")
    return str(actor or "system")


def _normalise_markdown_for_fingerprint(content: str) -> bytes:
    text = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end_idx = next(
            (idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"),
            None,
        )
        if end_idx is not None:
            frontmatter = []
            for line in lines[1:end_idx]:
                key = line.split(":", 1)[0].strip().lower()
                if key not in _FINGERPRINT_IGNORED_FRONTMATTER:
                    frontmatter.append(line)
            text = "\n".join(["---", *frontmatter, "---", *lines[end_idx + 1 :]])
    return (text.rstrip() + "\n").encode("utf-8")


def _content_fingerprint(doc: dict) -> Optional[str]:
    try:
        raw_path = (doc.get("file_path") or "").strip()
        if not raw_path:
            return None
        path = storage_paths.resolve_storage_path(
            raw_path,
            doc.get("project_id"),
            branch=(doc.get("branch") or "main"),
        )
        if path is None or not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    return hashlib.sha256(_normalise_markdown_for_fingerprint(raw)).hexdigest()


def _group_workflow_root_doc(doc: dict) -> Optional[dict]:
    if doc.get("type_code") in WORKFLOW_ROOT_TYPES:
        return doc
    roots = db_docs.list_documents(
        project_id=doc.get("project_id"),
        group_id=doc.get("group_id"),
        type_code="R",
        limit=1,
    )
    if not roots:
        roots = db_docs.list_documents(
            project_id=doc.get("project_id"),
            group_id=doc.get("group_id"),
            type_code="B",
            limit=1,
        )
    return roots[0] if roots else None


def _workflow_step_doc_ids(root_doc: Optional[dict]) -> Optional[set[str]]:
    if root_doc is None:
        return None
    sequence = db_wfseq.get_sequence_by_doc_id(root_doc["doc_id"])
    if sequence is None:
        return None
    return {
        item["result_doc_id"]
        for item in db_wfseq.get_sequence_items(sequence["id"])
        if item.get("result_doc_id")
    }


def _record_return_point(
    group_id: str, docs: list[dict], *, root_prev_status: str
) -> Optional[dict]:
    from modules.flow_gate.db import workflow_return_points as db_rp

    existing = db_rp.get_by_group(group_id)
    if existing is not None and db_rp.current_pending_min_seq(existing["id"]) is None:
        db_rp.delete(existing["id"])
        existing = None

    affected = [
        doc
        for doc in docs
        if doc.get("type_code") not in _RETURN_POINT_NON_RESTORE_TYPES
        and (doc.get("seq") or 0) > 0
        and (doc.get("doc_review_status") or "approved") == "approved"
    ]
    if not affected:
        return existing

    front_seq = max(int(doc.get("seq") or 0) for doc in affected)
    point = db_rp.ensure(group_id, front_seq, root_prev_status)
    for doc in affected:
        fingerprint = _content_fingerprint(doc) or ("!" * 64)
        db_rp.add_doc_if_absent(
            return_point_id=point["id"],
            doc_id=doc["doc_id"],
            seq=int(doc.get("seq") or 0),
            prev_status=doc.get("doc_review_status") or "approved",
            fingerprint=fingerprint,
        )
    return db_rp.get_by_group(group_id)


def _return_point_payload(group_id: str) -> dict:
    from modules.flow_gate.db import workflow_return_points as db_rp

    point = db_rp.summary(group_id)
    if point is None:
        return {
            "exists": False,
            "front_seq": None,
            "front_label": None,
            "restorable_count": 0,
            "current_min_seq": None,
            "destination_default": None,
            "destination_min": None,
        }
    current_min = point["current_min_seq"]
    return {
        "exists": True,
        "front_seq": point["front_seq"],
        "front_label": point["front_title"] or point["front_type_code"],
        "restorable_count": point["restorable_count"],
        "current_min_seq": current_min,
        "destination_default": point["front_seq"],
        "destination_min": current_min,
    }


def _archive_ac(doc: dict, *, reason: Optional[str], run_id: Optional[str]) -> None:
    """Invalidate a final-approval row without destroying its audit identity."""
    meta: dict[str, Any] = {}
    try:
        raw_meta = doc.get("meta")
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) and raw_meta else {}
        if not isinstance(meta, dict):
            meta = {}
    except (TypeError, ValueError):
        meta = {}
    meta["workflow_invalidated"] = True
    if reason:
        meta["workflow_invalidated_reason"] = reason
    if run_id:
        meta["workflow_invalidated_run_id"] = run_id
    db_docs.update(doc["doc_id"], {"status": "archived", "meta": json.dumps(meta, ensure_ascii=False)})


def _reopen_in_transaction(
    *,
    doc: dict,
    target_seq: int,
    actor_user_id: str,
    reason: Optional[str],
    run_id: Optional[str],
    preserve_ac: bool,
) -> dict:
    project_id = doc.get("project_id")
    group_id = doc.get("group_id")
    group_docs = db_docs.list_documents(project_id=project_id, group_id=group_id, limit=200)
    root_doc = _group_workflow_root_doc(doc)
    step_ids = _workflow_step_doc_ids(root_doc)

    def is_rewindable_step(candidate: dict) -> bool:
        if candidate.get("type_code") in _RETURN_POINT_NON_RESTORE_TYPES:
            return False
        if step_ids is not None and candidate["doc_id"] not in step_ids:
            return False
        return (candidate.get("seq") or 0) >= target_seq

    root_prev_status = (root_doc or {}).get("doc_review_status") or "wf_in_progress"
    root_was_done = root_prev_status == "wf_done"
    _record_return_point(
        group_id,
        [candidate for candidate in group_docs if is_rewindable_step(candidate)],
        root_prev_status=root_prev_status,
    )

    reopened: list[str] = []
    for candidate in group_docs:
        type_code = candidate.get("type_code")
        if type_code == "AC":
            if preserve_ac:
                _archive_ac(candidate, reason=reason, run_id=run_id)
            else:
                db_docs.delete(candidate["doc_id"])
            continue
        if (
            type_code in WORKFLOW_ROOT_TYPES
            or type_code == "Q"
            or type_code in AUTO_COMPLETE_TYPES
        ):
            continue
        if is_rewindable_step(candidate):
            db_docs.update(candidate["doc_id"], {"doc_review_status": "pending_review"})
            reopened.append(candidate["doc_id"])

    if root_was_done and root_doc is not None:
        db_docs.update(root_doc["doc_id"], {"doc_review_status": "wf_in_progress"})

    metadata: dict[str, Any] = {
        "reopened": reopened,
        "target_seq": target_seq,
        "return_point": _return_point_payload(group_id),
    }
    if reason:
        metadata["reason"] = reason
    if run_id:
        metadata["run_id"] = run_id
    try:
        event_logger.log_event(
            event_type="workflow_reopen",
            project_id=project_id,
            actor_user_id=actor_user_id,
            group_id=group_id,
            document_id=(root_doc or doc).get("id"),
            metadata=metadata,
        )
    except Exception as exc:  # pragma: no cover - audit remains best-effort for manual compatibility
        logger.warning("[workflow reopen] event logging failed: %s", exc, exc_info=True)

    return {"ok": True, "reopened": reopened, "return_point": _return_point_payload(group_id)}


def _rearm_git(project_id: str, group_id: str) -> None:
    try:
        git_service.reopen_group_git(project_id, group_id)
    except Exception as exc:  # pragma: no cover - document transaction has committed
        logger.warning("[workflow reopen] git re-arm failed for %s: %s", group_id, exc, exc_info=True)


def reopen_to_target(
    doc_id: str,
    target_seq: int,
    actor: str | dict | None,
    reason: Optional[str] = None,
    mutation_context: Optional[MutationPrincipal] = None,
    *,
    preserve_ac: bool = False,
    run_id: Optional[str] = None,
    precondition: Optional[Callable[[dict, int], Optional[dict]]] = None,
) -> dict:
    """Run the Time Machine reopen operation for one real workflow slot.

    The caller owns request parsing and user-facing error translation. This function owns
    mutation authorization, the transaction, the audit event, and post-commit git re-arm.
    Automatic callers may supply a precondition that is evaluated in the same transaction
    as the rewind, preventing a stale test completion from racing a newer edit or run.
    """
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"Document not found: {doc_id}")
    project_id = doc.get("project_id")
    group_id = doc.get("group_id")
    if not project_id or not group_id:
        raise ValueError("Document has no project/group")
    principal = mutation_context or system_principal(
        user_id=_actor_user_id(actor), group_id=group_id, run_id=run_id
    )
    assert_group_mutation_allowed(group_id, principal, "workflow reopen")

    resolved_target_seq = int(target_seq)
    with get_store().transaction():
        current_doc = db_docs.get_by_id(doc_id)
        if current_doc is None:
            raise LookupError(f"Document not found: {doc_id}")
        if precondition is not None:
            skipped = precondition(current_doc, resolved_target_seq)
            if skipped is not None:
                return skipped
        result = _reopen_in_transaction(
            doc=current_doc,
            target_seq=resolved_target_seq,
            actor_user_id=_actor_user_id(actor),
            reason=reason,
            run_id=run_id,
            preserve_ac=preserve_ac,
        )
    _rearm_git(project_id, group_id)
    return result


def _skip(ts_doc_id: str, target_seq: Optional[int], run_id: str, reason: str) -> dict:
    return {
        "auto_reopened": False,
        "target_doc_id": ts_doc_id,
        "target_seq": target_seq,
        "run_id": run_id,
        "auto_reopen_skipped": reason,
    }


def _already_reopened_for_run(run_id: str) -> bool:
    row = get_store()._fetch_one(
        "SELECT id FROM workflow_events WHERE event_type = ? AND metadata LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        ["workflow_reopen", f'%"run_id": "{run_id}"%'],
    )
    return row is not None


def auto_reopen_failed_ts(
    ts_doc_id: str,
    target_seq: Optional[int],
    actor_user_id: Optional[str],
    reason: str,
    run_id: str,
    mutation_context: Optional[MutationPrincipal] = None,
) -> dict:
    """Send one current CODE-failed TS through the shared Time Machine reopen path."""
    doc = db_docs.get_by_id(ts_doc_id)
    if doc is None:
        return _skip(ts_doc_id, target_seq, run_id, "ts_document_missing")
    project_id = doc.get("project_id")
    group_id = doc.get("group_id")
    actual_seq = int(doc.get("seq") or 0)
    if not project_id or not group_id or actual_seq <= 0:
        return _skip(ts_doc_id, actual_seq or target_seq, run_id, "ts_context_missing")

    principal = mutation_context or system_principal(
        user_id=actor_user_id or "system", group_id=group_id, run_id=run_id
    )

    def validate_failed_run(current_doc: dict, resolved_target_seq: int) -> Optional[dict]:
        current_seq = int(current_doc.get("seq") or 0)
        current_group_id = current_doc.get("group_id")
        if (
            current_doc.get("project_id") != project_id
            or current_group_id != group_id
            or current_seq <= 0
        ):
            return _skip(ts_doc_id, current_seq or target_seq, run_id, "ts_context_changed")
        if process_service.is_group_disposed(group_id):
            return _skip(ts_doc_id, current_seq, run_id, "group_disposed")

        run = db_test_runs.get_run(run_id)
        if run is None or run.get("doc_id") != ts_doc_id or run.get("status") != "failed":
            return _skip(ts_doc_id, current_seq, run_id, "run_not_terminal_failed")
        runs = db_test_runs.list_by_doc(ts_doc_id)
        if any(candidate.get("status") == "running" for candidate in runs):
            return _skip(ts_doc_id, current_seq, run_id, "newer_run_running")
        terminal = [candidate for candidate in runs if candidate.get("status") != "running"]
        if not terminal or terminal[0].get("run_id") != run_id:
            return _skip(ts_doc_id, current_seq, run_id, "not_latest_terminal_run")
        if int(current_doc.get("revision_no") or 0) != int(run.get("revision_no") or 0):
            return _skip(ts_doc_id, current_seq, run_id, "stale_revision")
        if (
            resolved_target_seq != current_seq
            or (target_seq is not None and int(target_seq) != current_seq)
        ):
            return _skip(ts_doc_id, current_seq, run_id, "sequence_changed")

        sequence = db_wfseq.get_sequence_for_member_doc(ts_doc_id)
        item = db_wfseq.get_item_by_result_doc_id(ts_doc_id)
        if (
            sequence is None
            or item is None
            or int(item.get("sequence_id") or 0) != int(sequence.get("id") or 0)
            or item.get("result_doc_id") != ts_doc_id
        ):
            return _skip(ts_doc_id, current_seq, run_id, "sequence_slot_changed")
        if _already_reopened_for_run(run_id):
            return _skip(ts_doc_id, current_seq, run_id, "duplicate_completion")
        return None

    result = reopen_to_target(
        doc_id=ts_doc_id,
        target_seq=actual_seq,
        actor=actor_user_id,
        reason=reason,
        mutation_context=principal,
        preserve_ac=True,
        run_id=run_id,
        precondition=validate_failed_run,
    )
    if "auto_reopened" in result:
        return result
    return {
        "auto_reopened": True,
        "target_doc_id": ts_doc_id,
        "target_seq": actual_seq,
        "run_id": run_id,
    }