"""Work plan (WP) human API — the earlier change the plan

    POST /api/v1/documents/work-plan                      create        (§4.2 / §4.3)
    GET  /api/v1/documents/{doc_id}/work-plan             read          (§4.4 / §4.5)
    PUT  /api/v1/documents/{doc_id}/work-plan             save          (§4.6 ~ §4.8)
    POST /api/v1/documents/{doc_id}/work-plan/suggest     AI suggestion (§4.9)

These routes only marshal HTTP. Every rule lives in services/work_plan_service, which
the AI inbox branch calls too — the design makes one validator the whole point of the
design. A rule written here instead would apply to people and not to AI workers.

Apply / preview / applications are deliberately absent: they need the
workflow sequence and belong to the next task set. Nothing here starts a run.
"""
from __future__ import annotations

from modules.flow_gate.storage.body import record_body_saved

import json as _json
import os
import re as _re
import threading
from functools import partial
from typing import Any, Optional

import anyio
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import document_revisions as db_revisions
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.documents import document_service
from modules.flow_gate.documents.constants import WORK_PLAN_TYPE
from modules.flow_gate.documents.attachments.constants import ATTACH_MAX_UPLOAD_BYTES
from modules.flow_gate.documents.type_code import doc_code_seq_text
from modules.flow_gate.numbering import numbering_service
from modules.flow_gate.services import work_plan_service as wp
from modules.flow_gate.services import work_plan_attachment_service as wp_attach
from modules.flow_gate.services import work_plan_apply_service as wpa
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.storage import paths as storage_paths

from modules.flow_gate.rbac.access import require_target_permission as require_permission

router = APIRouter(prefix="/documents", tags=["Documents"])

# The parent a work plan hangs from. A plan describes how a whole group will be run,
# so it attaches to that group's root document, not to a step inside it.
_PARENT_TYPES = {"R", "B"}


# ── Request models ───────────────────────────────────────────────────────────

class WorkPlanCreate(BaseModel):
    parent_doc_id: str
    title: Optional[str] = None
    counted_types: list[str] = Field(default_factory=list)
    provider_candidates: list[str] = Field(default_factory=list)
    quantities: dict[str, int] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    type_providers: dict[str, str] = Field(default_factory=dict)


class WorkPlanSave(BaseModel):
    base_revision_no: int
    body: dict
    capability_warning_acks: list[str] = Field(default_factory=list)


class WorkPlanRestore(BaseModel):
    base_revision_no: int


class WorkPlanRestore(BaseModel):
    base_revision_no: int


class WorkPlanSuggest(BaseModel):
    base_revision_no: Optional[int] = None
    scope: Optional[dict[str, list[str]]] = None


class WorkPlanApplyPreview(BaseModel):
    instruction_mode: str = "auto_approved"
    # codes the person already confirmed, e.g.
    # "legacy_card_unresolved" — the preview then no longer counts it as a blocker.
    acknowledged_codes: Optional[list[str]] = None


class WorkPlanSequenceCandidates(BaseModel):
    # Not an enum and not defaulted on purpose: the earlier implementation refuses anything that is not one
    # of the two modes rather than quietly picking one, because a silently defaulted mode
    # would rewrite a sequence the person never chose to rewrite.
    mode: str


class WorkPlanApply(BaseModel):
    instruction_mode: str = "auto_approved"
    change_workflow: bool
    workflow_tag: str
    wp_revision_no: int
    # without "legacy_card_unresolved" here, an apply over started
    # legacy rows whose card cannot be proven is refused with 409 legacy_card_unresolved.
    acknowledged_codes: Optional[list[str]] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _locale(request: Request) -> str:
    return wp.normalize_locale(request.headers.get("x-locale"))


def _validation_response(exc: wp.WorkPlanValidationError, locale: str) -> JSONResponse:
    return JSONResponse(status_code=422, content=wp.error_response(exc, locale))


