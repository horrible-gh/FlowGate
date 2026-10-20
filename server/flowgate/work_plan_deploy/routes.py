"""HTTP adapter of the work plan deploy (flowgate.v02.0074 T#4, D0006 §5). Under the WP API group, mounted under the
``/flowgate`` context like the other v0.2 card routes; user JWT only.

    GET  /api/v1/documents/{doc_id}/work-plan/deploy            status       (read permission)
    POST /api/v1/documents/{doc_id}/work-plan/deploy/preview    preview      {"mode", "wp_revision_no"}
    POST /api/v1/documents/{doc_id}/work-plan/deploy            execution    {"mode", "wp_revision_no", "plan_digest",
                                                                              "draft_version", "base_revision_no",
                                                                              "remove_node_keys"}

``mode``: ``first`` | ``add`` | ``cleanup``. The execution carries the preview's ``baseline`` as it came. Answers
are the WP API's: ``{"ok": true, ...}``, or ``{"ok": false, "code", "message", ...params}`` with the HTTP status;
ownership and run lock refusals keep the Graph's code names (``flow_owned_by_v01``, ``graph_run_locked``, ...).
Preview and execution need the document update permission (the draft save's), status the read permission.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from flowgate.work_plan_deploy import legacy_adapter as legacy
from flowgate.work_plan_deploy import service

router = APIRouter(prefix="/api/v1", tags=["WorkPlanDeploy"])
_log = logging.getLogger(__name__)

_EXECUTION_FIELDS = {"mode", "wp_revision_no", "plan_digest", "draft_version", "base_revision_no", "remove_node_keys"}

MESSAGES = {
    "wp_not_found": {"ko": "작업계획을 찾을 수 없습니다.", "en": "Work plan not found.", "ja": "作業計画が見つかりません。"},
    "not_a_work_plan": {"ko": "작업계획 문서가 아닙니다.", "en": "Not a work plan.", "ja": "作業計画ではありません。"},
    "wp_not_on_card": {"ko": "Designer 작업계획 카드의 계획이 아닙니다.", "en": "This plan is not on a Designer card.",
                       "ja": "Designerの作業計画カードの計画ではありません。"},
    "wp_not_editable": {"ko": "지금은 이 작업계획을 전개할 수 없습니다.", "en": "This plan cannot be deployed now.",
                        "ja": "現在この作業計画は展開できません。"},
    "wp_unreadable": {"ko": "작업계획을 읽을 수 없습니다.", "en": "The work plan cannot be read.",
                      "ja": "作業計画を読めません。"},
    "wp_changed": {"ko": "작업계획이 바뀌었습니다. 다시 미리보기 하세요.", "en": "The work plan changed. Preview again.",
                   "ja": "作業計画が変わりました。もう一度プレビューしてください。"},
    "wp_attachment_unreadable": {"ko": "사전 지시 파일을 읽을 수 없습니다.", "en": "A pre-instruction file cannot be read.",
                                 "ja": "事前指示ファイルを読めません。"},
    "deploy_already_deployed": {"ko": "이미 전개되었습니다. 재전개를 쓰세요.", "en": "Already deployed. Use redeploy.",
                                "ja": "既に展開済みです。再展開を使ってください。"},
    "deploy_not_deployed": {"ko": "아직 전개되지 않았습니다.", "en": "Not deployed yet.", "ja": "まだ展開されていません。"},
    "draft_version_conflict": {"ko": "다른 편집이 먼저 저장되었습니다. 다시 미리보기 하세요.",
                               "en": "Someone else saved the graph first. Preview again.",
                               "ja": "別の編集が先に保存されました。もう一度プレビューしてください。"},
    "deploy_blocked": {"ko": "카드 연결 때문에 전개할 수 없습니다.", "en": "The card connections block the deploy.",
                       "ja": "カードの接続のため展開できません。"},
    "deploy_preview_stale": {"ko": "미리보기 뒤에 정리 대상이 늘었습니다. 다시 확인하세요.",
                             "en": "More cards would be removed than the preview showed. Check again.",
                             "ja": "プレビュー後に整理対象が増えました。もう一度確認してください。"},
    "instruction_target_changed": {"ko": "지시 문서가 그사이 바뀌었습니다. 다시 시도하세요.",
                                   "en": "An instruction document changed meanwhile. Try again.",
                                   "ja": "指示文書がその間に変わりました。もう一度試してください。"},
    "deploy_restore_incomplete": {"ko": "전개 실패 후 일부 문서를 되돌리지 못했습니다.",
                                  "en": "Some documents could not be restored after the failed deploy.",
                                  "ja": "展開失敗後、一部の文書を元に戻せませんでした。"},
    "invalid_request": {"ko": "요청 형식이 올바르지 않습니다.", "en": "The request is not valid.",
                        "ja": "リクエストの形式が正しくありません。"},
}


def _message(code: str, request: Request, fallback: Any = None) -> str:
    table = MESSAGES.get(code)
    if table:
        return table.get(legacy.envelope.locale_of(request)) or table["ko"]
    if isinstance(fallback, str) and fallback:
        return fallback
    return legacy.envelope.message(code, request)


def _fail(request: Request, status: int, code: str, params: dict, text: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "ok": False, "code": code, "message": _message(code, request, text),
        **{k: v for k, v in (params or {}).items() if k not in ("ok", "code", "message")}})


def _run(request: Request, handler) -> JSONResponse:
    try:
        return JSONResponse(status_code=200, content={"ok": True, **handler()})
    except legacy.GraphApiError as exc:
        return _fail(request, exc.status, exc.code, exc.params or {}, exc.message)
    except Exception as exc:  # noqa: BLE001 — the guard rolled back; answer 500 with a trace id only
        _log.exception("work plan deploy failed: %s", exc)
        return _fail(request, 500, "internal_error", {})


def _flow_of(doc_id: str) -> dict:
    doc = service._wp_document(doc_id)
    return legacy.load_flow(doc["group_id"])


def _body(raw: Any, allowed: set[str]) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise service.DeployError(422, "invalid_request", {"errors": [{"field": "body", "reason": "not_an_object"}]})
    errors = [{"field": key, "reason": "unknown_field"} for key in sorted(set(raw) - allowed)]
    for name in ("wp_revision_no", "draft_version", "base_revision_no"):
        value = raw.get(name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            errors.append({"field": name, "reason": "not_a_non_negative_integer"})
    keys = raw.get("remove_node_keys")
    if keys is not None and (not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)):
        errors.append({"field": "remove_node_keys", "reason": "not_a_list_of_keys"})
    if raw.get("plan_digest") is not None and not isinstance(raw.get("plan_digest"), str):
        errors.append({"field": "plan_digest", "reason": "not_a_text"})
    if errors:
        raise service.DeployError(422, "invalid_request", {"errors": errors})
    return raw


@router.get("/documents/{doc_id}/work-plan/deploy")
def get_work_plan_deploy(doc_id: str, request: Request):
    def handle() -> dict:
        user_id = legacy.envelope.authenticate(request)
        legacy.envelope.authorize(user_id, _flow_of(doc_id), "read")
        return service.status(doc_id)

    return _run(request, handle)


@router.post("/documents/{doc_id}/work-plan/deploy/preview")
def preview_work_plan_deploy(doc_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.envelope.authenticate(request)
        payload = _body(body, {"mode", "wp_revision_no"})
        legacy.envelope.authorize(user_id, _flow_of(doc_id), "write")
        return service.preview(doc_id, payload.get("mode"), payload.get("wp_revision_no"),
                               legacy.envelope.locale_of(request))

    return _run(request, handle)


@router.post("/documents/{doc_id}/work-plan/deploy")
def execute_work_plan_deploy(doc_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.envelope.authenticate(request)
        payload = _body(body, _EXECUTION_FIELDS)
        legacy.envelope.authorize(user_id, _flow_of(doc_id), "write")
        return service.execute(doc_id, payload, user_id, legacy.envelope.locale_of(request))

    return _run(request, handle)
