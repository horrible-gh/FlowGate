"""Single-document retrieval endpoints (D021 §4-2, §4-3).

GET /api/v1/document/{id}
GET /api/v1/document/{id}/path
GET /api/v1/document/{project}/branches/{branch}/{module}/{group}/{doc}  ← T247 path-style
"""
from __future__ import annotations

import json
import re as _re
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.storage.paths import resolve_storage_path
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.services.q_service import get_answers_for_document
from modules.flow_gate.services import document_outline_service as outline_svc
from modules.flow_gate.utils.help_url import help_url
from modules.flow_gate.utils.id_validators import (
    validate_project_id,
    validate_group_id,
    validate_doc_id,
)
import LogAssist.log as logger

router = APIRouter(prefix="/api/v1", tags=["OutboundDocument"])


def _parse_rejection_history(raw: Any) -> list:
    """Convert DB rejection_history JSON string to a Python list. Returns an empty list on parse failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status,
                 "error_message": message, "help_url": help_url()},
    )


def _shape_review(row: dict) -> dict:
    raw_findings = row.get("findings")
    findings: list = []
    if isinstance(raw_findings, str):
        try:
            parsed = json.loads(raw_findings)
            if isinstance(parsed, list):
                findings = parsed
        except (json.JSONDecodeError, TypeError):
            findings = []
    elif isinstance(raw_findings, list):
        findings = raw_findings
    return {
        "id": row.get("id"),
        "revision_no": row.get("revision_no"),
        "reviewer_id": row.get("reviewer_id"),
        "verdict": row.get("verdict"),
        "finding_count": len(findings),
        "findings": findings,
        "comment": row.get("comment"),
        "reviewed_at": row.get("reviewed_at"),
        "created_at": row.get("created_at"),
    }


def _load_reviews(doc_id: str) -> tuple[Optional[dict], list[dict]]:
    try:
        rows = db_reviews.list_by_doc(doc_id)
    except Exception:
        return None, []
    history = [_shape_review(row) for row in rows]
    return (history[0] if history else None), history


def _load_test_runs(doc_id: str) -> tuple[Optional[dict], list[dict]]:
    try:
        from modules.flow_gate.services import test_run_service

        return test_run_service.load_test_run_embed(doc_id)
    except Exception:
        return None, []


_LEGACY_PROJECT_RE = _re.compile(r"^[a-z0-9_\-\u3131-\u318E\uAC00-\uD7A3]+$")
_LEGACY_GROUP_SEQ_RE = _re.compile(r"^\d{4}$")
_LEGACY_DOC_CODE_RE = _re.compile(r"^[A-Z]+\d{4}$")
_LEGACY_GROUP_ID_RE = _re.compile(r"^[a-z0-9_\-\u3131-\u318E\uAC00-\uD7A3]+-__ALL__-\d{4}$")
_LEGACY_DOC_ID_RE = _re.compile(r"^[a-z0-9_\-\u3131-\u318E\uAC00-\uD7A3]+-__ALL__-\d{4}-[A-Z]+\d{4}$")


def _validate_outbound_project_id(project: str) -> None:
    try:
        validate_project_id(project)
    except ValueError:
        if not _LEGACY_PROJECT_RE.fullmatch(project):
            raise ValueError(f"project_id format is invalid: {project!r}")


def _validate_outbound_doc_id(doc_id: str) -> None:
    if _LEGACY_DOC_ID_RE.fullmatch(doc_id):
        return
    validate_doc_id(doc_id)


def _compose_group_doc_ids(project: str, module: str, group: str, doc: str) -> tuple[str, str]:
    _validate_outbound_project_id(project)

    if _LEGACY_GROUP_ID_RE.fullmatch(group):
        group_id = group
    else:
        if not _LEGACY_GROUP_SEQ_RE.fullmatch(group):
            raise ValueError(f"group_id format is invalid: {group!r}")
        group_id = f"{project}-{module}-{group}"

    if _LEGACY_DOC_ID_RE.fullmatch(doc):
        doc_id = doc
        if not doc_id.startswith(f"{group_id}-"):
            raise ValueError(f"doc_id format is invalid: {doc!r}")
    else:
        if not _LEGACY_DOC_CODE_RE.fullmatch(doc):
            raise ValueError(f"doc_id format is invalid: {doc!r}")
        doc_id = f"{group_id}-{doc}"

    return group_id, doc_id


# _fallback_file_path was removed in L0054.0002 §4 — the branch-segment-drift
# fallback is now absorbed into storage.paths.resolve_storage_path().


@router.get("/document")
def get_document_rpc(doc_id: str = Query(...), request: Request = None):
    return get_document(request, doc_id)


@router.get("/document/path")
def get_document_path_rpc(doc_id: str = Query(...), request: Request = None):
    return get_document_path(request, doc_id)


# T247/T564 — path-style endpoint (including branch). Registered before the 1-segment {doc_id} endpoint.
@router.get("/document/{project}/branches/{branch}/{module}/{group}/{doc}")
def get_document_by_path(
    request: Request, project: str, branch: str, module: str, group: str, doc: str
):
    """Path-style document retrieval (T247/T564): /{project}/branches/{branch}/{module}/{group}/{doc}."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    if module != "__ALL__":
        return _fail(400, f"module currently only supports __ALL__ (input: {module})")

    try:
        _, doc_canonical = _compose_group_doc_ids(project, module, group, doc)
    except ValueError as exc:
        return _fail(422, str(exc))

    document = db_docs.get_by_id(doc_canonical)
    if document is None:
        return _fail(404, f"document not found: {doc_canonical}")
    doc_id = document["doc_id"]

    content: str | None = None
    file_path = document.get("file_path")
    if file_path:
        branch_val = document.get("branch", "main") or "main"
        resolved = resolve_storage_path(file_path, document.get("project_id"), branch=branch_val)
        if resolved is not None:
            try:
                content = resolved.read_text(encoding="utf-8")
            except OSError as e:
                logger.debug(f"[document/{doc_id}] failed to read file: {e}")
                return _fail(500, "An error occurred while reading the document content")

    resp: dict = {
        "ok": True,
        "doc_id": doc_id,
        "type": document.get("type_code"),
        "title": document.get("title"),
        "status": document.get("status"),
        "revision_no": document.get("revision_no", 0),
        "owner_id": document.get("owner_id"),
        "triggered_by": document.get("triggered_by"),
        "group_id": document.get("group_id"),
        "project": document.get("project_id"),
        "branch": document.get("branch", branch) or branch or "main",
        "module": document.get("module"),
        "stored_path": file_path,
        "content": content,
        "doc_review_status": document.get("doc_review_status"),
        "rejection_reason": document.get("rejection_reason"),
        "rejection_history": _parse_rejection_history(document.get("rejection_history")),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
    }
    # group 0022: Q&A is sub-data of every document. Attach the answers key only to
    # documents that have a container (query) — Q doc-type gating removed (Q type retired).
    qa_pairs = get_answers_for_document(doc_id)
    if qa_pairs:
        resp["answers"] = qa_pairs
    resp["ai_review"], resp["ai_review_history"] = _load_reviews(doc_id)
    resp["test_run"], resp["test_run_history"] = _load_test_runs(doc_id)
    return JSONResponse(content=resp)


