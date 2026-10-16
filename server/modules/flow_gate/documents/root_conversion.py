"""R↔B root conversion: judgement, coordinator and file stage (0064 L0005 2.5 / 2.6 / 2.7, P0006 §3.2 / §3.5).

``judge_root_conversion`` is a pure read: no number reservation, no file, no DB write. Its order is its
result — type, document, root, same type (no-op), disposed, AI run, Flow ownership, workflow decision,
children, target id, target attachment rows, body state, front matter identity.

``execute_root_conversion`` is the one entry point of the route and the v0.1 contact. Inside one Flow guard
transaction (the document's own row lock for a document without a Flow) it reads the document and its
context again, judges again and follows only that judgement: the new body file is staged next to the old
one (``write_new_file``, removed again on rollback), the DB identity is rewritten with the attachment rows
moved before the old row goes, the card lines and the draft cards follow, and the old body file, the old
attachment files and the empty old room are removed only after the outermost commit. Every cleanup removes only
the file it owns (the identity taken when it was read / made) and runs under the durable lock of the guard the
next conversion of the root takes (``flow_guard.run_cleanup``), so a conversion of another server process that
starts right after the commit or the rollback never loses the body or the attachments it staged.

Set 4 (T-B3) adds the succession branch: ``root_superseded`` (``superseded_by``), ``in_succession_chain``
and ``SUCCESSION`` replace the existing child-document refusal kept here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_sequences as db_sequences
from modules.flow_gate.db.connection import after_commit, get_store
from modules.flow_gate.documents import document_service
from modules.flow_gate.documents import front_matter_identity as identity
from modules.flow_gate.documents.attachments import conversion as attachment_conversion
from modules.flow_gate.documents.attachments.registry import registry_list
from modules.flow_gate.documents.conversion_errors import ConversionRefused
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.storage.body import body_read_state
from modules.flow_gate.storage.files import (
    file_identity, referenced_by_any_document, remove_owned_file, write_new_file,
)

_log = logging.getLogger(__name__)

ROOT_TYPES = ("R", "B")
NUMBER_SWAP, SUCCESSION = "number_swap", "succession"
REFUSED, NOOP = "refused", "noop"

__all__ = ["ConversionRefused", "ConversionContext", "Judgement", "read_context", "judge_root_conversion",
           "execute_root_conversion", "stage_converted_file", "finish_converted_file"]


@dataclass
class ConversionContext:
    """What the judgement reads besides the document, read by the caller at judgement time."""
    doc_id: str
    disposed: Optional[Exception] = None          # the existing refusal, raised unchanged
    ai_running: Optional[Exception] = None        # the existing mutation-policy refusal
    ownership: str = "unassigned"                 # read_ownership(...).state
    ownership_refusal: Optional[dict] = None      # the v0.1 flat body for graph / conflict


@dataclass
class Judgement:
    kind: str                                     # REFUSED | NOOP | NUMBER_SWAP
    refusal: Optional[Exception] = None
    new_type: Optional[str] = None
    new_id: Optional[str] = None
    mode: Optional[str] = None
    children: int = 0


@dataclass(frozen=True)
class StagedFile:
    path_value: Optional[str]                     # the new row's file_path
    filename: Optional[str]                       # the new row's filename
    staged: Optional[Path] = None                 # the new body file (None: no body file)
    old_abs: Optional[Path] = None
    old_identity: Optional[tuple] = None          # the old body as read: the only file its cleanup removes


def _code(doc_id: str) -> str:
    return doc_id.rpartition(".")[2]


def _refused(exc: Exception) -> Judgement:
    return Judgement(REFUSED, refusal=exc)


def read_context(doc_id: str, doc: Optional[dict]) -> ConversionContext:
    """Disposed, AI run (DB lease) and Flow ownership as they are now — the existing checks, captured."""
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.graph_workflow.services import v01_contact
    from modules.flow_gate.graph_workflow.services.engine_router import read_ownership

    context = ConversionContext(doc_id)
    if doc is None:
        return context
    try:
        routes._reject_if_group_disposed(doc)
    except HTTPException as exc:
        context.disposed = exc
    try:
        routes._reject_if_group_ai_running(doc)
    except Exception as exc:  # noqa: BLE001 — re-raised unchanged when the judgement reaches it
        context.ai_running = exc
    flow_id = doc.get("group_id")
    if flow_id:
        own = read_ownership(flow_id)                 # a failed read stops here, never a guessed state
        context.ownership = own.state
        refusal = v01_contact.ownership_refusal(own, doc.get("doc_id") or doc_id, flow_id)
        context.ownership_refusal = refusal.body() if refusal is not None else None
    return context


def child_count(doc: dict) -> int:
    """The existing child-document query (any status)."""
    group_id = doc.get("group_id")
    if not group_id:
        return 0
    rows = get_store()._fetch_all(
        "SELECT doc_id FROM documents WHERE group_id = ? AND doc_id != ? AND (triggered_by = ? OR target_id = ?)",
        [group_id, doc["doc_id"], doc["doc_id"], doc["doc_id"]],
    )
    return len(rows or [])


def _body_identity(doc: dict) -> tuple[str, Optional[identity.IdentityCheck]]:
    """(body state, identity check of the readable body). Read errors are the caller's to isolate."""
    state, _text = body_read_state(doc)
    if state != "readable":
        return state, None
    path, _located = storage_paths.locate_storage_path(
        (doc.get("file_path") or "").strip(), doc.get("project_id"), doc.get("branch") or "main")
    if path is None:
        raise FileNotFoundError(doc.get("file_path"))
    data = path.read_bytes()
    data.decode("utf-8")                              # checked only; the bytes stay as they are
    return state, identity.check(data, doc.get("type_code") or "", _code(doc["doc_id"]))


