"""Work plan (WP) human API — flowgate.default.0395 P0009 §4.

    POST /api/v1/documents/work-plan                      create        (§4.2 / §4.3)
    GET  /api/v1/documents/{doc_id}/work-plan             read          (§4.4 / §4.5)
    PUT  /api/v1/documents/{doc_id}/work-plan             save          (§4.6 ~ §4.8)
    POST /api/v1/documents/{doc_id}/work-plan/suggest     AI suggestion (§4.9)

These routes only marshal HTTP. Every rule lives in services/work_plan_service, which
the AI inbox branch calls too — D0007 §2.2 makes one validator the whole point of the
design. A rule written here instead would apply to people and not to AI workers.

Apply / preview / applications (P0009 §7·§8) are deliberately absent: they need the
workflow sequence and belong to the next task set. Nothing here starts a run (P0009 §9).
"""
from __future__ import annotations

import json as _json
import re as _re
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
from modules.flow_gate.numbering import numbering_service
from modules.flow_gate.services import work_plan_service as wp
from modules.flow_gate.services import work_plan_apply_service as wpa
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.storage import paths as storage_paths

try:
    from rbac.decorators import require_permission  # type: ignore[import]
except ImportError:
    def require_permission(perm: str):
        def _decorator(func):
            return func
        return _decorator

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


class WorkPlanSuggest(BaseModel):
    base_revision_no: Optional[int] = None
    scope: Optional[dict[str, list[str]]] = None


class WorkPlanApplyPreview(BaseModel):
    instruction_mode: str = "auto_approved"


class WorkPlanApply(BaseModel):
    instruction_mode: str = "auto_approved"
    change_workflow: bool
    workflow_tag: str
    wp_revision_no: int


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
    stored = (doc.get("file_path") or "").strip()
    branch = (doc.get("branch") or "main").strip() or "main"
    if stored:
        resolved = storage_paths.resolve_storage_path(stored, doc.get("project_id"), branch=branch)
        if resolved is not None:
            return resolved
        # The file may legitimately not exist yet (a rolled-back tree); fall through to
        # the canonical location rather than reporting "not a work plan".
        loose = storage_paths.resolve_storage_dir(stored, doc.get("project_id"))
        if loose is not None:
            return loose
    return _canonical_path(doc)


def _canonical_path(doc: dict):
    group_id = doc.get("group_id") or ""
    doc_id = doc.get("doc_id") or ""
    doc_code = doc_id[len(group_id) + 1:] if doc_id.startswith(group_id + ".") else doc_id
    return storage_paths.document_path(
        project_id=doc.get("project_id"),
        group_code=group_id,
        doc_code=doc_code,
        filename=wp.DOCUMENT_FILENAME,
        module=doc.get("module") or "none",
        branch=(doc.get("branch") or "main") or "main",
    )


def _providers(project_id: str) -> list[dict]:
    try:
        effective = ai_settings_service.resolve_effective(project_id)
    except Exception:  # noqa: BLE001 — an unreadable provider list is not a 500 here
        return []
    return list(effective.get("providers") or [])


def _revisions_brief(doc_id: str, limit: int = 20) -> list[dict]:
    try:
        rows = db_revisions.list_by_doc(doc_id)
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "revision_no": row.get("revision_no"),
            "created_at": row.get("created_at"),
            "created_by": row.get("created_by"),
        }
        for row in rows[:limit]
    ]


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
        "revisions": _revisions_brief(doc.get("doc_id") or ""),
    }
    if exc.raw is not None:
        content["raw"] = exc.raw
    return JSONResponse(status_code=409, content=content)


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