def _load_doc(doc_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    if str(doc.get("type_code") or "").upper() != WORK_PLAN_TYPE:
        raise HTTPException(
            status_code=422,
            detail=f"{doc_id} is not a work plan document.",
        )
    return doc


def _plan_path(doc: dict):
    # path computation lives in work_plan_service alone. A sequence save
    # recording an application calls the same function, so the two paths cannot diverge.
    return wp.plan_path_for_doc(doc)


def _canonical_path(doc: dict):
    return wp.canonical_path_for_doc(doc)


def _providers(project_id: str) -> list[dict]:
    try:
        effective = ai_settings_service.resolve_effective(project_id)
    except Exception:  # noqa: BLE001 — an unreadable provider list is not a 500 here
        return []
    return list(effective.get("providers") or [])


def _revision_backup(doc: dict, row: dict) -> tuple[Optional[Any], Optional[dict], Optional[str]]:
    """Resolve and validate a revision without exposing its storage path.

    (main's original wording: "Resolve and validate a revision without ever exposing its storage path.")
    """
    stored = str(row.get("backup_path") or "").strip()
    if not stored:
        return None, None, "backup_path_missing"
    path = storage_paths.resolve_storage_path(
        stored, doc.get("project_id"), branch=doc.get("branch") or "main",
    )
    if path is None:
        return None, None, "backup_unavailable"
    try:
        body = wp.load_body(
            path,
            project_id=doc.get("project_id"),
            doc_id=doc.get("doc_id"),
        )
    except wp.WorkPlanUnreadable as exc:
        return path, None, exc.reason
    except OSError:
        return None, None, "backup_unavailable"
    return path, body, None


def _revisions_brief(doc: dict, limit: int = 20) -> list[dict]:
    """List one fail-closed restore candidate per revision number."""
    doc_id = doc.get("doc_id") or ""
    try:
        rows = db_revisions.list_by_doc(doc.get("doc_id") or "")
    except Exception:  # noqa: BLE001 — history failure must not hide the raw recovery view
        return []
    # main's simpler loop, replaced below:
    #     result = []
    #     for row in rows[:limit]:
    #         _path, _body, reason = _revision_backup(doc, row)
    # sliced to `limit` raw rows and resolved every one unconditionally.
    # That is not safe once a revision number
    # can be ambiguous (0599's whole point): slicing before dedup/ambiguity-checking could
    # silently return fewer than `limit` distinct revisions, or resolve an ambiguous
    # revision_no against whichever row happened to be listed first. So this loop walks the
    # full row list, dedupes by revision_no, checks each one through
    # `get_single_by_doc_revision` before resolving it, and only applies the `limit` cutoff
    # to the deduped `result`.
    result = []
    seen: set[int] = set()
    for row in rows:
        revision_no = int(row.get("revision_no") or 0)
        if revision_no in seen:
            continue
        seen.add(revision_no)
        try:
            selected = db_revisions.get_single_by_doc_revision(doc_id, revision_no)
        except db_revisions.RevisionAmbiguityError:
            selected = None
            reason = "revision_ambiguous"
        else:
            _path, _body, reason = _revision_backup(doc, selected or row)
        result.append({
            "revision_no": row.get("revision_no"),
            "created_at": row.get("created_at"),
            "created_by": row.get("created_by"),
            "restorable": selected is not None and reason is None,
            "restore_unavailable_reason": reason,
        })
        if len(result) >= limit:
            break
    return result


def _last_editor(doc: dict) -> Optional[str]:
    """documents has no updated_by column; the last revision row carries the editor."""
    try:
        rows = db_revisions.list_by_doc(doc.get("doc_id") or "")
    except Exception:  # noqa: BLE001
        rows = []
    if rows:
        return rows[0].get("created_by") or doc.get("owner_id")
    return doc.get("owner_id")


def _unreadable_response(doc: dict, exc: wp.WorkPlanUnreadable, locale: str) -> JSONResponse:
    copy = {
        "ko": "이 작업계획을 표로 열 수 없습니다. 원문 보기로 확인해 주세요.",
        "en": "This work plan cannot be opened as a table. Use the raw view.",
        "ja": "この作業計画は表として開けません。原文表示で確認してください。",
    }
    content: dict[str, Any] = {
        "code": "wp_unreadable",
        "message": copy.get(locale, copy["ko"]),
        "reason": exc.reason,
        "detail": exc.detail,
        "revision_no": doc.get("revision_no", 0),
        "revisions": _revisions_brief(doc),
    }
    if exc.raw is not None:
        content["raw"] = exc.raw
    return JSONResponse(status_code=409, content=content)


# an advisory lock allowing only one save per document at a time.
# Same mechanism and same reason as document-number reservation (numbering_service._get_lock):
# every invariant of the form "two concurrent requests, only one wins" rests on this
# in-process lock. The lock is per document, so requests saving different plans never wait
# on each other. the earlier change the earlier implementation: the lock lives in work_plan_locks, so
# the v0.2 work plan deploy takes the very same lock object as the save and the restore here.
from modules.flow_gate.services.work_plan_locks import plan_save_lock as _plan_save_lock  # noqa: E402


def _revision_conflict_response(
    doc: dict, locale: str, base_revision_no: Optional[int], current_revision: int,
) -> JSONResponse:
    """The single "someone else already saved" answer (P0009 §4.7, decision 7).

    0403 NR0004 F1/F6: the save path's early check, losing the revision CAS, and the proposal
    path's stale baseline all use the same body. The screen shows its reload banner off this
    one code, so the same event must not arrive in a different shape from each place.
    """
    conflict_copy = {
        "ko": "다른 사람이 이미 저장했습니다. 새로 읽어 오세요.",
        "en": "Someone else already saved this plan. Re-read it.",
        "ja": "他の人が既に保存しました。読み直してください。",
    }
    return JSONResponse(status_code=409, content={
        "code": "wp_revision_conflict",
        "message": conflict_copy.get(locale, conflict_copy["ko"]),
        "base_revision_no": base_revision_no,
        "current_revision_no": current_revision,
        # Decision 7: the other side's BODY is not shipped — there is no merge, so the
        # screen has nothing to do with it. Who and when is what decides the next click.
        "updated_by": _last_editor(doc),
        "updated_at": doc.get("updated_at"),
    })


def _emit(doc: dict, operation: str, payload: dict, actor: str) -> None:
    """Best-effort screen refresh. A failed publish must never fail a saved write."""
    try:
        from modules.flow_gate.api.v1.events.publisher import FlowEvent, publish_event_threadsafe
        from modules.flow_gate.api.v1.events.event_types import EventType

        base = dict(
            project=doc.get("project_id"),
            group_id=doc.get("group_id"),
            doc_id=doc.get("doc_id"),
            audience=actor,
        )
        publish_event_threadsafe(FlowEvent(
            event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
            payload={"operation": operation, **payload},
            **base,
        ))
        publish_event_threadsafe(FlowEvent(
            event_type=EventType.GROUP_VIEW_REFRESH,
            payload={"group_id": doc.get("group_id"),
                     "reason": "document_added" if operation == "created" else "document_updated"},
            **base,
        ))
    except Exception as exc:  # noqa: BLE001
        import LogAssist.log as logger
        logger.warning(f"[work-plan] SSE publish failed (ignored): {exc}")


def _started_card_ids(doc: dict, body: dict) -> set:
    """0649 T#3 (NR0003 O7): cards the editor must keep in place — best effort.

    A read failure only drops the editor's early lock; apply/pour/PATCH still refuse a plan
    that moves or removes a started card (O2 and the post-write check).

    T0017: a card whose deployed v0.2 Graph card ran (or cannot be judged) is started too, also
    after the Flow's run lock is released, so the editor locks its instruction choice; the save
    (``wpa.instruction_mode_lock_errors``) and the deploy refuse that change on their own.
    """
    started: set = set()
    try:
        started |= set(wpa.started_card_ids(doc, body))
    except Exception as exc:  # noqa: BLE001
        import LogAssist.log as logger
        logger.warning(f"[work-plan] started card lookup failed (ignored): {exc}")
    try:
        started |= wpa.graph_started_card_ids(doc)
    except Exception as exc:  # noqa: BLE001
        import LogAssist.log as logger
        logger.warning(f"[work-plan] Graph started card lookup failed (ignored): {exc}")
    return started


def _read_view(doc: dict, body: dict) -> dict:
    providers = _providers(doc.get("project_id") or "")
    started_cards = _started_card_ids(doc, body)
    meta = {}
    try:
        meta = _json.loads(doc.get("meta") or "{}") or {}
    except Exception:  # noqa: BLE001
        meta = {}
    plan_meta = meta.get("work_plan") or {}
    applications = wpa.read_applications(_plan_path(doc), doc.get("doc_id") or "", limit=1)
    # the screen does not compute editability on its own.
    # The screen locked the whole table on doc_review_status == 'approved' alone, while the
    # server looked at group final approval and closed state. So a plan could be "locked on
    # screen but editable via API/AI" and another "open on screen but 422 on save" at the same
    # time. The verdict is made here in one place and the screen consumes that answer.
    final_approved = document_service.is_final_approved(doc)
    editable = document_service.is_document_editable(doc, final_approved=final_approved)
    if editable:
        edit_locked_reason = None
    elif final_approved:
        edit_locked_reason = "final_approved"
    else:
        edit_locked_reason = "status"
    return {
        "ok": True,
        "doc_id": doc.get("doc_id"),
        "doc_type": WORK_PLAN_TYPE,
        "title": doc.get("title"),
        "group_id": doc.get("group_id"),
        "parent_doc_id": doc.get("target_id") or doc.get("triggered_by"),
        "status": doc.get("status"),
        "doc_review_status": doc.get("doc_review_status"),
        "editable": editable,
        "edit_locked_reason": edit_locked_reason,
        "revision_no": doc.get("revision_no", 0),
        "stored_path": doc.get("file_path"),
        "origin": plan_meta.get("origin") or "human",
        "origin_run_id": plan_meta.get("origin_run_id"),
        "created_by": doc.get("owner_id"),
        "updated_by": _last_editor(doc),
        "updated_at": doc.get("updated_at"),
        "body": body,
        # ("each provider should be changeable from the full provider list"):
        # ship the registered list already read above. Without it the editor knows nothing to
        # choose from beyond the candidate snapshot frozen into the plan, and cannot even draw
        # a non-candidate provider's name. It is read at the same moment as the plan body, so the two cannot disagree on screen.
        "registered_providers": [
            {
                "id": provider.get("id"),
                "name": provider.get("name"),
                "group_label": wp.provider_group_label(provider),
            }
            for provider in providers
            if provider.get("id")
        ],
        "provider_status": wp.provider_status(body, providers),
        "step_execution_status": [
            {
                "step_key": step.get("key"),
                # which card the row belongs to and whether that card
                # already started in the workflow (it cannot be moved or removed).
                "card_id": step.get("card_id"),
                "started": step.get("card_id") in started_cards,
                "reviewer_provider": {
                    "provider_id": step.get("reviewer_provider_id"),
                    "enabled": (
                        step.get("reviewer_provider_id") is None
                        or step.get("reviewer_provider_id") in {
                            str(provider.get("id")) for provider in providers
                            if provider.get("id")
                        }
                    ),
                    "current_name": next(
                        (
                            provider.get("name") for provider in providers
                            if str(provider.get("id")) == str(step.get("reviewer_provider_id"))
                        ),
                        None,
                    ),
                },
                "pre_instruction_attachment": wp_attach.reference_status(
                    doc.get("doc_id") or "",
                    step.get("pre_instruction_attachment"),
                ),
            }
            for step in body.get("steps") or []
        ],
        "review_count_choices": list(wp.review_count_choices()),
        "assignment_summary": wp.assignment_summary(body, providers),
        "unassigned_step_count": wp.unassigned_step_count(body),
        "totals": wp.totals(body),
        # where the screen used to hold its own 200 and block input silently.
        # The server states the cap and the editor draws the remaining count from that value.
        "limits": {
            "note_max_chars": wp.NOTE_MAX_CHARS,
            "pre_instruction_text_max_chars": wp.PRE_INSTRUCTION_TEXT_MAX_CHARS,
            "pre_instruction_attachment_max_bytes": ATTACH_MAX_UPLOAD_BYTES,
        },
        "last_application": (applications.get("items") or [None])[0],
    }


# ── Create ───────────────────────────────────────────────

@router.post("/work-plan", status_code=201)
@require_permission("project.document.create")
def create_work_plan(
    request: Request,
    body: WorkPlanCreate,
    current_user: dict = Depends(get_current_user),
):
    return JSONResponse(status_code=410, content={
        "ok": False, "code": "work_plan_retired",
        "message": "Work plan creation and deployment have been retired. Place cards through chat.",
    })


def _project_branch(project_id: Optional[str]) -> str:
    try:
        from modules.flow_gate.db import projects as _projects

        settings = _projects.get_settings(project_id) or {}
        return (settings.get("branch") or "main").strip() or "main"
    except Exception:  # noqa: BLE001
        return "main"


# ── Read ─────────────────────────────────────────────────

def _workflow_items(doc: dict) -> list[dict]:
    """The slot list of the workflow sequence this document belongs to; empty when there is none."""
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc.get("doc_id") or "")
        if seq is None:
            return []
        return db_wfseq.get_sequence_items(seq["id"])
    except Exception:  # noqa: BLE001 — an unreadable sequence must not block opening the document
        return []