# T565/R2: backward-compatible endpoint — supports pre-T564 URL (branch='main' fixed)
@router.get("/document/{project}/{module}/{group}/{doc}")
def get_document_by_path_legacy(
    request: Request, project: str, module: str, group: str, doc: str
):
    """T565/R2: Backward compatibility for old path-style URLs (branch='main' fixed)."""
    return get_document_by_path(request, project, "main", module, group, doc)


@router.get("/document/{doc_id}/path")
def get_document_path(request: Request, doc_id: str):
    """Document file path lookup (D021 §4-3)."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc))

    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")

    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "stored_path": doc.get("file_path"),
        "branch": doc.get("branch", "main"),
    })


@router.get("/document/{doc_id}/reviews")
def get_document_reviews(request: Request, doc_id: str):
    """Retrieve structured AI review history for a document."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc))
    if db_docs.get_by_id(doc_id) is None:
        return _fail(404, f"Document {doc_id} does not exist")

    latest, history = _load_reviews(doc_id)
    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "ai_review": latest,
        "ai_review_history": history,
    })


# ── 0370 R0001 / P0002 / L0003: the four partial-read endpoints ─────────────────
#
# Until now, opening a document dragged the entire body along. The four endpoints below are
# ways to fetch only the part you need, and they are **all additive**: no field is removed
# from, and no meaning changed in, the existing `GET /document/{doc_id}` response (P0002 §0).
#
# Not one line of computation lives here. Outlines, sections and coordinates are all done by
# document_outline_service, and this file only repackages the result into the shape P0002
# fixed — so one named number never differs per screen (L0003's purpose).


