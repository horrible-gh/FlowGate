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
from modules.flow_gate.numbering import numbering_service
from modules.flow_gate.services import work_plan_service as wp
from modules.flow_gate.services import work_plan_apply_service as wpa
from modules.flow_gate.services import work_plan_sequence_service as wpseq
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


class WorkPlanSequenceCandidates(BaseModel):
    # Not an enum and not defaulted on purpose: L0011 §4.2 refuses anything that is not one
    # of the two modes rather than quietly picking one, because a silently defaulted mode
    # would rewrite a sequence the person never chose to rewrite.
    mode: str


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
    # 0403 NR0004 F3 — 경로 계산은 work_plan_service 한 곳에 있다. 시퀀스 저장이 적용
    # 이력을 남길 때도 같은 함수를 부르므로, 두 경로가 갈라질 자리가 없다.
    return wp.plan_path_for_doc(doc)


def _canonical_path(doc: dict):
    return wp.canonical_path_for_doc(doc)


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


# 0403 NR0004 F1 — 문서 하나의 저장을 한 번에 하나만 하게 하는 어드바이저리 잠금.
# 문서 번호 예약(numbering_service._get_lock)과 같은 방식이고 같은 이유다: 이 서버가
# 지키는 불변식 가운데 "두 요청이 동시에 들어와도 하나만 이긴다"에 해당하는 것들은 모두
# 이 인프로세스 잠금에 기대고 있다. 잠금은 문서마다 따로여서, 서로 다른 계획을 저장하는
# 요청끼리는 기다리지 않는다.
_save_locks: dict[str, threading.Lock] = {}
_save_locks_meta = threading.Lock()


def _plan_save_lock(doc_id: str) -> threading.Lock:
    with _save_locks_meta:
        lock = _save_locks.get(doc_id)
        if lock is None:
            lock = threading.Lock()
            _save_locks[doc_id] = lock
        return lock


