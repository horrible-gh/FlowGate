"""P0008 §1.2 / §1.3 response envelopes and request context for the Graph API."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from modules.flow_gate.graph_workflow.services.errors import FlowLockTimeout, GraphApiError

from .graph_messages import fill_issue_messages
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services.auth_outbound import verify_bearer

_log = logging.getLogger(__name__)

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
READ_PERMISSION = "perm_document_read"
WRITE_PERMISSION = "perm_document_update"

_MESSAGES = {
    "unauthorized": {"ko": "인증이 필요합니다.", "en": "Authentication is required.", "ja": "認証が必要です。"},
    "token_kind_not_allowed": {
        "ko": "작업 토큰으로는 이 API를 호출할 수 없습니다.",
        "en": "A worker token cannot call this API.",
        "ja": "作業トークンではこの API を呼び出せません。",
    },
    "flow_access_denied": {
        "ko": "이 플로우에 대한 권한이 없습니다.",
        "en": "You do not have permission for this flow.",
        "ja": "このフローへの権限がありません。",
    },
    "flow_not_found": {"ko": "Flow를 찾을 수 없습니다.", "en": "Flow not found.", "ja": "フローが見つかりません。"},
    "draft_not_found": {
        "ko": "편집 중인 초안이 없습니다.", "en": "There is no draft being edited.", "ja": "編集中の下書きがありません。",
    },
    "flow_disposed": {
        "ko": "폐기된 Flow는 변경할 수 없습니다.", "en": "A disposed flow cannot be changed.", "ja": "破棄されたフローは変更できません。",
    },
    "invalid_request": {
        "ko": "요청 형식이 올바르지 않습니다.", "en": "The request is malformed.", "ja": "リクエストの形式が正しくありません。",
    },
    "empty_layout": {
        "ko": "저장할 좌표나 화면 위치가 없습니다.",
        "en": "There are no positions or viewport to save.",
        "ja": "保存する座標や表示位置がありません。",
    },
    "internal_error": {
        "ko": "처리 중 오류가 발생했습니다.", "en": "An error occurred while processing.", "ja": "処理中にエラーが発生しました。",
    },
    "flow_owned_by_v01": {
        "ko": "이 Flow는 v0.1 엔진이 담당합니다. Graph 초안을 편집하거나 확정할 수 없습니다.",
        "en": "This flow is owned by the v0.1 engine. Its Graph draft cannot be edited or confirmed.",
        "ja": "このフローは v0.1 エンジンの担当です。Graph の下書きを編集・確定できません。",
    },
    "flow_ownership_conflict": {
        "ko": "Graph 배정과 v0.1 시퀀스가 함께 있는 Flow입니다. 운영자 정리가 필요합니다.",
        "en": "This flow has both a Graph assignment and a v0.1 sequence. An operator must resolve it.",
        "ja": "Graph 割り当てと v0.1 シーケンスが両方あるフローです。運用者による整理が必要です。",
    },
    "draft_version_conflict": {
        "ko": "다른 사용자가 초안을 먼저 저장했습니다. 다시 불러온 뒤 편집하세요.",
        "en": "Someone saved the draft first. Reload it and edit again.",
        "ja": "他のユーザーが先に下書きを保存しました。読み込み直してから編集してください。",
    },
    "revision_conflict": {
        "ko": "초안의 기준 개정이 최신이 아닙니다.",
        "en": "The draft is not based on the latest revision.",
        "ja": "下書きの基準リビジョンが最新ではありません。",
    },
    "revision_not_found": {
        "ko": "확정 개정을 찾을 수 없습니다.", "en": "Revision not found.", "ja": "確定リビジョンが見つかりません。",
    },
    "graph_validation_failed": {
        "ko": "그래프를 확정할 수 없습니다. 문제 {count}건을 확인하세요.",
        "en": "The graph cannot be confirmed. Check the {count} problem(s).",
        "ja": "グラフを確定できません。問題 {count} 件を確認してください。",
    },
    "feature_not_enabled": {
        "ko": "이 기능은 아직 열리지 않았습니다.", "en": "This feature is not enabled yet.", "ja": "この機能はまだ有効ではありません。",
    },
}


def server_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def locale_of(request: Optional[Request]) -> str:
    value = (request.headers.get("x-locale") if request is not None else None) or "ko"
    return value if value in ("ko", "en", "ja") else "ko"


def message(code: str, request: Optional[Request]) -> str:
    table = _MESSAGES.get(code) or _MESSAGES["internal_error"]
    return table[locale_of(request)]


def ok(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": True, "server_ts": server_ts(), **body})


def fail(request: Optional[Request], status: int, code: str, params: Optional[dict] = None,
         text: Optional[str] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "ok": False,
        "server_ts": server_ts(),
        "error": {"code": code, "message": text or message(code, request), "params": params or {}},
    })


def invalid(request: Request, errors: list[dict], message_key: str = "invalid_request") -> GraphApiError:
    return GraphApiError(422, "invalid_request", {"errors": errors}, message=message(message_key, request))


def authenticate(request: Request) -> str:
    """User access JWT only (P0008 §1.1). Returns the user id."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        # verify_bearer answers 403 only for a valid worker token lacking read permission.
        if auth.status_code == 403:
            raise GraphApiError(403, "token_kind_not_allowed", {"token_kind": "worker"})
        raise GraphApiError(401, "unauthorized")
    if not auth.get("_is_user_jwt"):
        raise GraphApiError(403, "token_kind_not_allowed", {"token_kind": "worker"})
    user_id = auth.get("issued_to") or auth.get("sub")
    if not user_id:
        raise GraphApiError(401, "unauthorized")
    return str(user_id)


def authorize(user_id: str, group: dict, required: str) -> None:
    permission = READ_PERMISSION if required == "read" else WRITE_PERMISSION
    if not has_permission(user_id, group.get("project_id") or "", permission):
        raise GraphApiError(403, "flow_access_denied", {"flow_id": group["group_id"], "required": required})


def client_id_of(request: Request) -> Optional[str]:
    raw = request.headers.get("x-canvas-client-id")
    if raw is None:
        return None
    if not CLIENT_ID_RE.match(raw):
        raise invalid(request, [{"field": "header.x-canvas-client-id", "reason": "invalid_format"}])
    return raw


def run(request: Request, handler: Callable[[], dict], status: int = 200) -> JSONResponse:
    """Call the handler and map every outcome onto the P0008 envelope."""
    try:
        return ok(handler(), status)
    except GraphApiError as exc:
        params, text = exc.params, exc.message
        if exc.code == "graph_validation_failed":
            params = {**params, "issues": fill_issue_messages(params.get("issues") or [], locale_of(request))}
            text = text or message(exc.code, request).format(count=len(params["issues"]))
        return fail(request, exc.status, exc.code, params, text)
    except (FlowLockTimeout, Exception) as exc:  # noqa: BLE001 — L0009 E-02/E-03: rolled back, 500
        trace_id = f"gw_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
        _log.exception("graph api failure trace_id=%s: %s", trace_id, exc)
        return fail(request, 500, "internal_error", {"trace_id": trace_id})