def _fail_with(status: int, message: str, extra: Optional[dict] = None) -> JSONResponse:
    """Attach the extra fields P0002 requires on a failure response (candidates, revision, ...)."""
    content: dict = {
        "ok": False,
        "http_status": status,
        "error_message": message,
        "help_url": help_url(),
    }
    if extra:
        content.update(extra)
    return JSONResponse(status_code=status, content=content)


def _document_text(doc: dict):
    """Read the stored body as a single canonical text, or None if it cannot be read."""
    file_path = doc.get("file_path")
    if not file_path:
        return None
    branch_val = doc.get("branch", "main") or "main"
    resolved = resolve_storage_path(file_path, doc.get("project_id"), branch=branch_val)
    if resolved is None:
        return None
    return outline_svc.DocumentText.from_path(resolved)


def _load_for_query(request: Request, doc_id: str, revision_no: Optional[int]):
    """The shared prologue of all four reads — it follows L0003 §4-1's check order exactly.

    1 token → 2 doc_id format → 3 document exists → 6 revision_no match. Reading the body (7)
    comes after, but a 409 must carry ``content_sha256`` (P0002 scenario 8), so the file is
    read up front. If unreadable only the fingerprint is null and the order is preserved.

    **Check 6 (409) coming before check 8 (404)** is the heart of this order. Answering "no
    such section" for a stale locator makes the worker think a heading was deleted and hunt in the wrong place.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth, None, None

    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc)), None, None

    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist"), None, None

    text = _document_text(doc)
    current = int(doc.get("revision_no", 0) or 0)
    if revision_no is not None and int(revision_no) != current:
        return _fail_with(
            409,
            f"revision changed: requested r{int(revision_no)}, current r{current}",
            {
                "doc_id": doc_id,
                "requested_revision_no": int(revision_no),
                "current_revision_no": current,
                "content_sha256": text.content_sha256 if text is not None else None,
            },
        ), None, None
    return None, doc, text


@router.get("/document/{doc_id}/outline")
def get_document_outline(
    request: Request,
    doc_id: str,
    max_level: int = Query(outline_svc.MAX_HEADING_LEVEL),
    revision_no: Optional[int] = Query(None),
):
    """Outline lookup (P0002 scenarios 1 and 2). Not one character of the body is included.

    A document with no headings gets 200 and an empty ``items``. A 404 would make the worker
    think the document itself is missing — common, since requirement (R) documents are usually short prose (P0002 scenario 2).
    """
    err, doc, text = _load_for_query(request, doc_id, revision_no)
    if err is not None:
        return err
    if text is None:
        return _fail(404, f"document body is not readable: {doc_id}")

    # max_level is only a display depth, so an out-of-range value is clamped rather than
    # rejected. Bouncing one outline read with a 422 buys nothing in unattended work.
    level = max(1, min(int(max_level), outline_svc.MAX_HEADING_LEVEL))
    items, truncated = outline_svc.outline_items(text, level)
    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "revision_no": int(doc.get("revision_no", 0) or 0),
        "content_sha256": text.content_sha256,
        "title": doc.get("title"),
        "type": doc.get("type_code"),
        "document_lines": text.document_lines,
        "document_chars": text.document_chars,
        "body_line_start": text.body_line_start,
        "section_total": text.section_total,
        "max_level": level,
        "truncated": truncated,
        "items": items,
    })


@router.get("/document/{doc_id}/section")
def get_document_section(
    request: Request,
    doc_id: str,
    section: Optional[str] = Query(None),
    section_id: Optional[str] = Query(None),
    lines: Optional[str] = Query(None),
    chars: Optional[str] = Query(None),
    include_children: bool = Query(True),
    max_chars: Optional[int] = Query(None),
    revision_no: Optional[int] = Query(None),
):
    """Section read (P0002 scenarios 3-8).

    Send **exactly one** of ``section``/``section_id``/``lines``/``chars``. Whether found by
    name or by line number the same locator comes back, so outline → section read → search
    results all chain together.

    Past the cap it returns whole lines only, **never cutting mid-line**, along with a
    ``next_locator`` to continue from. That is why ``chars`` can be less than ``max_chars``.
    """
    err, doc, text = _load_for_query(request, doc_id, revision_no)
    if err is not None:
        return err
    if text is None:
        return _fail(404, f"document body is not readable: {doc_id}")

    rev = int(doc.get("revision_no", 0) or 0)
    try:
        limit = outline_svc.clamp_max_chars(max_chars)
        resolved = outline_svc.resolve_locator(
            text, doc_id, rev,
            section=section, section_id=section_id, lines=lines, chars=chars,
            include_children=include_children,
        )
    except outline_svc.LocatorError as exc:
        return _fail_with(exc.status, exc.message, exc.extra)

    last_line, truncated = outline_svc.cut_to_limit(
        text, resolved.line_start, resolved.line_end, limit
    )
    enclosing = resolved.item or outline_svc.enclosing_section(text.items, resolved.line_start)
    locator = outline_svc.build_locator(
        text, doc_id, rev, resolved.line_start, last_line, enclosing
    )
    next_locator = None
    if truncated:
        # Continuation keeps pointing at the same section and only advances the start line; the end stays the section's end.
        next_locator = outline_svc.build_locator(
            text, doc_id, rev, last_line + 1, resolved.line_end, enclosing,
            char_start=text.char_start_of(last_line + 1),
            char_end=text.char_end_of(resolved.line_end),
        )

    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "revision_no": rev,
        "content_sha256": text.content_sha256,
        "resolved_by": resolved.resolved_by,
        "ambiguous": resolved.ambiguous,
        "candidates": resolved.candidates,
        "include_children": include_children,
        "locator": locator,
        # text includes the heading line itself, so pasting what you receive reproduces the original.
        "heading": text.heading_line_text(enclosing),
        "text": text.slice_lines(resolved.line_start, last_line),
        "chars": locator["char_end"] - locator["char_start"],
        "lines": last_line - resolved.line_start + 1,
        "body_line_start": text.body_line_start,
        "document_lines": text.document_lines,
        "document_chars": text.document_chars,
        "truncated": truncated,
        "next_locator": next_locator,
    })


@router.get("/document/{doc_id}/meta")
def get_document_meta(
    request: Request,
    doc_id: str,
    revision_no: Optional[int] = Query(None),
):
    """Everything except the body (P0002 scenario 12).

    The existing ``GET /document/{doc_id}`` response minus ``content``, plus ``answers_count``
    and ``body``. **The ``content`` key is absent entirely** — leaving it ``null`` would be
    indistinguishable from a document whose body is empty.

    A missing or unreadable file still gets 200: the document card (title, status, review
    state, revision) must be drawable independently of the body.
    """
    err, doc, text = _load_for_query(request, doc_id, revision_no)
    if err is not None:
        return err

    if text is None:
        body = {
            "present": False, "chars": 0, "lines": 0, "body_line_start": 1,
            "section_total": 0, "content_sha256": None, "outline_url": None,
        }
    else:
        path = request.url.path
        outline_url = (path[: -len("/meta")] + "/outline") if path.endswith("/meta") else None
        body = {
            "present": True,
            "chars": text.document_chars,
            "lines": text.document_lines,
            "body_line_start": text.body_line_start,
            "section_total": text.section_total,
            "content_sha256": text.content_sha256,
            "outline_url": outline_url,
        }

    resp: dict = {
        "ok": True,
        "doc_id": doc_id,
        "type": doc.get("type_code"),
        "title": doc.get("title"),
        "status": doc.get("status"),
        "revision_no": int(doc.get("revision_no", 0) or 0),
        "owner_id": doc.get("owner_id"),
        "triggered_by": doc.get("triggered_by"),
        "group_id": doc.get("group_id"),
        "project": doc.get("project_id"),
        "module": doc.get("module"),
        "branch": doc.get("branch", "main"),
        "stored_path": doc.get("file_path"),
        "doc_review_status": doc.get("doc_review_status"),
        "rejection_reason": doc.get("rejection_reason"),
        "rejection_history": _parse_rejection_history(doc.get("rejection_history")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
    resp["ai_review"], resp["ai_review_history"] = _load_reviews(doc_id)
    resp["test_run"], resp["test_run_history"] = _load_test_runs(doc_id)
    # Use the existing lookup if you need answer contents; the point here is to omit bodies, so only counts.
    resp["answers_count"] = len(get_answers_for_document(doc_id) or [])
    resp["body"] = body
    return JSONResponse(content=resp)


_RELATIONS_REFERENCED_BY_MAX = 50


def _doc_brief(doc_id: Optional[str]) -> Optional[dict]:
    """One document row for a relations response; only the id survives if the target was deleted."""
    if not doc_id:
        return None
    row = db_docs.get_by_id(doc_id)
    if row is None:
        return {"doc_id": doc_id, "type": None, "title": None, "status": None}
    return {
        "doc_id": row.get("doc_id"),
        "type": row.get("type_code"),
        "title": row.get("title"),
        "status": row.get("status"),
    }


def _doc_seq(row: dict) -> int:
    """Position within the bundle; when the ``seq`` column is empty it is read off the doc_id tail."""
    seq = row.get("seq")
    if isinstance(seq, int):
        return seq
    try:
        return int(str(seq))
    except (TypeError, ValueError):
        pass
    m = _re.search(r"(\d+)-[A-Za-z]+$", row.get("doc_id") or "")
    return int(m.group(1)) if m else 0


def _workflow_item_brief(item: Optional[dict]) -> Optional[dict]:
    if item is None:
        return None
    return {
        "item_seq": item.get("item_seq"),
        "type": item.get("type"),
        "label": item.get("label"),
        "status": item.get("status"),
        "result_doc_id": item.get("result_doc_id"),
    }


_WORKFLOW_UNDECIDED = {
    "root_doc_id": None, "doc_class": None, "decided": False, "item_seq": None,
    "type": None, "label": None, "status": None, "prev_item": None, "next_item": None,
    "orphan": False,
}


def _relations_workflow(doc_id: str) -> dict:
    """Which workflow slot this is; with no decision it returns a set of all-null values."""
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    try:
        sequence = db_wfseq.get_sequence_for_member_doc(doc_id)
    except Exception:  # noqa: BLE001 — a relations lookup must not die over the workflow
        sequence = None
    try:
        orphan = db_wfseq.is_orphaned_workflow_member(doc_id)
    except Exception:  # noqa: BLE001
        orphan = False
    if not sequence:
        undecided = dict(_WORKFLOW_UNDECIDED)
        undecided["orphan"] = orphan
        return undecided
    try:
        items = db_wfseq.get_sequence_items(sequence["id"]) or []
    except Exception:  # noqa: BLE001
        items = []

    root_doc_id = sequence.get("doc_id")
    mine_idx = None
    for idx, item in enumerate(items):
        if item.get("result_doc_id") == doc_id:
            mine_idx = idx
            break
    if mine_idx is None:
        # This document is not yet registered as any slot's output (the R that owns the workflow, say).
        return {
            "root_doc_id": root_doc_id,
            "doc_class": items[0].get("doc_class") if items else None,
            "decided": True,
            "item_seq": None, "type": None, "label": None, "status": None,
            "prev_item": None, "next_item": None, "orphan": False,
        }
    mine = items[mine_idx]
    return {
        "root_doc_id": root_doc_id,
        "doc_class": mine.get("doc_class"),
        "decided": True,
        "item_seq": mine.get("item_seq"),
        "type": mine.get("type"),
        "label": mine.get("label"),
        "status": mine.get("status"),
        "prev_item": _workflow_item_brief(items[mine_idx - 1] if mine_idx > 0 else None),
        "next_item": _workflow_item_brief(
            items[mine_idx + 1] if mine_idx + 1 < len(items) else None
        ),
        "orphan": False,
    }


@router.get("/document/{doc_id}/relations")
def get_document_relations(
    request: Request,
    doc_id: str,
    revision_no: Optional[int] = Query(None),
):
    """Relations lookup (P0002 scenario 13). **The body is never read.**

    Gathers what this document came from (``triggered_by``), what it points at (``target``),
    what points back at it (``referenced_by``), its neighbours in the same bundle, and which
    workflow slot it is. No new table, no new column — it only reads values that already exist.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc))
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")

    current = int(doc.get("revision_no", 0) or 0)
    if revision_no is not None and int(revision_no) != current:
        return _fail_with(
            409,
            f"revision changed: requested r{int(revision_no)}, current r{current}",
            {"doc_id": doc_id, "requested_revision_no": int(revision_no),
             "current_revision_no": current, "content_sha256": None},
        )

    from modules.flow_gate.db import document_revisions as db_revisions
    from modules.flow_gate.db import groups as db_groups

    group_id = doc.get("group_id")
    group_row = db_groups.get_by_id(group_id) if group_id else None
    siblings = db_docs.get_documents_by_group_id(group_id) if group_id else []
    siblings.sort(key=lambda r: (_doc_seq(r), r.get("created_at") or "", r.get("doc_id") or ""))
    position = next(
        (i for i, r in enumerate(siblings) if r.get("doc_id") == doc_id), None
    )
    prev_doc = _doc_brief(siblings[position - 1]["doc_id"]) if position else None
    next_doc = (
        _doc_brief(siblings[position + 1]["doc_id"])
        if position is not None and position + 1 < len(siblings) else None
    )

    referrers = db_docs.get_documents_by_target_id(doc_id) if doc_id else []
    referenced_by = [
        {"doc_id": r.get("doc_id"), "type": r.get("type_code"),
         "title": r.get("title"), "status": r.get("status")}
        for r in referrers[:_RELATIONS_REFERENCED_BY_MAX]
    ]

    try:
        revisions = db_revisions.list_by_doc(doc_id) or []
    except Exception:  # noqa: BLE001
        revisions = []

    resp: dict = {
        "ok": True,
        "doc_id": doc_id,
        "revision_no": current,
        "group": {
            "group_id": group_id,
            "title": (group_row or {}).get("title"),
            "seq": _doc_seq(doc),
            "document_total": len(siblings),
            "prev_doc": prev_doc,
            "next_doc": next_doc,
        },
        "triggered_by": _doc_brief(doc.get("triggered_by")),
        "target": _doc_brief(doc.get("target_id")),
        "referenced_by": referenced_by,
        "superseded_by": _doc_brief(doc.get("superseded_by")),
        "workflow": _relations_workflow(doc_id),
        # Revision history only. The body of each revision is not served here.
        "revisions": [
            {
                "revision_no": r.get("revision_no"),
                "created_at": r.get("created_at"),
                "edit_reason": r.get("edit_reason"),
                "linked_doc_id": r.get("linked_doc_id"),
                "backup_path": r.get("backup_path"),
            }
            for r in revisions
        ],
        "answers_count": len(get_answers_for_document(doc_id) or []),
        "ai_review_count": len(_load_reviews(doc_id)[1]),
        "test_run_count": len(_load_test_runs(doc_id)[1]),
    }
    if len(referrers) > _RELATIONS_REFERENCED_BY_MAX:
        resp["referenced_by_truncated"] = True
    return JSONResponse(content=resp)


