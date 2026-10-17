"""Document body diagnosis and recovery (0064 T-B1d; L0005 2.11~2.13, P0006 §2.6~§2.8, DB0007 §2.4).

``diagnose_scope`` only reads: document rows, files, revision backups, review rows and events. It writes no
row, no file and no ``id_counter``. ``recover_one`` is the one judgement behind the single ``regenerate``
route and the bulk ``body-recovery`` route. The checks run once outside the document's guard (an early
refusal) and again inside it on the row read again; then the body state and the diagnosis are taken again,
and only then the row's ``file_path`` changes (DB first) and the new file is written last
(``write_new_file``: an existing file is never overwritten, and the new one goes again on rollback).
Number, title, status, approval and references never change.

A refusal is ``RecoveryRefused``: nothing was changed. The single route answers it with the existing
envelopes (P0006 §2.7), the bulk route carries it as ``refusal`` and goes on with the next document (§2.8).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from modules.flow_gate.storage import paths as storage_paths

_log = logging.getLogger(__name__)

DIAG_SCAN_PAGE = 500
BULK_RECOVERY_MAX = 50
ROOT_TYPES = ("R", "B")

FIX_STORAGE_ROOT = "fix_storage_root"
RELINK_OPPOSITE = "relink_opposite"
RELINK_STANDARD = "relink_standard"
RESTORE_REVISION = "restore_revision"
REGENERATE_CARD_DRAFT = "regenerate_card_draft"
REGENERATE_STUB = "regenerate_stub"
READ_ERROR_MANUAL = "read_error_manual"
METHODS = (FIX_STORAGE_ROOT, RELINK_OPPOSITE, RELINK_STANDARD, RESTORE_REVISION, REGENERATE_CARD_DRAFT,
           REGENERATE_STUB, READ_ERROR_MANUAL)
NOT_RECOVERABLE = frozenset({FIX_STORAGE_ROOT, READ_ERROR_MANUAL})
RELINKS = frozenset({RELINK_OPPOSITE, RELINK_STANDARD})
RESTORED_FROM = {RESTORE_REVISION: "revision", REGENERATE_STUB: "metadata", REGENERATE_CARD_DRAFT: "card_draft",
                 RELINK_OPPOSITE: "relinked_opposite", RELINK_STANDARD: "relinked_standard"}
# L0005 1.3 ``body_lost`` (also the value the ``doc_body_recovered`` note keeps) -> P0006 ``body_loss``.
BODY_LOSS = {"false": "none", "possible": "possible", "true": "lost"}
# ``get_storage_root_info`` source -> the P0006 §2.6 name; the configured value keys of each source.
ROOT_SOURCES = {"environment": "env", "project": "project_setting", "system": "system_setting",
                "default": "default"}
_CONFIGURED_SOURCES = (("system", "system_setting"), ("project", "project_setting"), ("env", "env"),
                       ("default", "default"))
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

DETAILS = {
    "file_exists": "Document file already exists; nothing to regenerate.",
    "read_error": "The document file exists but cannot be read; fix permissions or encoding first.",
    "diagnosis_changed": "The diagnosis changed since it was shown; run the diagnosis again.",
    "storage_root_mismatch": ("The storage root appears to have changed; correct the storage root setting "
                              "instead of recovering this document."),
    "target_in_use": "The recovery target file is used by another document; nothing was changed.",
    "group_ai_running": "The group has a running AI run; try again after it ends.",
    "tr2_not_supported": "TR2 recovery requires its canonical JSON document.",
}
SCOPE_REQUIRED = "Exactly one of group_id or project_id is required."
SELECTION_COUNT_INVALID = f"selections must contain between 1 and {BULK_RECOVERY_MAX} items."
UNKNOWN_METHOD = "Unknown expected_method."


class RecoveryRefused(Exception):
    """A logical refusal of one document's recovery (P0006 §2.7 / §2.8). Nothing was changed.

    ``cause`` is the existing exception of a refusal whose single response stays as it is (disposal 409,
    the AI run lock 423); the single route raises it again.
    """

    def __init__(self, error: str, detail: Optional[str] = None, *, status: int = 409,
                 evidence: Optional[dict] = None, cause: Optional[BaseException] = None):
        super().__init__(error)
        self.error, self.status, self.evidence, self.cause = error, status, evidence, cause
        self.detail = detail if detail is not None else DETAILS.get(error)

    def refusal(self) -> dict:
        return {"error": self.error, "detail": self.detail, "evidence": self.evidence}


def _console():
    """The v0.1 documents router: the checks, paths and stubs recovery has always used live there."""
    from modules.flow_gate.documents.routers import documents as console

    return console


def _meta(doc: dict) -> dict:
    from modules.flow_gate.documents.live_root import meta_dict

    return meta_dict(doc.get("meta"))


def _graph_card(doc: dict) -> Optional[dict]:
    card = _meta(doc).get("graph_card")
    return card if isinstance(card, dict) else None


def _code(doc: dict) -> str:
    return _console()._short_doc_code(doc)


def _type(doc: dict) -> str:
    return str(doc.get("type_code") or "").upper()


def _branch(doc: dict) -> str:
    return (doc.get("branch") or "main") or "main"


# ── storage root (L0005 2.11.6) ──────────────────────────────────────────────

def storage_root_info(project_id: Optional[str]) -> dict:
    """The effective storage root, its source and every configured value (P0006 §2.6 ``storage_root``)."""
    info = storage_paths.get_storage_root_info(project_id)
    return {
        "effective": info["effective"],
        "source": ROOT_SOURCES.get(info["source"], info["source"]),
        "configured": {
            "env": os.environ.get("FLOWGATE_STORAGE_DIR", "").strip() or None,
            "project": info["configured"]["project"],
            "system": info["configured"]["system"],
            "default": str(storage_paths.default_storage_root()),
        },
    }


def _same_root(a: str, b: str) -> bool:
    return os.path.normcase(str(Path(a).resolve(strict=False))) == os.path.normcase(str(Path(b).resolve(strict=False)))


def find_under_other_roots(doc: dict, root: dict) -> Optional[dict]:
    """The stored relative path is a file under another configured root: the root changed, not the document."""
    raw = (doc.get("file_path") or "").strip().replace("\\", "/")
    if raw.startswith("/storage/"):
        raw = raw[len("/storage/"):]
    elif not raw or Path(raw).is_absolute() or _DRIVE_RE.match(raw):
        return None                                   # absolute paths are not checked (L0005 2.11.6)
    seen = [root["effective"]]
    for key, source in _CONFIGURED_SOURCES:
        value = root["configured"].get(key)
        if not value or any(_same_root(value, other) for other in seen):
            continue
        seen.append(value)
        if (Path(value) / raw).is_file():
            return {"root": value, "source": source}
    return None


# ── relink candidates (L0005 2.11.3 / 2.11.5) ────────────────────────────────

def standard_location(doc: dict) -> Path:
    """Where the document's file belongs: a card document where its generator files it (WP included),
    any other document where ``regenerate`` recomputes a missing path."""
    console = _console()
    card = _graph_card(doc)
    if card and card.get("slot"):
        return storage_paths.document_path(
            project_id=doc.get("project_id"), group_code=doc.get("group_id") or "", doc_code=_code(doc),
            filename=f"{card['slot']}.md", module=doc.get("module") or "none",
            branch=console._get_project_branch(doc.get("project_id")),
        )
    return console._regenerate_target_path({**doc, "file_path": ""})


def front_matter_matches(path, doc: dict) -> bool:
    """The file's front matter names this document: its number (required, either spelling), and its group and
    type when they are there. No front matter, an unsafe one or an unreadable file never match: the whole file
    must decode as UTF-8, or the relinked body would be one its reader cannot open."""
    from modules.flow_gate.documents.front_matter_identity import doc_code_alternates, scalars

    try:
        data = Path(path).read_bytes()
        data.decode("utf-8")
    except (OSError, UnicodeError):
        return False
    found = scalars(data, ("doc_number", "group_id", "type"))
    if not found or "doc_number" not in found:
        return False
    if found["doc_number"] not in doc_code_alternates(_code(doc)):
        return False
    if "group_id" in found and found["group_id"] != (doc.get("group_id") or ""):
        return False
    return "type" not in found or found["type"].upper() == _type(doc)


def _recorded_directory(doc: dict) -> Optional[Path]:
    raw = (doc.get("file_path") or "").strip()
    for candidate, inside in storage_paths.storage_path_candidates(raw, doc.get("project_id"), _branch(doc)):
        if inside and candidate.parent.is_dir():
            return candidate.parent
    return None


def opposite_number_candidate(doc: dict) -> Optional[Path]:
    """The one file of the other root number (an R↔B conversion that renamed the file but not the row)."""
    from modules.flow_gate.documents.document_service import compute_converted_doc_id
    from modules.flow_gate.storage.files import referenced_by_any_document

    kind = _type(doc)
    if kind not in ROOT_TYPES:
        return None
    try:
        opposite = compute_converted_doc_id(doc["doc_id"], "B" if kind == "R" else "R")
    except (ValueError, KeyError):
        return None
    prefix = _code({**doc, "doc_id": opposite}) + "_"
    folder = _recorded_directory(doc)
    if folder is None:
        return None
    files = [f for f in folder.iterdir() if f.name.startswith(prefix) and f.is_file()]
    if len(files) != 1 or referenced_by_any_document(files[0]):
        return None
    return files[0] if front_matter_matches(files[0], doc) else None


def standard_location_candidate(doc: dict) -> Optional[Path]:
    from modules.flow_gate.storage.files import referenced_by_any_document

    path = standard_location(doc)
    if not path.is_file() or referenced_by_any_document(path):
        return None
    return path if front_matter_matches(path, doc) else None


# ── card origin and the untouched evidence (L0005 2.11.4) ────────────────────

def _has_rows(table: str, doc_id: str) -> bool:
    from modules.flow_gate.db.connection import get_store

    return get_store()._fetch_one(f"SELECT 1 AS hit FROM {table} WHERE doc_id = ? LIMIT 1", [doc_id]) is not None


def _rejections(doc: dict) -> int:
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history

    history = parse_rejection_history(doc.get("rejection_history"))
    return sum(1 for item in history if isinstance(item.get("reason"), str) and item["reason"].strip())


def body_written(doc_id: str) -> bool:
    """A body was written into this document: a person's save, or a recovery other than an empty card draft.

    The ``doc_body_recovered`` note is parsed here (DB0007 §2.4: no ``LIKE`` on the JSON); an unreadable note
    counts as a write."""
    from modules.flow_gate.db.connection import get_store

    rows = get_store()._fetch_all(
        "SELECT event_type, note FROM events WHERE doc_id = ? AND event_type IN ('doc_body_saved', 'doc_body_recovered')",
        [doc_id])
    for row in rows or []:
        if row["event_type"] == "doc_body_saved":
            return True
        try:
            method = json.loads(row.get("note") or "").get("method")
        except (ValueError, TypeError, AttributeError):
            method = None
        if method != REGENERATE_CARD_DRAFT:
            return True
    return False


def card_origin(doc: dict) -> dict:
    """Whether a card made this document, and whether its empty draft is proven never written since."""
    from modules.flow_gate.documents.live_root import in_succession_chain

    card = _graph_card(doc)
    if card is None:
        return {"card": False}
    doc_id = doc["doc_id"]
    try:
        revision_no = int(doc.get("revision_no") or 0)
    except (TypeError, ValueError):
        revision_no = -1
    unsubmitted = (doc.get("status") == "draft" and not str(doc.get("doc_review_status") or "").strip()
                   and revision_no == 0 and not _has_rows("document_revisions", doc_id)
                   and not _has_rows("document_reviews", doc_id) and _rejections(doc) == 0
                   and not str(doc.get("superseded_by") or "").strip())
    inherited = bool(_meta(doc).get("converted_from")) or in_succession_chain(doc)
    created_empty_known = card.get("created_empty") is True and not inherited
    no_write = not body_written(doc_id)
    proven = unsubmitted and created_empty_known and no_write
    return {"card": True, "node_key": card.get("node_key"), "slot": card.get("slot"),
            "unsubmitted": unsubmitted and no_write, "untouched_proven": proven, "inherited_body": inherited,
            "body_lost": "false" if proven else "possible"}


# ── diagnosis (L0005 2.11.1 / 2.11.2) ────────────────────────────────────────

def read_state(doc: dict) -> str:
    """The body read state; a read error is this document's ``read_error``, never the whole diagnosis's."""
    from modules.flow_gate.storage.body import body_read_state

    try:
        return body_read_state(doc)[0]
    except (OSError, UnicodeError):
        _log.warning("body diagnosis read failed: %s", doc.get("doc_id"), exc_info=True)
        return "read_error"


def diagnose_doc(doc: dict, reason: str, root: dict) -> dict:
    """The recovery method of one unreadable document, in the fixed order of L0005 2.11.2 / 4.3."""
    origin = card_origin(doc)
    found = {"reason": reason, "origin": origin, "candidate": None, "evidence": None, "body_lost": None}
    if reason == "read_error":
        return {**found, "method": READ_ERROR_MANUAL}
    if reason in ("file_not_found", "path_unresolved"):
        hit = find_under_other_roots(doc, root)
        if hit:
            return {**found, "method": FIX_STORAGE_ROOT, "evidence": hit}
    if reason == "file_not_found":
        candidate = opposite_number_candidate(doc)
        if candidate is not None:
            return {**found, "method": RELINK_OPPOSITE, "candidate": candidate, "body_lost": "false"}
    if reason == "file_path_missing":
        candidate = standard_location_candidate(doc)
        if candidate is not None:
            return {**found, "method": RELINK_STANDARD, "candidate": candidate, "body_lost": "false"}
    revision = _console()._latest_revision_body(doc["doc_id"], doc.get("project_id"))
    if revision is not None:
        return {**found, "method": RESTORE_REVISION, "body_lost": "false", "revision": revision}
    if origin["card"] and origin["unsubmitted"]:
        return {**found, "method": REGENERATE_CARD_DRAFT, "body_lost": origin["body_lost"]}
    return {**found, "method": REGENERATE_STUB, "body_lost": "true"}


def _public_origin(origin: dict) -> dict:
    if not origin.get("card"):
        return {"card": False}
    return {key: origin[key] for key in ("card", "node_key", "slot", "unsubmitted", "untouched_proven",
                                         "inherited_body")}


def _summary(found: dict) -> dict:
    """``diagnosis`` of a ``diagnosis_changed`` refusal."""
    return {"reason": found["reason"], "method": found["method"],
            "recoverable": found["method"] not in NOT_RECOVERABLE, "body_loss": BODY_LOSS.get(found["body_lost"])}


def diagnosis_item(doc: dict, found: dict) -> dict:
    candidate = found["candidate"]
    return {
        "doc_id": doc["doc_id"], "code": _code(doc), "title": doc.get("title"), "type": doc.get("type_code"),
        "status": doc.get("status"), "file_path": doc.get("file_path") or "",
        **_summary(found),
        "origin": _public_origin(found["origin"]),
        "candidate": None if candidate is None else {
            "path": Path(candidate).as_posix(),
            "relative": storage_paths.to_storage_relative(candidate, doc.get("project_id"))},
        "evidence": found["evidence"],
    }


def _project_documents(project_id: str):
    from modules.flow_gate.db.connection import get_store

    offset = 0
    while True:
        page = get_store()._fetch_all(
            "SELECT * FROM documents WHERE project_id = ? ORDER BY doc_id LIMIT ? OFFSET ?",
            [project_id, DIAG_SCAN_PAGE, offset]) or []
        yield from page
        if len(page) < DIAG_SCAN_PAGE:
            return
        offset += DIAG_SCAN_PAGE


def diagnose_scope(group_id: Optional[str] = None, project_id: Optional[str] = None) -> dict:
    """P0006 §2.6: every document of a Flow, or of a project read DIAG_SCAN_PAGE rows at a time to the end."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    group_id, project_id = (group_id or "").strip() or None, (project_id or "").strip() or None
    if (group_id is None) == (project_id is None):
        raise HTTPException(status_code=422, detail=SCOPE_REQUIRED)
    if group_id is not None:
        group = db_groups.get_by_id(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail=f"Flow not found: {group_id}")
        project_id = group.get("project_id")
        scope = {"kind": "group", "id": group_id, "project_id": project_id}
        docs = sorted(db_documents.get_documents_by_group_id(group_id) or [], key=lambda d: d["doc_id"])
    else:
        if db_projects.get_by_id(project_id) is None:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        scope = {"kind": "project", "id": project_id, "project_id": project_id}
        docs = _project_documents(project_id)
    root = storage_root_info(project_id)
    checked, items = 0, []
    for doc in docs:
        if doc.get("status") == "cancelled":
            continue
        checked += 1
        reason = read_state(doc)
        if reason != "readable":
            items.append(diagnosis_item(doc, diagnose_doc(doc, reason, root)))
    suspects = sorted({item["evidence"]["root"] for item in items if item["method"] == FIX_STORAGE_ROOT})
    canonical = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "ok": True, "scope": scope, "storage_root": root,
        "summary": {"checked": checked, "unreadable": len(items),
                    "recoverable": sum(1 for item in items if item["recoverable"]),
                    "storage_root_suspect": sum(1 for item in items if item["method"] == FIX_STORAGE_ROOT),
                    "suspect_roots": suspects},
        "items": items,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