def _dedicated_write(operation, *args):
    from modules.flow_gate.graph_workflow.services.card_document_ports import dedicated_write
    from modules.flow_gate.services.mutation_policy import MutationPolicyError
    try:
        return dedicated_write(operation, *args)
    except (HTTPException, MutationPolicyError):
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc


def _heal_unwritten_plan(
    doc: dict, exc: wp.WorkPlanUnreadable, actor_user_id: str,
) -> Optional[dict]:
    return _dedicated_write("wp_heal", doc, exc, actor_user_id)


@router.get("/{doc_id}/work-plan")
@require_permission("project.document.read")
def get_work_plan(
    request: Request,
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    locale = _locale(request)
    doc = _load_doc(doc_id)
    try:
        body = wp.load_body(
            _plan_path(doc),
            project_id=doc.get("project_id"),
            doc_id=doc.get("doc_id"),
        )
    except wp.WorkPlanUnreadable as exc:
        body = _heal_unwritten_plan(doc, exc, current_user["user_id"])
        if body is None:
            return _unreadable_response(doc, exc, locale)
    return _read_view(doc, body)


# ── Revision recovery ─────────────────────────────────────────────────────────

@router.post("/{doc_id}/work-plan/revisions/{revision_no}/restore")
@require_permission("project.document.update")
def restore_work_plan_revision(
    request: Request,
    doc_id: str,
    revision_no: int,
    body: WorkPlanRestore,
    current_user: dict = Depends(get_current_user),
):
    return _dedicated_write("wp_restore", request, doc_id, revision_no, body, current_user)


# ── Save ─────────────────────────────────────────────────

@router.put("/{doc_id}/work-plan")
@require_permission("project.document.update")
def save_work_plan(
    request: Request,
    doc_id: str,
    body: WorkPlanSave,
    current_user: dict = Depends(get_current_user),
):
    return _dedicated_write("wp_save", request, doc_id, body, current_user)


@router.post("/{doc_id}/work-plan/pre-instruction-attachments", status_code=201)
@require_permission("project.document.update")
async def upload_pre_instruction_attachment(
    request: Request,
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Upload one server-named reserved file for an editable instruction step."""

    doc = _load_doc(doc_id)
    from modules.flow_gate.documents.routers.documents import (
        _reject_if_group_ai_running,
        _reject_if_group_disposed,
    )
    _reject_if_group_disposed(doc)
    _reject_if_group_ai_running(doc)
    final_approved = document_service.is_final_approved(doc)
    if not document_service.is_document_editable(doc, final_approved=final_approved):
        raise HTTPException(status_code=422, detail="Work plan is not editable.")

    form = await request.form()
    files = list(form.getlist("file"))
    step_key = str(form.get("step_key") or "")
    if len(files) != 1:
        raise HTTPException(status_code=422, detail="Exactly one file is required.")
    try:
        plan = wp.load_body(
            _plan_path(doc),
            project_id=doc.get("project_id"),
            doc_id=doc_id,
        )
    except wp.WorkPlanUnreadable as exc:
        return _unreadable_response(doc, exc, _locale(request))
    step = next(
        (candidate for candidate in plan.get("steps") or []
         if candidate.get("key") == step_key),
        None,
    )
    if step is None:
        raise HTTPException(status_code=422, detail="Unknown WorkPlan step_key.")
    if step.get("locked") or (step.get("pair_role") == "result" and step.get("type") not in ("D", "P", "L", "DB")):
        raise HTTPException(
            status_code=422,
            detail="Pre-instruction attachments are allowed only on editable instruction steps.",
        )
    try:
        uploaded = await wp_attach.upload_pre_instruction_attachment(
            doc_id,
            step_key,
            files[0],
            current_user,
            request.headers.get("content-length"),
        )
    except wp_attach.AttachmentError as exc:
        return exc.response()
    return {"ok": True, **uploaded}


# ── Suggestion ──────────────────────────────────────────────────

@router.post("/{doc_id}/work-plan/suggest")
@require_permission("project.document.read")
def suggest_work_plan(
    request: Request,
    doc_id: str,
    body: WorkPlanSuggest = Body(default=WorkPlanSuggest()),
    current_user: dict = Depends(get_current_user),
):
    """Return a scoped, unsaved proposal from the project's existing assignment map."""
    locale = _locale(request)
    doc = _load_doc(doc_id)
    # actually check the base_revision_no the request sent.
    # This route validates the scope the screen sent against the canonical file on disk. But
    # the screen may hold an unsaved plan, so a perfectly valid step key created by raising a
    # quantity on screen was absent from the server's canonical file and got rejected with
    # wp_scope_invalid. Conversely a step deleted on screen survived on the server, so the
    # proposal's baseline differed from the screen's. There must be one baseline: if the sent revision differs, refuse to propose and ask for a save/reload first.
    current_revision = doc.get("revision_no", 0) or 0
    if body.base_revision_no is not None and body.base_revision_no != current_revision:
        return _revision_conflict_response(doc, locale, body.base_revision_no, current_revision)
    try:
        plan = wp.load_body(
            _plan_path(doc),
            project_id=doc.get("project_id"),
            doc_id=doc.get("doc_id"),
        )
    except wp.WorkPlanUnreadable as exc:
        return _unreadable_response(doc, exc, locale)

    project_id = doc.get("project_id") or ""
    providers = {p["id"]: p for p in _providers(project_id)}
    candidate_ids = {
        str(entry.get("provider_id")) for entry in (plan.get("provider_candidates") or [])
        if entry.get("provider_id")
    }
    # (option B-2): the AI scope dialog now shows every registered
    # provider, so this gate widens to the same set. Left narrow, the value picked by the
    # screen's [all] button comes straight back as a 422 — an [all] that is not all.
    selectable_ids = candidate_ids | {str(provider_id) for provider_id in providers}
    steps = plan.get("steps") or []
    step_by_key = {str(step.get("key")): step for step in steps}
    counted_types = set(plan.get("counted_types") or [])

    scope = body.scope
    if scope is None:
        quantity_codes: list[str] = []
        step_keys = [
            str(step.get("key")) for step in steps
            if not step.get("locked") and not step.get("provider_id")
        ]
        provider_ids = set(selectable_ids)
    else:
        quantity_codes = list(scope.get("quantity_type_codes") or [])
        step_keys = list(scope.get("step_keys") or [])
        provider_ids = set(scope.get("provider_ids") or [])
        invalid = (
            [code for code in quantity_codes if code not in counted_types]
            + [key for key in step_keys if key not in step_by_key or step_by_key[key].get("locked")]
            + [provider_id for provider_id in provider_ids if provider_id not in selectable_ids]
        )
        if invalid:
            messages = {
                "ko": "AI 배정 범위에 알 수 없거나 잠긴 항목이 있습니다.",
                "en": "The AI scope contains an unknown or locked item.",
                "ja": "AIの範囲に不明またはロック済みの項目があります。",
            }
            return JSONResponse(status_code=422, content={
                "code": "wp_scope_invalid",
                "message": messages.get(locale, messages["ko"]),
                "invalid": invalid,
            })

    type_counts = wp.workflow_type_counts(_workflow_items(doc))
    suggested_quantities = {
        code: type_counts[code] for code in quantity_codes if code in type_counts
    }
    suggested_steps: list[dict] = []
    for key in step_keys:
        step = step_by_key[key]
        try:
            provider_id = ai_settings_service.resolve_doctype_provider(
                project_id, str(step.get("type") or "")
            )
        except Exception:  # noqa: BLE001
            provider_id = None
        if not provider_id or provider_id not in provider_ids:
            continue
        suggested_steps.append({
            "key": key,
            "provider_id": provider_id,
            "provider_display_name": (providers.get(provider_id) or {}).get("name"),
            "note": step.get("note"),
        })

    scope_echo = {
        "quantity_type_codes": quantity_codes,
        "step_keys": step_keys,
        "provider_ids": sorted(provider_ids),
    }
    result = {
        "ok": True,
        "suggested": {"steps": suggested_steps},
        "basis": "not_specified" if scope is None else "project_type_provider_map",
    }
    if scope is not None:
        result["suggested"]["quantities"] = suggested_quantities
        result["scope_echo"] = scope_echo
    return result


# ── Apply preview / apply / journal ───────────────────────────

def _preview_sync(doc_id: str, body: WorkPlanApplyPreview, locale: str) -> dict:
    doc = _load_doc(doc_id)
    plan = wp.load_body(
            _plan_path(doc),
            project_id=doc.get("project_id"),
            doc_id=doc.get("doc_id"),
        )
    return wpa.preview(
        doc=doc,
        plan=plan,
        providers=_providers(doc.get("project_id") or ""),
        instruction_mode=body.instruction_mode,
        locale=locale,
        acknowledged_codes=body.acknowledged_codes,
    )


@router.post("/{doc_id}/work-plan/apply/preview")
@require_permission("project.document.read")
async def preview_work_plan_apply(
    request: Request,
    doc_id: str,
    body: WorkPlanApplyPreview,
    current_user: dict = Depends(get_current_user),
):
    # All DB/file work, including helpers reached from the service, runs off the event loop.
    return JSONResponse(status_code=410, content={
        "ok": False, "code": "work_plan_retired",
        "message": "Work plan creation and deployment have been retired. Place cards through chat.",
    })


def _apply_sync(
    doc_id: str,
    body: WorkPlanApply,
    locale: str,
    applied_by: str,
) -> dict:
    doc = _load_doc(doc_id)
    plan_path = _plan_path(doc)
    plan = wp.load_body(
        plan_path,
        project_id=doc.get("project_id"),
        doc_id=doc.get("doc_id"),
    )
    owner_id = doc.get("target_id") or doc.get("triggered_by")
    owner_doc = db_docs.get_by_id(owner_id)
    if owner_doc is None:
        raise HTTPException(status_code=422, detail=f"Workflow owner not found: {owner_id}")
    try:
        return wpa.apply(
            doc=doc,
            owner_doc=owner_doc,
            plan=plan,
            plan_path=plan_path,
            providers=_providers(doc.get("project_id") or ""),
            instruction_mode=body.instruction_mode,
            change_workflow=body.change_workflow,
            workflow_tag=body.workflow_tag,
            wp_revision_no=body.wp_revision_no,
            applied_by=applied_by,
            locale=locale,
            acknowledged_codes=body.acknowledged_codes,
        )
    except wpa.ApplyConflict as exc:
        copy = {
            "workflow_changed": {
                "ko": "미리보기를 연 뒤 워크플로가 바뀌었습니다. 다시 채워 주세요.",
                "en": "The workflow changed after preview. Fill it again.",
                "ja": "プレビュー後にワークフローが変わりました。もう一度入力してください。",
            },
            "wp_changed": {
                "ko": "미리보기를 연 뒤 작업계획이 바뀌었습니다. 다시 읽어 주세요.",
                "en": "The work plan changed after preview. Re-read it.",
                "ja": "プレビュー後に作業計画が変わりました。読み直してください。",
            },
            # the same copy the preview warning shows.
            "legacy_card_unresolved": {
                lang: wpa._COPY[lang]["legacy_card_unresolved"].format(
                    count=len(exc.payload.get("rows") or []),
                )
                for lang in ("ko", "en", "ja")
            },
        }
        # the card-order refusals speak with the preview's own copy.
        if exc.code not in copy and exc.code in wpa._COPY["ko"]:
            count = len(
                exc.payload.get("rows") or exc.payload.get("cards") or exc.payload.get("keys") or []
            ) or 1
            copy[exc.code] = {
                lang: wpa._COPY[lang][exc.code].format(count=count) for lang in ("ko", "en", "ja")
            }
        payload = dict(exc.payload)
        payload["message"] = (copy.get(exc.code) or {}).get(locale) or (copy.get(exc.code) or {}).get("ko") or exc.code
        return JSONResponse(status_code=409, content=payload)


@router.post("/{doc_id}/work-plan/apply")
@require_permission("project.document.update")
async def apply_work_plan(
    request: Request,
    doc_id: str,
    body: WorkPlanApply,
    current_user: dict = Depends(get_current_user),
):
    return JSONResponse(status_code=410, content={
        "ok": False, "code": "work_plan_retired",
        "message": "Work plan creation and deployment have been retired. Place cards through chat.",
    })


def _sequence_candidates_sync(doc_id: str, body: WorkPlanSequenceCandidates, locale: str):
    """0399 P0013 ① — read a plan and hand back the rows the edit dialog should open with.

    Deliberately not routed through _load_doc: P0013 pins the two refusals to their own
    bodies ({"error": "not_a_work_plan"} / {"error": "doc_not_found"}), and the shared
    helper answers with FastAPI's generic {"detail": ...}. The caller here is a dialog that
    branches on the code, so the code is the contract.
    """
    if body.mode not in wpseq.MODES:
        return JSONResponse(status_code=422, content={
            "error": "invalid_mode",
            "allowed": list(wpseq.MODES),
            "received": body.mode,
        })
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": doc_id})
    if str(doc.get("type_code") or "").upper() != WORK_PLAN_TYPE:
        return JSONResponse(status_code=422, content={"error": "not_a_work_plan", "doc_id": doc_id})
    try:
        plan = wp.load_body(
            _plan_path(doc),
            project_id=doc.get("project_id"),
            doc_id=doc.get("doc_id"),
        )
    except wp.WorkPlanUnreadable as exc:
        # "plan_unreadable": a plan nobody can open as a table is a different
        # problem from a plan with nothing in it, and the person's next move differs, so it
        # keeps the reader's own 409 rather than being folded into an empty result.
        return _unreadable_response(doc, exc, locale)
    return wpseq.build_candidates(doc=doc, plan=plan, mode=body.mode, locale=locale)


@router.post("/{doc_id}/work-plan/sequence-candidates")
@require_permission("project.document.read")
async def work_plan_sequence_candidates(
    request: Request,
    doc_id: str,
    body: WorkPlanSequenceCandidates,
    current_user: dict = Depends(get_current_user),
):
    return await anyio.to_thread.run_sync(
        partial(_sequence_candidates_sync, doc_id, body, _locale(request))
    )


def _applications_sync(doc_id: str, limit: int) -> dict:
    doc = _load_doc(doc_id)
    return wpa.read_applications(_plan_path(doc), doc_id, limit)


@router.get("/{doc_id}/work-plan/applications")
@require_permission("project.document.read")
async def get_work_plan_applications(
    doc_id: str,
    limit: int = 20,
    current_user: dict = Depends(get_current_user),
):
    return await anyio.to_thread.run_sync(partial(_applications_sync, doc_id, limit))