def _revision_conflict_response(
    doc: dict, locale: str, base_revision_no: Optional[int], current_revision: int,
) -> JSONResponse:
    """"이미 다른 사람이 저장했다"는 한 가지 대답 (P0009 §4.7 결정 7).

    0403 NR0004 F1·F6: 저장 경로의 앞선 검사, 리비전 선점(CAS)에서 진 경우, 그리고 제안
    경로의 낡은 기준까지 같은 본문을 쓴다. 화면은 이 code 하나만 보고 [새로 읽기] 띠를
    띄우므로, 같은 사건이 자리마다 다른 모양으로 오면 안 된다.
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
        # 결정 7: the other side's BODY is not shipped — there is no merge, so the
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


def _read_view(doc: dict, body: dict) -> dict:
    providers = _providers(doc.get("project_id") or "")
    meta = {}
    try:
        meta = _json.loads(doc.get("meta") or "{}") or {}
    except Exception:  # noqa: BLE001
        meta = {}
    plan_meta = meta.get("work_plan") or {}
    applications = wpa.read_applications(_plan_path(doc), doc.get("doc_id") or "", limit=1)
    # 0403 NR0004 F7 — 편집 가능 여부는 화면이 따로 계산하지 않는다.
    # 화면은 doc_review_status == 'approved' 하나만 보고 표 전체를 잠갔고, 서버는 그룹
    # 최종승인과 종료 상태를 봤다. 그래서 "화면은 잠겼는데 API/AI 는 고칠 수 있는" 계획과
    # "화면은 열렸는데 저장이 422 로 막히는" 계획이 동시에 존재했다. 판정은 여기 한 곳에서
    # 하고 화면은 그 답을 받아 쓴다.
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
        # 0411 T0004 (B0001 "각 프로바이더는 전체 프로바이더에서 바꿀수 있게"): 255행에서
        # 이미 읽어 둔 등록 목록을 응답에 싣는다. 없으면 편집기는 계획에 얼어붙은 후보
        # 스냅샷 말고는 고를 것을 알지 못하고, 후보 밖 공급자의 이름도 못 그린다.
        # 계획 본문과 같은 시점에 읽힌 목록이라 화면에서 두 값이 어긋날 자리가 없다.
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
        "assignment_summary": wp.assignment_summary(body, providers),
        "unassigned_step_count": wp.unassigned_step_count(body),
        "totals": wp.totals(body),
        # 0406 T0022 작업 6: 화면이 200 을 따로 들고 있다가 경고 없이 입력을 막던 자리.
        # 상한은 서버가 말해 주고, 편집기는 그 값으로 남은 글자 수를 그린다.
        "limits": {"note_max_chars": wp.NOTE_MAX_CHARS},
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
    # 0405 T0011 rev2 (반려: "AI공급자 선택할게 없으면 [2 후보공급자]는 안나오게 하고 1만
    # 선택하고 생성할수 있게 해야하지 않겠니?"): 등록된 공급자가 하나도 없는 프로젝트에서는
    # 고를 방법 자체가 없으므로 빈 후보를 받는다 — 그런 프로젝트에서만이다. 고를 수 있는데
    # 비워 보내면 지금까지대로 반려한다. 후보가 비어도 되는 것은 정본 규칙과도 어긋나지
    # 않는다 (work_plan_service: "후보가 비어 있으면 steps[].provider_id 를 비워 둡니다").
    # 0411 T0004: 후보의 뜻이 "AI 에게 맡길 때 고를 수 있는 범위"로 좁아졌지만 이 판정은
    # 그대로 둔다. 사람이 단계마다 고르는 것은 이제 등록 목록 전체에서 자유롭고, 여기서
    # 묻는 것은 "AI 에게 맡길 범위를 하나도 안 골랐는가"다 — 고를 수 있는데 비워 보내면
    # 그 계획은 AI 위임 창의 [전체]가 아무도 고르지 않는 계획이 된다.
    registered_providers = _providers(parent.get("project_id"))
    if not body.provider_candidates and registered_providers:
        errors.append(wp.empty_selection_error("provider_candidates"))
    # 0405 NR0006 §3.2: this pre-check used to demand >= 1 for EVERY entry, which
    # contradicted the canonical rule (work_plan_service.COUNT_MIN = 0, and a count of 0
    # simply produces no step). The screen always sends the full countable key set with 0
    # for the types nobody picked, so a perfectly normal partial selection — the very
    # {DS:1, D:0, T:1} shape WorkPlanCreateDialog.spec.ts pins — was rejected with 422.
    # The canonical validator below still enforces the real 0..20 range.
    invalid_quantities = {
        code: count for code, count in body.quantities.items()
        if isinstance(count, bool) or count < wp.COUNT_MIN
    }
    if invalid_quantities:
        raise HTTPException(
            status_code=422,
            detail=f"quantities values must be integers greater than or equal to {wp.COUNT_MIN}.",
        )
    if errors:
        return _validation_response(
            wp.WorkPlanValidationError(errors, action="create"), locale
        )

    project_id = parent.get("project_id")
    group_id = parent.get("group_id")
    module = parent.get("module") or "none"

    candidates = wp.snapshot_candidates(list(body.provider_candidates), registered_providers)
    # flowgate.default.0423 T0005 items 6-7: an explicit request value still wins, but a
    # type the request left out of `quantities` gets the group's workflow_type_counts
    # derivation instead of initial_body's own 0 fallback, when one exists. This is the
    # same derivation the WP editor's AI-scope suggestion already reuses (work_plan.py
    # suggest_work_plan, work_plan_service.auto_plan_body) — not a new inference.
    derived_quantities = wp.workflow_type_counts(_workflow_items(parent))
    merged_quantities = {**derived_quantities, **dict(body.quantities)}
    plan = wp.initial_body(
        list(body.counted_types),
        candidates,
        project_id,
        quantities=merged_quantities,
        defaults=dict(body.defaults),
        type_providers=dict(body.type_providers),
    )
    try:
        plan = wp.validate(plan, project_id=project_id, action="create")
    except wp.WorkPlanValidationError as exc:
        return _validation_response(exc, locale)

    # 0403 NR0004 F8 — 제목 길이 검사는 문서 번호를 예약하기 전에 한다.
    # 예약은 되돌릴 수 없다. 뒤에서 422 를 던지면 그 번호는 아무 문서도 갖지 않은 채 남아
    # 그룹의 문서 번호에 구멍이 생긴다. 요청만 보고 판정할 수 있는 검사는 전부 예약 앞에.
    requested_title = (body.title or "").strip()
    if len(requested_title) > 100:
        raise HTTPException(status_code=422, detail="Title must be 100 characters or fewer.")

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
    title = requested_title or doc_id

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

    # 0403 NR0004 F1 — "읽고 · 확인하고 · 파일을 쓰고 · 리비전을 올린다"를 한 덩어리로 묶는다.
    #
    # 묶기 전에는 이랬다: 요청 두 개가 같은 리비전을 읽고 나란히 앞의 검사를 통과한 뒤, 둘 다
    # 같은 정본 파일을 쓰고, 마지막에 DB 리비전 경쟁에서 한쪽만 이긴다. 진 쪽이 파일을 나중에
    # 썼다면 사용자에게는 "저장 실패(409)"라고 답해 놓고 디스크의 정본은 그 사람의 본문이다.
    # 이후의 읽기도 붓기도 전부 이 파일을 읽으므로, DB 리비전의 주인과 실제로 적용되는 계획
    # 내용이 갈라진 채로 남는다.
    #
    # 잠금 방식은 문서 번호 예약(numbering_service)과 같다 — 문서 하나에 대한 인프로세스
    # 어드바이저리 잠금. 잠금 안에서 리비전을 다시 읽으므로, 기다렸다 들어온 요청은 자기가
    # 들고 있던 base_revision_no 가 이미 낡았다는 것을 파일에 손대기 전에 알게 된다.
    with _plan_save_lock(doc_id):
        fresh = db_docs.get_by_id(doc_id) or doc
        current_revision = fresh.get("revision_no", 0) or 0
        if body.base_revision_no != current_revision:
            return _revision_conflict_response(
                fresh, locale, body.base_revision_no, current_revision,
            )

        path = _plan_path(fresh)
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

        # 잠금 안이어도 리비전 증가를 파일 쓰기보다 앞에 둔다. 이 서버가 여러 프로세스로
        # 뜨는 날에도 "리비전을 못 가져간 요청은 파일에 손대지 않는다"가 남는다.
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
            # 리비전을 가져가지 못했다. 파일은 이 요청이 손대지 않았고, 앞으로도 손대지 않는다.
            return _revision_conflict_response(
                refreshed or fresh, locale, body.base_revision_no, current_revision,
            )
        new_revision = refreshed["revision_no"]

        try:
            wp.write_body_atomically(path, plan)
        except OSError as exc:
            # 리비전만 오르고 본문은 옛것인 상태를 남기지 않는다. 되돌리기가 실패해도 파일은
            # 여전히 이전 본문이므로 데이터는 일관적이다 — 리비전 번호만 한 칸 앞선다.
            try:
                store._execute(
                    "UPDATE documents SET revision_no = ?, updated_at = ? "
                    "WHERE doc_id = ? AND revision_no = ?",
                    [current_revision, fresh.get("updated_at"), doc_id, new_revision],
                )
            except Exception as revert_exc:  # noqa: BLE001
                import LogAssist.log as logger
                logger.warning(f"[work-plan] revision revert failed ({doc_id}): {revert_exc}")
            raise HTTPException(status_code=500, detail=f"Storage error: {exc}")

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
    # 0403 NR0004 F6 — 요청이 보낸 base_revision_no 를 실제로 검사한다.
    # 이 라우트는 화면이 보내 준 범위를 디스크의 정본에 대고 검증한다. 그런데 화면은 아직
    # 저장하지 않은 계획을 들고 있을 수 있어서, 화면에서 수량을 늘려 만든 정상적인 단계 키가
    # 서버 정본에는 없어 wp_scope_invalid 로 거절되곤 했다. 반대로 화면에서 지운 단계는
    # 서버에 남아 제안의 기준이 화면과 달랐다. 기준은 하나여야 한다 — 보낸 리비전이 서버의
    # 리비전과 다르면 제안하지 않고 저장/새로 읽기를 먼저 하라고 답한다.
    current_revision = doc.get("revision_no", 0) or 0
    if body.base_revision_no is not None and body.base_revision_no != current_revision:
        return _revision_conflict_response(doc, locale, body.base_revision_no, current_revision)
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
    # 0411 T0004 (NR0003 §6 안 B-2): AI 범위 대화상자가 이제 등록된 공급자 전체를 보여
    # 주므로, 이 게이트도 같은 집합으로 넓힌다. 좁은 채로 두면 화면의 [전체] 단추가 고른
    # 값이 그대로 422 로 되돌아온다 — "전체인데 전체가 아닌 [전체]"가 남는다.
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
        plan = wp.load_body(_plan_path(doc))
    except wp.WorkPlanUnreadable as exc:
        # L0011 §4.1-2 "plan_unreadable": a plan nobody can open as a table is a different
        # problem from a plan with nothing in it, and the person's next move differs, so it
        # keeps the reader's own 409 rather than being folded into an empty result.
        return _unreadable_response(doc, exc, locale)
    return wpseq.build_candidates(doc=doc, plan=plan, mode=body.mode, locale=locale)


@router.post("/{doc_id}/work-plan/sequence-candidates")
@require_permission("perm_document_read")
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
@require_permission("perm_document_read")
async def get_work_plan_applications(
    doc_id: str,
    limit: int = 20,
    current_user: dict = Depends(get_current_user),
):
    return await anyio.to_thread.run_sync(partial(_applications_sync, doc_id, limit))