# ── recovery (L0005 2.12 / 2.13) ─────────────────────────────────────────────

def card_front_matter(project_id: str, module: str, flow_id: str, doc_type: str, code: str, title: str) -> str:
    """The front matter of a card document's empty draft (the card document generator and its recovery)."""
    lines = ["---", f"project: {project_id}", f"module: {module}", f"group: {flow_id.rsplit('.', 1)[-1]}",
             f"group_id: {flow_id}", f"type: {doc_type}", f"doc_number: {code}", f"title: {title}", "---", ""]
    return "\n".join(lines)


def _empty_plan(doc: dict) -> bytes:
    from modules.flow_gate.services import work_plan_service as wp

    return wp.dumps(wp.empty_recovery_body(doc.get("project_id") or None)).encode("utf-8")


def card_draft_bytes(doc: dict) -> bytes:
    """The card generator's empty draft; the title is the row's, a WP is an empty plan JSON."""
    from modules.flow_gate.documents.constants import WORK_PLAN_TYPE

    if _type(doc) == WORK_PLAN_TYPE:
        return _empty_plan(doc)
    return card_front_matter(doc.get("project_id") or "", doc.get("module") or "none", doc.get("group_id") or "",
                             doc.get("type_code") or "", _code(doc), doc.get("title") or "").encode("utf-8")