def _read_view(doc: dict, body: dict) -> dict:
    providers = _providers(doc.get("project_id") or "")
    meta = {}
    try:
        meta = _json.loads(doc.get("meta") or "{}") or {}
    except Exception:  # noqa: BLE001
        meta = {}
    plan_meta = meta.get("work_plan") or {}
    applications = wpa.read_applications(_plan_path(doc), doc.get("doc_id") or "", limit=1)
    return {
        "ok": True,
        "doc_id": doc.get("doc_id"),
        "doc_type": WORK_PLAN_TYPE,
        "title": doc.get("title"),
        "group_id": doc.get("group_id"),
        "parent_doc_id": doc.get("target_id") or doc.get("triggered_by"),
        "status": doc.get("status"),
        "doc_review_status": doc.get("doc_review_status"),
        "revision_no": doc.get("revision_no", 0),
        "stored_path": doc.get("file_path"),
        "origin": plan_meta.get("origin") or "human",
        "origin_run_id": plan_meta.get("origin_run_id"),
        "created_by": doc.get("owner_id"),
        "updated_by": _last_editor(doc),
        "updated_at": doc.get("updated_at"),
        "body": body,
        "provider_status": wp.provider_status(body, providers),
        "assignment_summary": wp.assignment_summary(body, providers),
        "unassigned_step_count": wp.unassigned_step_count(body),
        "totals": wp.totals(body),
        "last_application": (applications.get("items") or [None])[0],
    }


# ── Create (P0009 §4.2 / §4.3) ───────────────────────────────────────────────