def judge_root_conversion(doc: Optional[dict], new_type: Any, context: ConversionContext) -> Judgement:
    """L0005 2.5 — pure read, the order is the result."""
    doc_id = (doc or {}).get("doc_id") or context.doc_id
    t = str(new_type or "").strip().upper()
    if t not in ROOT_TYPES:
        return _refused(HTTPException(
            status_code=422, detail=f"Target type must be one of {sorted(ROOT_TYPES)}; got {t!r}."))
    if doc is None:
        return _refused(HTTPException(status_code=404, detail=f"Document not found: {doc_id}"))
    current = (doc.get("type_code") or "").upper()
    if current not in ROOT_TYPES:
        return _refused(HTTPException(
            status_code=422,
            detail=f"Only workflow root documents (R/B) can be converted; {doc_id} is {current}."))
    if current == t:
        return Judgement(NOOP)
    if context.disposed is not None:
        return _refused(context.disposed)
    if context.ai_running is not None:
        return _refused(context.ai_running)
    if context.ownership in ("graph", "conflict"):
        body = context.ownership_refusal or {"error": "flow_owned_by_graph" if context.ownership == "graph"
                                              else "flow_ownership_conflict", "doc_id": doc_id}
        return _refused(ConversionRefused(body["error"], body=body))
    if db_sequences.get_sequence_by_doc_id(doc_id) is not None:
        return _refused(HTTPException(
            status_code=409, detail="Cannot convert root type after the workflow decision was taken."))
    # Set 4 (T-B3): ``superseded_by`` -> root_superseded(live_root) is judged here.
    children = child_count(doc)
    # Set 4 (T-B3): in_succession_chain(doc) and the SUCCESSION mode replace this refusal. Until then a
    # root with children keeps the existing refusal, status and detail unchanged.
    if children:
        return _refused(HTTPException(
            status_code=409, detail="Cannot convert root type: child documents already reference it."))
    mode = NUMBER_SWAP
    new_id = document_service.compute_converted_doc_id(doc_id, t)
    if db_docs.get_by_id(new_id) is not None:
        return _refused(HTTPException(status_code=409, detail=f"Target doc_id already exists: {new_id}."))
    if registry_list(new_id):
        return _refused(ConversionRefused(
            "attachment_target_rows_exist", doc_id=doc_id, target_doc_id=new_id, mode=mode))
    try:
        state, found = _body_identity(doc)        # one read per judgement; errors stay inside this boundary
    except (OSError, UnicodeError):
        state, found = "read_error", None         # never read as "empty" or "no identity fields"
    if state == "read_error":
        return _refused(ConversionRefused("root_body_unreadable", doc_id=doc_id, reason="read_error", mode=mode))
    # A number swap of a root without a readable file keeps the existing behaviour (path value only).
    if found is not None and not found.safe:
        return _refused(ConversionRefused(
            "front_matter_identity_unsafe", doc_id=doc_id, reason=found.reason, field=found.field))
    return Judgement(NUMBER_SWAP, new_type=t, new_id=new_id, mode=mode, children=0)