def stub_bytes(doc: dict) -> bytes:
    """The metadata stub ``regenerate`` has always written (a WP: an empty plan JSON)."""
    from modules.flow_gate.documents.constants import WORK_PLAN_TYPE

    if _type(doc) == WORK_PLAN_TYPE:
        return _empty_plan(doc)
    return _console()._build_next_empty_content(
        project_id=doc.get("project_id"), module=doc.get("module") or "none", group_id=doc.get("group_id") or "",
        type_code=doc.get("type_code") or "", doc_code=_code(doc), title=doc.get("title") or "",
        target_id=doc.get("target_id") or "", next_type="",
    ).encode("utf-8")


def recorded_or_standard(doc: dict) -> Path:
    """The recorded location while it is inside the storage jail, else the standard one."""
    raw = (doc.get("file_path") or "").strip()
    if raw:
        _path, state = storage_paths.locate_storage_path(raw, doc.get("project_id"), _branch(doc))
        if state == "file_not_found":
            for candidate, inside in storage_paths.storage_path_candidates(raw, doc.get("project_id"), _branch(doc)):
                if inside:
                    return candidate
    return standard_location(doc)


def _in_use(target: Path, project_id: Optional[str], user: dict) -> RecoveryRefused:
    used_by = user.get("doc_id") if user.get("project_id") == project_id else None
    return RecoveryRefused("target_in_use", evidence={
        "target_file_path": storage_paths.to_storage_relative(target, project_id), "used_by_doc_id": used_by})


