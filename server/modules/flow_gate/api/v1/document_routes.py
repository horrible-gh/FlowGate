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

from modules.flow_gate.db import conversation_turns as conversation_turn_store
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.storage.paths import resolve_storage_path
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.services.q_service import get_answers_for_document
from modules.flow_gate.services import conversation_markdown_service
from modules.flow_gate.services import document_outline_service as outline_svc
from modules.flow_gate.services.conversation_turn_service import ConversationTurnError
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


def _file_content(doc: dict) -> Optional[str]:
    """Read the durable artifact used by non-live and legacy conversations."""
    file_path = doc.get("file_path")
    if not file_path:
        return None
    branch_val = doc.get("branch", "main") or "main"
    resolved = resolve_storage_path(file_path, doc.get("project_id"), branch=branch_val)
    if resolved is None:
        return None
    return resolved.read_text(encoding="utf-8")


def _uses_live_conversation_content(doc: dict) -> bool:
    """Whether a CH's database turns, rather than its snapshot, are canonical now."""
    if doc.get("type_code") != "CH":
        return False
    try:
        return conversation_turn_store.migration_state(doc["doc_id"]) == "migrated"
    except Exception as exc:  # database trouble must not make ordinary document GET fail
        logger.warning(f"[document/{doc.get('doc_id')}] failed to resolve CH state: {exc}")
        return False


def _resolve_live_content(doc: dict) -> Optional[str]:
    """Resolve the body visible now, falling back to the durable file artifact."""
    if _uses_live_conversation_content(doc):
        try:
            return conversation_markdown_service.render_markdown(doc["doc_id"])["content"]
        except ConversationTurnError as exc:
            logger.warning(
                f"[document/{doc.get('doc_id')}] live CH render failed; using file snapshot: {exc}"
            )
            try:
                return _file_content(doc)
            except OSError as file_exc:
                logger.warning(
                    f"[document/{doc.get('doc_id')}] failed to read CH file fallback: {file_exc}"
                )
                return None
    return _file_content(doc)


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

    file_path = document.get("file_path")
    try:
        content = _resolve_live_content(document)
    except OSError:
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


# ── 0370 R0001 / P0002 / L0003: 부분 조회 네 갈래 ────────────────────────────────
#
# 지금까지 문서를 열면 본문 전체가 한꺼번에 딸려 왔다. 아래 네 엔드포인트는 필요한
# 부분만 가져오는 길이며 **전부 추가**다 — 기존 `GET /document/{doc_id}` 의 응답에서
# 필드를 빼거나 뜻을 바꾸지 않는다(P0002 §0).
#
# 계산은 한 줄도 여기 두지 않는다. 목차·구간·좌표는 전부 document_outline_service 가
# 하고, 이 파일은 그 결과를 P0002 가 정한 모양으로 옮겨 담기만 한다. 같은 이름의 숫자가
# 화면마다 달라지지 않게 하려는 것이다(L0003 목적).


def _fail_with(status: int, message: str, extra: Optional[dict] = None) -> JSONResponse:
    """실패 응답에 P0002 가 요구한 부가 필드(후보·개정판 번호 등)를 덧붙인다."""
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
    """현재 문서 GET과 동일한 정본을 부분 조회용 텍스트로 만든다."""
    content = _resolve_live_content(doc)
    if content is None:
        return None
    return outline_svc.DocumentText.from_raw(content)