@router.post("/work-plan", status_code=201)
@require_permission("perm_document_create")
def create_work_plan(
    request: Request,
    body: WorkPlanCreate,
    current_user: dict = Depends(get_current_user),
):
    locale = _locale(request)
    parent = db_docs.get_by_id(body.parent_doc_id)
    if parent is None:
        raise HTTPException(status_code=422, detail=f"Parent document not found: {body.parent_doc_id}")
    if str(parent.get("type_code") or "").upper() not in _PARENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail="A work plan attaches to the group's root document "
                   f"({'/'.join(sorted(_PARENT_TYPES))}), not to {parent.get('type_code')}.",
        )

    from modules.flow_gate.documents.routers.documents import (
        _reject_if_group_ai_running,
        _reject_if_group_disposed,
    )
    _reject_if_group_disposed(parent)
    _reject_if_group_ai_running(parent)

    # The screen already blocks an empty selection (D0007 §6.3); this is the same
    # verdict for a request that never went through the screen (P0009 §4.3).
    errors = []
    if not body.counted_types:
        errors.append(wp.empty_selection_error("counted_types"))
    if not body.provider_candidates:
        errors.append(wp.empty_selection_error("provider_candidates"))
    invalid_quantities = {
        code: count for code, count in body.quantities.items()
        if isinstance(count, bool) or count < 1
    }
    if invalid_quantities:
        raise HTTPException(
            status_code=422,
            detail="quantities values must be integers greater than or equal to 1.",
        )
    if errors:
        return _validation_response(
            wp.WorkPlanValidationError(errors, action="create"), locale
        )

    project_id = parent.get("project_id")
    group_id = parent.get("group_id")
    module = parent.get("module") or "none"

    candidates = wp.snapshot_candidates(list(body.provider_candidates), _providers(project_id))
    plan = wp.initial_body(
        list(body.counted_types),
        candidates,
        project_id,
        quantities=dict(body.quantities),
        defaults=dict(body.defaults),
        type_providers=dict(body.type_providers),
    )
    try:
        plan = wp.validate(plan, project_id=project_id, action="create")
    except wp.WorkPlanValidationError as exc:
        return _validation_response(exc, locale)

    try:
        doc_code = numbering_service.reserve_document(
            group_id=group_id, doc_type=WORK_PLAN_TYPE, module=module,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Numbering lock timeout: {exc}")

    doc_id = f"{group_id}.{doc_code}"
    match = _re.match(r"^(\d+)-[A-Za-z]+$", doc_code)
    seq = int(match.group(1)) if match else 0
    branch = _project_branch(project_id)
    path = storage_paths.document_path(
        project_id=project_id,
        group_code=group_id,
        doc_code=doc_code,
        filename=wp.DOCUMENT_FILENAME,
        module=module,
        branch=branch,
    )
    title = (body.title or "").strip() or doc_id
    if len(title) > 100:
        raise HTTPException(status_code=422, detail="Title must be 100 characters or fewer.")

    try:
        wp.write_body_atomically(path, plan)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Storage error: {exc}")

    now = now_iso()
    try:
        doc = document_service.create_document({
            "doc_id": doc_id,
            "project_id": project_id,
            "module": module,
            "group_id": group_id,
            "type_code": WORK_PLAN_TYPE,
            "seq": seq,
            "title": title,
            "status": "open",
            "owner_id": current_user["user_id"],
            "target_id": body.parent_doc_id,
            "triggered_by": body.parent_doc_id,
            "file_path": storage_paths.to_storage_relative(path, project_id),
            "revision_no": 0,
            "created_at": now,
            "updated_at": now,
            "meta": _json.dumps({"work_plan": {"origin": "human"}}, ensure_ascii=False),
        }, actor_user_id=current_user["user_id"])
    except Exception as exc:  # noqa: BLE001 — roll the file back, never leave an orphan
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"DB registration error: {exc}")

    # 0395 T0021 — 작업계획을 워크플로 시퀀스의 한 칸으로 넣을 수 있게 되면서 생긴 짝.
    # D0007 §7 은 작업계획을 "워크플로 칸을 차지하는 타입"으로 못박았고(그래서 WP 는
    # NON_SLOT_WORKFLOW_TYPES 에 없다), AI 경로는 이미 인박스 7.5 단계에서 머리 칸을
    # 채운다. 사람 경로만 이 등록을 하지 않으면, 시퀀스에 WP 칸을 둔 그룹에서 사람이
    # 계획을 만들었을 때 그 칸은 영원히 대기로 남고 만들어진 문서는 고아(orphaned
    # workflow member)로 잡힌다 — 화면상으로는 "만들었는데 워크플로가 안 넘어간다"이다.
    #
    # 머리가 WP 일 때만 채운다. 작업계획은 자문형이라 아무 시점에나 쓸 수 있고(같은
    # 이유로 인박스 머리 타입 가드에서도 빠져 있다), 머리가 다른 타입인데 칸을 차지하면
    # 그 단계의 결과 문서를 작업계획이 가로채는 꼴이 된다.
    # 최선노력(best-effort)이다: 문서는 이미 만들어졌으므로 여기서 실패해도 생성을
    # 되돌리지 않는다. 인박스 7.5 단계와 같은 태도다.
    try:
        head_item = db_wfseq.get_pending_head_by_group(group_id, project_id)
        if head_item is not None and str(head_item.get("type") or "").upper() == WORK_PLAN_TYPE:
            from modules.flow_gate.workflow.pipeline_service import register_workflow_result

            register_workflow_result(
                item_id=head_item["id"],
                registered_path=storage_paths.to_storage_relative(path, project_id),
                registered_doc_id=doc_id,
                registered_at=now,
                actor_user_id=current_user["user_id"],
            )
    except Exception as exc:  # noqa: BLE001 — the document exists; slot filling is best-effort
        import LogAssist.log as logger
        logger.warning(f"[work-plan] workflow slot registration skipped ({doc_id}): {exc}")

    # D0007 §3.1 결정 4: a work plan is NOT auto-complete — it opens pending_review and
    # goes through the ordinary review pipeline. §4.2 also forbids touching the parent's
    # status, so _try_close_parent_on_child_created is deliberately not called here.
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    try:
        transition_document_review(
            doc_id=doc_id,
            action="submit",
            actor_user_id=current_user["user_id"],
            user_permissions={"document.update"},
        )
    except Exception as exc:  # noqa: BLE001 — the document exists; review state is best-effort
        import LogAssist.log as logger
        logger.warning(f"[work-plan] review transition skipped ({doc_id}): {exc}")

    refreshed = db_docs.get_by_id(doc_id) or doc
    _emit(refreshed, "created",
          {"doc_id": doc_id, "type": WORK_PLAN_TYPE, "title": title,
           "status": refreshed.get("status"), "revision_no": 0},
          current_user["user_id"])

    return JSONResponse(status_code=201, content={
        "ok": True,
        "doc_id": doc_id,
        "doc_type": WORK_PLAN_TYPE,
        "title": title,
        "group_id": group_id,
        "parent_doc_id": body.parent_doc_id,
        "status": refreshed.get("status"),
        "doc_review_status": refreshed.get("doc_review_status"),
        "revision_no": refreshed.get("revision_no", 0),
        "stored_path": refreshed.get("file_path"),
        "created_by": current_user["user_id"],
        "created_at": now,
        "origin": "human",
        # The screen draws the table straight from this response; re-reading would
        # leave a created-but-empty screen if the next request never lands (§4.2).
        "body": plan,
    })