def recover_write_checks(doc: dict) -> None:
    """The existing recovery checks (disposal, AI run lock, TR2), as refusals. The same function runs outside
    and inside the guard; recovery applies no editability check (a final approved document recovers too)."""
    from modules.flow_gate.services.mutation_policy import MutationPolicyError

    console = _console()
    try:
        console._reject_if_group_disposed(doc)
    except HTTPException as exc:
        raise RecoveryRefused("group_disposed", str(exc.detail), status=exc.status_code, cause=exc) from exc
    try:
        console._reject_if_group_ai_running(doc)
    except (MutationPolicyError, HTTPException) as exc:
        raise RecoveryRefused("group_ai_running", status=getattr(exc, "status_code", 423), cause=exc) from exc
    if _type(doc) == "TR2":
        raise RecoveryRefused("tr2_not_supported", evidence={"type_code": "TR2"})


def recover_one(doc_id: str, expected_method: Optional[str], actor: str) -> dict:
    """Recover one document's body by the method its diagnosis gives now (L0005 2.12)."""
    from modules.flow_gate.documents import document_service
    from modules.flow_gate.graph_workflow.services.flow_guard import run_in_document_guard

    def not_found() -> RecoveryRefused:
        return RecoveryRefused("not_found", f"Document not found: {doc_id}", status=404)

    doc = document_service.get_document(doc_id)
    if doc is None:
        raise not_found()
    recover_write_checks(doc)

    def recover() -> dict:
        fresh = document_service.get_document(doc_id)
        if fresh is None:
            raise not_found()
        recover_write_checks(fresh)
        reason = read_state(fresh)
        if reason == "read_error":
            raise RecoveryRefused("read_error")
        if reason == "readable":
            raise RecoveryRefused("file_exists")
        found = diagnose_doc(fresh, reason, storage_root_info(fresh.get("project_id")))
        if expected_method is not None and found["method"] != expected_method:
            raise RecoveryRefused("diagnosis_changed", evidence=_summary(found))
        if found["method"] == FIX_STORAGE_ROOT:
            raise RecoveryRefused("storage_root_mismatch", evidence=found["evidence"])
        return _apply(fresh, reason, found, actor)

    return run_in_document_guard(doc_id, recover)