def _load_for_query(request: Request, doc_id: str, revision_no: Optional[int]):
    """조회 네 갈래의 공통 앞부분 — L0003 §4-1 의 검사 순서를 그대로 따른다.

    1 토큰 → 2 doc_id 형식 → 3 문서 존재 → 6 revision_no 대조. 본문 읽기(7)는 그
    뒤이지만, 409 응답이 ``content_sha256`` 을 실어야 하므로(P0002 시나리오 8) 파일은
    먼저 읽어 둔다. 읽지 못했으면 지문만 null 이 되고 순서는 유지된다.

    **6번(409)이 8번(404)보다 앞**인 것이 이 순서의 핵심이다. 손에 든 위치가 낡았을 때
    "그런 구간 없음" 이라고 답하면 작업자는 제목이 지워진 줄 알고 엉뚱한 곳을 뒤진다.
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

    try:
        text = _document_text(doc)
    except OSError:
        return _fail_with(500, "An error occurred while reading the document content"), None, None
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
    """목차 조회 (P0002 시나리오 1·2). 본문 글자는 한 자도 담지 않는다.

    제목이 하나도 없는 문서는 200 + 빈 ``items`` 다. 404 로 답하면 작업자가 문서 자체가
    없는 줄 안다 — 요건 문서 R 은 대개 짧은 산문이라 흔한 경우다(P0002 시나리오 2).
    """
    err, doc, text = _load_for_query(request, doc_id, revision_no)
    if err is not None:
        return err
    if text is None:
        return _fail(404, f"document body is not readable: {doc_id}")

    # max_level 은 표시 깊이일 뿐이라 범위를 벗어나도 멈춰 세우지 않고 자른다. 무인
    # 작업에서 목차 한 번을 422 로 되돌리는 편익이 없다.
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
    """구간 읽기 (P0002 시나리오 3~8).

    ``section``·``section_id``·``lines``·``chars`` 중 **정확히 하나**만 보낸다. 이름으로
    찾든 줄 번호로 찾든 같은 로케이터가 나오므로 목차 → 구간 읽기 → 검색 결과를 서로 이어
    쓸 수 있다.

    상한을 넘으면 **줄 가운데를 자르지 않고** 마지막으로 끝난 줄까지만 주고, 이어 읽을
    ``next_locator`` 를 함께 준다. 그래서 ``chars`` 가 ``max_chars`` 보다 작을 수 있다.
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
        # 이어 읽기는 같은 구간을 가리킨 채 시작 줄만 민다. 끝은 원래 구간의 끝이다.
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
        # text 에는 제목 줄 자신도 들어간다 — 받아서 그대로 붙여 넣으면 원문이 되도록.
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
    """본문 뺀 정보만 조회 (P0002 시나리오 12).

    기존 ``GET /document/{doc_id}`` 응답에서 ``content`` 만 빼고 ``answers_count``·``body``
    를 더한 모양이다. **``content`` 키 자체가 없다** — ``null`` 로 두면 "본문이 빈 문서"
    와 구별이 안 된다.

    파일이 없거나 읽히지 않아도 200 이다. 문서 카드(제목·상태·검토 상태·개정판)는 본문과
    무관하게 그릴 수 있어야 하기 때문이다.
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
    # 답의 내용이 필요하면 기존 조회를 쓰면 된다. 여기서는 본문을 빼는 게 목적이라 개수만.
    resp["answers_count"] = len(get_answers_for_document(doc_id) or [])
    resp["body"] = body
    return JSONResponse(content=resp)


_RELATIONS_REFERENCED_BY_MAX = 50


def _doc_brief(doc_id: Optional[str]) -> Optional[dict]:
    """관계 응답에 싣는 문서 한 줄. 가리키는 문서가 지워졌으면 id 만 남는다."""
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
    """묶음 안에서 몇 번째 문서인가. ``seq`` 컬럼이 비었으면 doc_id 꼬리에서 읽는다."""
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
    """워크플로의 어느 칸인지. 결정이 없으면 전부 null 인 한 벌을 돌려준다."""
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    try:
        sequence = db_wfseq.get_sequence_for_member_doc(doc_id)
    except Exception:  # noqa: BLE001 — 관계 조회가 워크플로 때문에 죽으면 안 된다
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
        # 이 문서가 아직 어느 칸의 결과물로 등록되지 않았다(예: 워크플로를 소유한 R 자신).
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
    """관계 조회 (P0002 시나리오 13). **본문은 읽지 않는다.**

    이 문서가 무엇에서 나왔고(``triggered_by``), 무엇을 가리키며(``target``), 거꾸로
    이 문서를 가리키는 문서가 무엇이고(``referenced_by``), 같은 묶음의 앞뒤가 무엇이며,
    워크플로의 어느 칸인지를 모아 준다. 새 표도 새 컬럼도 쓰지 않고 이미 있는 값만 읽는다.
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
        # 개정 이력만이다. 각 개정판의 본문은 여기서 주지 않는다.
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

    file_path = doc.get("file_path")
    try:
        content = _resolve_live_content(doc)
    except OSError:
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