def _project_branch(project_id: Optional[str]) -> str:
    try:
        from modules.flow_gate.db import projects as _projects

        settings = _projects.get_settings(project_id) or {}
        return (settings.get("branch") or "main").strip() or "main"
    except Exception:  # noqa: BLE001
        return "main"


# ── Read (P0009 §4.4 / §4.5) ─────────────────────────────────────────────────

def _workflow_items(doc: dict) -> list[dict]:
    """이 문서가 속한 워크플로 시퀀스의 칸 목록. 없으면 빈 목록."""
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc.get("doc_id") or "")
        if seq is None:
            return []
        return db_wfseq.get_sequence_items(seq["id"])
    except Exception:  # noqa: BLE001 — 시퀀스를 못 읽는다고 문서를 못 열면 안 된다
        return []


def _heal_unwritten_plan(
    doc: dict, exc: wp.WorkPlanUnreadable, actor_user_id: str,
) -> Optional[dict]:
    """계획이 한 번도 쓰인 적 없는 파일을 정본 JSON 으로 되살린다 (0395 T0026 재작업).

    [빈 문서 만들기]로 만들어진 작업계획은 마크다운 머리말 뼈대만 있어서 판독기가 열지
    못한다. 그런 문서를 열 때마다 "표로 열 수 없습니다"만 돌려주면 사용자는 이미 만들어 둔
    계획을 영영 못 쓴다. 계획이 들어 있던 적 없는 파일에 한해(``is_unwritten_plan``) 이
    자리에서 채워진 정본을 만들고 문서가 그 파일을 가리키게 한다.

    원본 마크다운 파일은 지우지 않는다 — 되살리기는 덮어쓰기가 아니라 옆에 정본을 두는
    일이고, 지난 파일은 그대로 남는다.
    """
    if exc.reason != "not_json" or not wp.is_unwritten_plan(exc.raw):
        return None
    path = _canonical_path(doc)
    body = wp.auto_plan_body(doc.get("project_id"), _workflow_items(doc))
    try:
        wp.write_body_atomically(path, body)
    except OSError:
        return None
    relative = storage_paths.to_storage_relative(path, doc.get("project_id"))
    try:
        document_service.update_document(
            doc.get("doc_id") or "", {"file_path": relative}, actor_user_id=actor_user_id,
        )
    except Exception as exc_update:  # noqa: BLE001 — 파일은 이미 살아났다
        import LogAssist.log as logger
        logger.warning(f"[work-plan] file_path repoint skipped ({doc.get('doc_id')}): {exc_update}")
    doc["file_path"] = relative
    return body