def _apply(doc: dict, reason: str, found: dict, actor: str) -> dict:
    """Inside the guard: DB first, the new file last, the event in the same transaction. ``actor`` is the
    canvas notice's actor."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db.connection import after_commit
    from modules.flow_gate.db.events import insert_event
    from modules.flow_gate.storage.files import StorageUnwritable, document_using_file, write_new_file

    method, project_id = found["method"], doc.get("project_id")
    own = (doc["doc_id"],)
    data = content = None
    if method in RELINKS:
        target = Path(found["candidate"])
        try:
            content = target.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:            # no longer a body its reader opens: not a recovery
            raise RecoveryRefused("diagnosis_changed", evidence=_summary(
                diagnose_doc(doc, reason, storage_root_info(project_id)))) from exc
    elif method == RESTORE_REVISION:
        target, data = recorded_or_standard(doc), found["revision"].encode("utf-8")
    elif method == REGENERATE_CARD_DRAFT:
        target, data = standard_location(doc), card_draft_bytes(doc)
    else:
        target, data = recorded_or_standard(doc), stub_bytes(doc)
    before = doc.get("file_path") or ""
    after = storage_paths.to_storage_relative(target, project_id)
    if data is not None:
        # Another row pointing at the target uses it even while its file is missing (L0005 2.3.1); this row
        # may point at it already (a recorded location) and is not another user.
        user = document_using_file(target, own)
        if user is not None:
            raise _in_use(target, project_id, user)
    # Only the path (and the file name derived from it): recovery applies no editability check, unlike
    # ``document_service.update_document`` (L0005 2.12 — a final approved or closed document recovers too).
    updated = db_documents.update(doc["doc_id"], {"file_path": after})
    if data is not None:                          # this row points at the target already (DB first)
        try:
            write_new_file(target, data, exclude_doc_ids=own)
        except StorageUnwritable as exc:
            if exc.params.get("cause") != "target_in_use":
                raise
            raise _in_use(target, project_id, document_using_file(target, own) or {}) from exc
        content = data.decode("utf-8")
    body_lost = found["body_lost"]
    insert_event(doc["doc_id"], "doc_body_recovered", reason=reason, note=json.dumps(
        {"method": method, "before": before, "after": after, "body_lost": body_lost, "reason": reason},
        ensure_ascii=False))
    console = _console()
    refreshed = updated or {**doc, "file_path": after}
    after_commit(lambda: console._broadcast_document_refresh(refreshed))
    if doc.get("group_id"):
        from modules.flow_gate.graph_workflow.services.canvas_events import (
            CANVAS_REASON_DOCUMENTS, publish_canvas_changed,
        )

        publish_canvas_changed(project_id=project_id, flow_id=doc["group_id"], reason=CANVAS_REASON_DOCUMENTS,
                               actor_user_id=actor, origin_client_id=None)
    loss = BODY_LOSS[body_lost]
    return {"data": updated, "restored_from": RESTORED_FROM[method], "method": method,
            "body_lost": loss != "none", "body_loss": loss, "before_file_path": before, "after_file_path": after,
            "content": content}


def recover_bulk(selections: list[tuple[str, Optional[str]]], actor: str) -> dict:
    """P0006 §2.8: one transaction per document, in the request order; a refusal or a failure of one document
    never stops the next."""
    results, counts = [], {"recovered": 0, "refused": 0, "failed": 0}
    for doc_id, expected_method in selections:
        row = {"doc_id": doc_id, "status": "recovered", "method": None, "restored_from": None, "body_lost": None,
               "body_loss": None, "before_file_path": None, "after_file_path": None, "refusal": None}
        try:
            out = recover_one(doc_id, expected_method, actor)
        except RecoveryRefused as refused:
            row.update(status="refused", refusal=refused.refusal())
        except Exception:  # noqa: BLE001 — one document's failure is that document's result
            _log.exception("body recovery failed: %s", doc_id)
            row.update(status="failed", refusal={"error": "internal_error", "detail": None, "evidence": None})
        else:
            row.update({key: out[key] for key in ("method", "restored_from", "body_lost", "body_loss",
                                                  "before_file_path", "after_file_path")})
        counts[row["status"]] += 1
        results.append(row)
    return {"ok": True, **counts, "results": results}
