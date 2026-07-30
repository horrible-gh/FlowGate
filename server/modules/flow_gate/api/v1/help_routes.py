"""API help endpoints (D021 §4-1).

GET /api/v1/help
No authentication required

GET /api/v1/help/doc_type
Authentication required (Bearer token)

GET /api/v1/help/question
Authentication required (Bearer token)
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.flow_gate import template_provision
from modules.flow_gate.db import templates as db_templates
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.utils.help_url import help_url, outbound_api_base

router = APIRouter(prefix="/api/v1", tags=["Help"])


@router.get("/help/doc_type")
def get_help_doc_type(request: Request):
    """Document type code list. Used by workers to look up the meaning of type_code (e.g. D, DS, R …).

    Auth: Bearer token required.
    Data source: DB document_types table (is_active=1, global default types).
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    locale = template_provision.normalize_locale(auth.get("continuation_locale"))
    rows = db_templates.list_document_types(project_id=None, locale=locale)
    result = [
        {
            "type_code": r["type_code"],
            "name": r["type_name"],
            "series": r["series"],
            "description": r.get("description"),
        }
        for r in rows
        if r.get("is_active", 1)
    ]
    return JSONResponse(content=result)


_QUESTION_HELP_COPY = {
    "ko": {
        "note": "질의는 Q 문서가 아니라 해당 문서의 질의 데이터로 등록합니다. 콘솔 선택지를 강요하지 마세요.",
        "titles": ("기능 범위", "질의 우선순위"),
        "bodies": (
            "R0001 본문이 한 줄이라 DS 설계 대상이 불명확합니다. 구체적 기능 범위와 인수 기준은 무엇입니까?",
            "멘트의 next_type 은 DS 인데 질의가 필요합니다. 질의를 먼저 등록할까요, 모호함을 가정하고 DS 를 작성할까요?",
        ),
    },
    "en": {
        "note": "Register a query as query data on the relevant document, not as a Q document. Do not force console choices.",
        "titles": ("Feature scope", "Query priority"),
        "bodies": (
            "The R0001 body has only one line, so the DS design scope is unclear. What are the specific feature scope and acceptance criteria?",
            "The mention says next_type is DS, but clarification is needed. Should the query be registered first, or should DS be drafted with explicit assumptions?",
        ),
    },
    "ja": {
        "note": "質問はQ文書ではなく、対象文書の質問データとして登録します。コンソールで選択肢を強制しないでください。",
        "titles": ("機能範囲", "質問の優先順位"),
        "bodies": (
            "R0001の本文が1行だけなので、DS設計の対象が不明確です。具体的な機能範囲と受入基準は何ですか。",
            "メンションのnext_typeはDSですが、確認が必要です。先に質問を登録するべきですか、それとも前提を明記してDSを作成するべきですか。",
        ),
    },
}