@router.get("/{doc_id}/work-plan")
@require_permission("perm_document_read")
def get_work_plan(
    request: Request,
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    locale = _locale(request)
    doc = _load_doc(doc_id)
    try:
        body = wp.load_body(_plan_path(doc))
    except wp.WorkPlanUnreadable as exc:
        body = _heal_unwritten_plan(doc, exc, current_user["user_id"])
        if body is None:
            return _unreadable_response(doc, exc, locale)
    return _read_view(doc, body)


# ── Save (P0009 §4.6 ~ §4.8) ─────────────────────────────────────────────────

@router.put("/{doc_id}/work-plan")
@require_permission("perm_document_update")
def save_work_plan(
    request: Request,
    doc_id: str,
    body: WorkPlanSave,
    current_user: dict = Depends(get_current_user),
):
    locale = _locale(request)
    doc = _load_doc(doc_id)

    from modules.flow_gate.documents.routers.documents import (
        _reject_if_group_ai_running,
        _reject_if_group_disposed,
    )
    _reject_if_group_disposed(doc)
    _reject_if_group_ai_running(doc)

    final_approved = document_service.is_final_approved(doc)
    if not document_service.is_document_editable(doc, final_approved=final_approved):
        raise HTTPException(
            status_code=422,
            detail="Modification not allowed after final approval."
            if final_approved else
            f"Modification not allowed for status: {doc.get('status')}",
        )

    # 결정 6 (P0009 §4.8): validate BEFORE the revision check. The other order makes a
    # user throw away their edits with [새로 읽기] only to be told the values were
    # invalid anyway — two rounds of wasted work for one mistake.
    try:
        plan = wp.validate(body.body, project_id=doc.get("project_id"), action="save")
    except wp.WorkPlanValidationError as exc:
        return _validation_response(exc, locale)

    current_revision = doc.get("revision_no", 0) or 0
    if body.base_revision_no != current_revision:
        conflict_copy = {
            "ko": "다른 사람이 이미 저장했습니다. 새로 읽어 오세요.",
            "en": "Someone else already saved this plan. Re-read it.",
            "ja": "他の人が既に保存しました。読み直してください。",
        }
        return JSONResponse(status_code=409, content={
            "code": "wp_revision_conflict",
            "message": conflict_copy.get(locale, conflict_copy["ko"]),
            "base_revision_no": body.base_revision_no,
            "current_revision_no": current_revision,
            # 결정 7: the other side's BODY is not shipped — there is no merge, so the
            # screen has nothing to do with it. Who and when is what decides the next click.
            "updated_by": _last_editor(doc),
            "updated_at": doc.get("updated_at"),
        })

    path = _plan_path(doc)
    backup_rel: Optional[str] = None
    if path.exists():
        revisions_dir = path.parent / "revisions"
        try:
            revisions_dir.mkdir(parents=True, exist_ok=True)
            backup = revisions_dir / f"{doc_id}.r{current_revision}{path.suffix or '.json'}"
            backup.write_bytes(path.read_bytes())
            backup_rel = storage_paths.to_storage_relative(backup, doc.get("project_id"))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Storage error: {exc}")

    try:
        wp.write_body_atomically(path, plan)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Storage error: {exc}")

    now = now_iso()
    store = get_store()
    store._execute(
        "UPDATE documents SET revision_no = revision_no + 1, updated_at = ?, "
        "file_path = ? WHERE doc_id = ? AND revision_no = ?",
        [now, storage_paths.to_storage_relative(path, doc.get("project_id")),
         doc_id, current_revision],
    )
    refreshed = db_docs.get_by_id(doc_id)
    if refreshed is None or refreshed.get("revision_no") != current_revision + 1:
        raise HTTPException(status_code=409, detail="Concurrent modification conflict. Please retry.")
    new_revision = refreshed["revision_no"]

    if backup_rel:
        try:
            db_revisions.create({
                "doc_id": doc_id,
                "revision_no": current_revision,
                "backup_path": backup_rel,
                "edit_reason": "user_comment",
                "linked_doc_id": None,
                "created_by": current_user["user_id"],
                "created_at": now,
            })
        except Exception as exc:  # noqa: BLE001 — the save already committed
            import LogAssist.log as logger
            logger.warning(f"[work-plan] revision row failed (ignored): {exc}")

    try:
        db_events.create({
            "event_type": "doc_edited",
            "project_id": doc.get("project_id"),
            "group_id": doc.get("group_id"),
            "document_id": None,
            "actor_user_id": current_user["user_id"],
            "from_state": None,
            "to_state": None,
            "metadata": _json.dumps({
                "doc_id": doc_id, "edit_reason": "work_plan_saved",
                "revision_no": new_revision,
            }, ensure_ascii=False),
        })
    except Exception as exc:  # noqa: BLE001
        import LogAssist.log as logger
        logger.warning(f"[work-plan] event failed (ignored): {exc}")

    _emit(refreshed, "updated",
          {"doc_id": doc_id, "type": WORK_PLAN_TYPE, "title": refreshed.get("title"),
           "status": refreshed.get("status"), "revision_no": new_revision},
          current_user["user_id"])

    providers = _providers(doc.get("project_id") or "")
    # §4.6: saving never changes the review state — pending stays pending.
    return {
        "ok": True,
        "doc_id": doc_id,
        "revision_no": new_revision,
        "updated_at": now,
        "updated_by": current_user["user_id"],
        "doc_review_status": refreshed.get("doc_review_status"),
        "unassigned_step_count": wp.unassigned_step_count(plan),
        "assignment_summary": wp.assignment_summary(plan, providers),
        "totals": wp.totals(plan),
    }


# ── Suggestion (P0009 §4.9) ──────────────────────────────────────────────────

@router.post("/{doc_id}/work-plan/suggest")
@require_permission("perm_document_read")
def suggest_work_plan(
    request: Request,
    doc_id: str,
    body: WorkPlanSuggest = Body(default=WorkPlanSuggest()),
    current_user: dict = Depends(get_current_user),
):
    """Return a scoped, unsaved proposal from the project's existing assignment map."""
    locale = _locale(request)
    doc = _load_doc(doc_id)
    try:
        plan = wp.load_body(_plan_path(doc))
    except wp.WorkPlanUnreadable as exc:
        return _unreadable_response(doc, exc, locale)

    project_id = doc.get("project_id") or ""
    providers = {p["id"]: p for p in _providers(project_id)}
    candidate_ids = {
        str(entry.get("provider_id")) for entry in (plan.get("provider_candidates") or [])
        if entry.get("provider_id")
    }
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
        provider_ids = set(candidate_ids)
    else:
        quantity_codes = list(scope.get("quantity_type_codes") or [])
        step_keys = list(scope.get("step_keys") or [])
        provider_ids = set(scope.get("provider_ids") or [])
        invalid = (
            [code for code in quantity_codes if code not in counted_types]
            + [key for key in step_keys if key not in step_by_key or step_by_key[key].get("locked")]
            + [provider_id for provider_id in provider_ids if provider_id not in candidate_ids]
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


# ── Apply preview / apply / journal (P0009 §7·§8) ───────────────────────────

def _preview_sync(doc_id: str, body: WorkPlanApplyPreview, locale: str) -> dict:
    doc = _load_doc(doc_id)
    plan = wp.load_body(_plan_path(doc))
    return wpa.preview(
        doc=doc,
        plan=plan,
        providers=_providers(doc.get("project_id") or ""),
        instruction_mode=body.instruction_mode,
        locale=locale,
    )


@router.post("/{doc_id}/work-plan/apply/preview")
@require_permission("perm_document_read")
async def preview_work_plan_apply(
    request: Request,
    doc_id: str,
    body: WorkPlanApplyPreview,
    current_user: dict = Depends(get_current_user),
):
    # All DB/file work, including helpers reached from the service, runs off the event loop.
    return await anyio.to_thread.run_sync(
        partial(_preview_sync, doc_id, body, _locale(request))
    )


def _apply_sync(
    doc_id: str,
    body: WorkPlanApply,
    locale: str,
    applied_by: str,
) -> dict:
    doc = _load_doc(doc_id)
    plan_path = _plan_path(doc)
    plan = wp.load_body(plan_path)
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
        }
        payload = dict(exc.payload)
        payload["message"] = copy[exc.code].get(locale, copy[exc.code]["ko"])
        return JSONResponse(status_code=409, content=payload)


@router.post("/{doc_id}/work-plan/apply")
@require_permission("perm_document_update")
async def apply_work_plan(
    request: Request,
    doc_id: str,
    body: WorkPlanApply,
    current_user: dict = Depends(get_current_user),
):
    return await anyio.to_thread.run_sync(partial(
        _apply_sync,
        doc_id,
        body,
        _locale(request),
        current_user["user_id"],
    ))


def _applications_sync(doc_id: str, limit: int) -> dict:
    doc = _load_doc(doc_id)
    return wpa.read_applications(_plan_path(doc), doc_id, limit)


@router.get("/{doc_id}/work-plan/applications")
@require_permission("perm_document_read")
async def get_work_plan_applications(
    doc_id: str,
    limit: int = 20,
    current_user: dict = Depends(get_current_user),
):
    return await anyio.to_thread.run_sync(partial(_applications_sync, doc_id, limit))