# ── file stage (L0005 2.6) ───────────────────────────────────────────────────

def _converted_path_string(raw: str, old_code: str, new_code: str) -> str:
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    renamed = document_service._converted_filename(base, old_code, new_code)
    return raw[: len(raw) - len(base)] + renamed if renamed else raw


def stage_converted_file(doc: dict, new_id: str, new_type: str, mode: str) -> StagedFile:
    """Prepare the converted body next to the original, which is never touched here."""
    doc_id = doc["doc_id"]
    old_code, new_code = _code(doc_id), _code(new_id)
    filename = document_service._converted_filename(doc.get("filename"), old_code, new_code)
    raw = (doc.get("file_path") or "").strip()
    if not raw:
        return StagedFile(doc.get("file_path"), filename)          # number swap: the value stays as it is
    try:
        old_abs, state = storage_paths.locate_storage_path(raw, doc.get("project_id"), doc.get("branch") or "main")
    except (OSError, UnicodeError):
        raise ConversionRefused("root_body_unreadable", doc_id=doc_id, reason="read_error", mode=mode)
    if old_abs is None:
        # Set 4: a succession refuses here (the body went missing after the judgement).
        return StagedFile(_converted_path_string(raw, old_code, new_code), filename)
    new_name = document_service._converted_filename(old_abs.name, old_code, new_code)
    if new_name == old_abs.name:
        new_name = f"{new_code}_{old_abs.name}"                    # a name that does not start with the code
    new_abs = old_abs.parent / new_name
    old_identity = file_identity(old_abs)
    try:
        data = old_abs.read_bytes()
        data.decode("utf-8")
        data = identity.rewrite(data, doc.get("type_code") or "", old_code, new_type, new_code)
    except identity.IdentityUnsafe as exc:
        raise ConversionRefused("front_matter_identity_unsafe", doc_id=doc_id, reason=exc.check.reason,
                                field=exc.check.field)
    except (OSError, UnicodeError):
        raise ConversionRefused("root_body_unreadable", doc_id=doc_id, reason="read_error", mode=mode)
    write_new_file(new_abs, data)                                  # removed again if anything rolls back
    if doc.get("filename") == old_abs.name:
        filename = new_abs.name
    return StagedFile(storage_paths.to_storage_relative(new_abs, doc.get("project_id")), filename,
                      new_abs, old_abs, old_identity)


def _remove_old_body(old_abs: Path, old_identity: Optional[tuple]) -> None:
    """Under the durable lock: a path another row uses, or that holds another file by now, is kept."""
    try:
        if referenced_by_any_document(old_abs):
            _log.info("old body kept, still referenced: %s", old_abs)
            return
        remove_owned_file(old_abs, old_identity)
    except Exception:  # noqa: BLE001 — committed already; the old file is referenced by no row
        _log.warning("old body cleanup failed: %s", old_abs, exc_info=True)


def _run_cleanup(key, body, what: str) -> None:
    from modules.flow_gate.graph_workflow.services.flow_guard import run_cleanup

    try:
        run_cleanup(key, body, what)
    except Exception:  # noqa: BLE001 — committed already; a file left behind is an unreferenced orphan
        _log.warning("%s failed", what, exc_info=True)