@router.get("/help/question")
def get_help_question(request: Request):
    """Localized query-registration guide for authenticated workers."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    locale = template_provision.normalize_locale(auth.get("continuation_locale"))
    copy = _QUESTION_HELP_COPY[locale]
    titles = copy["titles"]
    bodies = copy["bodies"]
    return JSONResponse(content={
        "note": copy["note"],
        "example": {
            "method": "POST",
            "url": "/flowgate/api/v1/q/{doc_id}/questions",
            "headers": {
                "Authorization": "Bearer <YOUR_TOKEN>",
                "Content-Type": "application/json",
            },
            "body": {
                "asker_kind": "ai",
                "questions": [
                    {"title": titles[0], "body": bodies[0]},
                    {"title": titles[1], "body": bodies[1]},
                ],
            },
        },
    })

@router.get("/help")
def get_help():
    """Single entry point for API usage. Unauthenticated access allowed."""
    return JSONResponse(content={
        "ok": True,
        "version": "v1",
        "base_url": outbound_api_base(),
        "endpoints": [
            {"method": "POST", "path": "/token/issue", "summary": "Issue a work token (screen action trigger)", "auth": "session_cookie"},
            {"method": "POST", "path": "/inbox", "summary": "Register/update an artifact (action: new | edit)", "auth": "bearer_token"},
            {"method": "GET", "path": "/list/projects", "summary": "Project list", "auth": "bearer_token"},
            {"method": "GET", "path": "/list/projects/{p}/modules", "summary": "Module list", "auth": "bearer_token", "example": "/list/projects/myproject/modules"},
            {"method": "GET", "path": "/list/projects/{p}/groups", "summary": "Group list", "auth": "bearer_token",
             "example": "/list/projects/myproject/groups?before=myproject.none.0002&limit=5",
             "params": {
                 "before": "Reference group_id — returns the N items up to and including that group (group_number descending). Ignores offset when before is set",
                 "limit": "Number of items to return (default 50)",
                 "offset": "Offset (when before is not used)",
             }},
            {"method": "GET", "path": "/list/groups/{gid}/documents", "summary": "Document list within a group", "auth": "bearer_token",
             "example": "/list/groups/myproject.none.0001/documents?before=0003-DS&limit=5",
             "params": {
                 "before": "Reference doc_id — returns the N items up to and including that document (doc_number descending). Ignores offset when before is set",
                 "limit": "Number of items to return (default 50)",
                 "offset": "Offset (when before is not used)",
             }},
            {"method": "GET", "path": "/list/doc-types", "summary": "Supported document type list", "auth": "bearer_token"},
            {"method": "GET", "path": "/document/{id}", "summary": "Document body + metadata", "auth": "bearer_token", "example": "/document/myproject.none.0001.0001-R"},
            {"method": "GET", "path": "/document/{id}/path", "summary": "Document file path", "auth": "bearer_token", "example": "/document/myproject.none.0001.0001-R/path"},
            {"method": "GET", "path": "/project/{p}/source-path", "summary": "Project source code path", "auth": "bearer_token", "example": "/project/myproject/source-path"},
            {"method": "GET", "path": "/group/{gid}/next-action", "summary": "Last/expected next action of the group", "auth": "bearer_token", "example": "/group/myproject.none.0001/next-action"},
            {"method": "GET", "path": "/workflow/{doc_id}/head", "summary": "Query current head step of the workflow sequence (including doc_class)", "auth": "bearer_token", "example": "/workflow/R016/head"},
            {"method": "GET", "path": "/workflow/{doc_id}/sequence", "summary": "Query all workflow sequence items + head", "auth": "bearer_token", "example": "/workflow/R016/sequence"},
            {"method": "POST", "path": "/workflow/{doc_id}/decide", "summary": "Save workflow decision (once at initialization, defines sequence)", "auth": "bearer_token", "example": "/workflow/R016/decide"},
            {"method": "POST", "path": "/workflow/{doc_id}/advance", "summary": "Advance to next step (number assignment + token issue + ment creation)", "auth": "bearer_token", "example": "/workflow/R016/advance"},
            {"method": "GET", "path": "/help/doc_type", "summary": "Document type code list", "auth": "bearer_token"},
            {"method": "GET", "path": "/help/question", "summary": "Query registration guide (register as document-bound query data, not a Q document)", "auth": "bearer_token"},
            {"method": "GET", "path": "/events/stream", "summary": "SSE screen push stream (screen only)", "auth": "session_cookie"},
        ],
        "notes": [
            "Every path above is relative to base_url.",
            "A worker token may call any endpoint listed here with auth=bearer_token; there is no "
            "per-path scope allowlist. Read a document body with GET /document/{id} — singular.",
            "The console UI is served by a separate, unlisted API under the same base_url whose "
            "paths are PLURAL (/documents/…). It accepts only a signed-in user session and answers "
            "a worker token with 401 'Invalid authentication credentials'. That 401 means the wrong "
            "API was called, not that the token lacks a scope.",
        ],
        "error_format": {
            "ok": False,
            "http_status": "<int>",
            "error_message": "<string>",
            "help_url": help_url(),
        },
    })