@router.get("/document/{doc_id}")
def get_document(request: Request, doc_id: str):
    """Retrieve document content and metadata (D021 §4-2)."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc))

    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")

    content: str | None = None
    file_path = doc.get("file_path")
    if file_path:
        branch_val = doc.get("branch", "main") or "main"
        resolved = resolve_storage_path(file_path, doc.get("project_id"), branch=branch_val)
        if resolved is not None:
            try:
                content = resolved.read_text(encoding="utf-8")
            except OSError as e:
                logger.debug(f"[document/{doc_id}] failed to read file: {e}")
                return _fail(500, "An error occurred while reading the document content")

    resp: dict = {
        "ok": True,
        "doc_id": doc_id,
        "type": doc.get("type_code"),
        "title": doc.get("title"),
        "status": doc.get("status"),
        "revision_no": doc.get("revision_no", 0),
        "owner_id": doc.get("owner_id"),
        "triggered_by": doc.get("triggered_by"),
        "group_id": doc.get("group_id"),
        "project": doc.get("project_id"),
        "module": doc.get("module"),
        "branch": doc.get("branch", "main"),
        "stored_path": file_path,
        "content": content,
        "doc_review_status": doc.get("doc_review_status"),
        "rejection_reason": doc.get("rejection_reason"),
        "rejection_history": _parse_rejection_history(doc.get("rejection_history")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
    qa_pairs = get_answers_for_document(doc_id)
    if qa_pairs:
        resp["answers"] = qa_pairs
    resp["ai_review"], resp["ai_review_history"] = _load_reviews(doc_id)
    resp["test_run"], resp["test_run_history"] = _load_test_runs(doc_id)
    return JSONResponse(content=resp)