def finish_converted_file(staged: StagedFile, mode: str, doc_id: str, cleanup_key) -> None:
    """Number swap only: queue removing the old body after the outermost commit.

    ``cleanup_key`` is the guard of the document as committed — the one the next conversion takes."""
    if mode != NUMBER_SWAP or staged.staged is None:
        return
    old_abs, old_identity = staged.old_abs, staged.old_identity
    if not after_commit(lambda: _run_cleanup(cleanup_key, lambda: _remove_old_body(old_abs, old_identity),
                                             f"old body cleanup {old_abs}")):
        raise ConversionRefused("transaction_required", doc_id=doc_id)


# ── coordinator (L0005 2.6) ──────────────────────────────────────────────────

def execute_root_conversion(doc_id: str, new_type: Any, actor: Optional[str]) -> dict:
    """The route's and the v0.1 contact's one entry point: ``{"data": row}`` plus ``conversion`` when it ran.

    The boundary is the document's guard (its Flow's, or its own row lock without a Flow), shared with the
    attachment writes. Called inside a transaction that is not that guard, it refuses before anything is
    read or staged (``transaction_required``)."""
    from modules.flow_gate.graph_workflow.services.flow_guard import GuardNotHeld, run_in_document_guard

    try:
        return run_in_document_guard(doc_id, lambda: _execute_in_guard(doc_id, new_type, actor))
    except GuardNotHeld:
        raise ConversionRefused("transaction_required", doc_id=doc_id)


def _execute_in_guard(doc_id: str, new_type: Any, actor: Optional[str]) -> dict:
    doc = db_docs.get_by_id(doc_id)
    judged = judge_root_conversion(doc, new_type, read_context(doc_id, doc))
    if judged.kind == REFUSED:
        raise judged.refusal
    if judged.kind == NOOP:
        return {"data": doc}
    try:
        return _convert_by_number_swap(doc, judged, actor)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Conversion failed: {exc}") from exc


def _convert_by_number_swap(doc: dict, judged: Judgement, actor: Optional[str]) -> dict:
    """L0005 2.7: number known (no reservation) -> target rows empty -> body staged -> attachments staged ->
    new row, references, attachment rows, old row -> card lines -> draft cards -> cleanup queued."""
    from modules.flow_gate.graph_workflow.services import v01_contact
    from modules.flow_gate.graph_workflow.services.flow_guard import document_guard_key

    old_id, new_id, mode = doc["doc_id"], judged.new_id, judged.mode
    old_type, new_type = (doc.get("type_code") or "").upper(), judged.new_type
    attachment_conversion.require_target_empty(old_id, new_id, mode, "number_confirmed")
    staged = stage_converted_file(doc, new_id, new_type, mode)
    stage = attachment_conversion.stage_attachment_room(doc, new_id, staged.path_value, mode)
    inserted = document_service.convert_by_number_swap(
        doc, new_id, new_type, staged.path_value, staged.filename, actor,
        before_delete=lambda: attachment_conversion.reassign_attachment_rows(doc, new_id, stage, mode),
        note_suffix=(f" mode={mode} children=0 attachments={stage.reassigned_count}/0"
                     f" ghosts={stage.ghost_count}"),
    )
    draft_node_keys = v01_contact.follow_root_conversion(doc, inserted, old_type=old_type, new_type=new_type,
                                                         actor=actor)
    cleanup_key = document_guard_key(inserted, new_id)          # the guard the next conversion of the root takes
    finish_converted_file(staged, mode, old_id, cleanup_key)
    attachment_conversion.finish_attachment_room(stage, old_id, cleanup_key)
    _log.info("root converted mode=%s %s -> %s children=0", mode, old_id, new_id)
    return {
        "data": inserted,
        "conversion": {
            "mode": mode, "old_doc_id": old_id, "new_doc_id": new_id, "old_type": old_type, "new_type": new_type,
            "child_count": 0,
            "attachments": {"count": stage.reassigned_count, "ghosts": stage.ghost_count},
            "draft_node_keys": list(draft_node_keys),
        },
    }
