"""CLI chat HTTP transport. Manual body parsing keeps authentication ahead of 422."""
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from flowgate.card_documents import legacy_adapter as legacy
from . import chat_adapter, chat_authz, chat_documents, chat_questions

router = APIRouter(prefix="/chat", tags=["ChatCardDocumentEdit"])
BASE = "/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document"


def _bearer(request):
    raw = request.headers.get("Authorization", "")
    return raw[7:].strip() if raw.startswith("Bearer ") else None


@router.get(BASE)
def get_document(flow_id: str, node_key: str, slot: str, request: Request):
    return legacy.run(request, lambda: chat_adapter.chat_read(
        chat_authz.CLI, token_raw=_bearer(request),
        coords={"flow_id": flow_id, "node_key": node_key, "slot": slot}))


@router.post(BASE + "/revisions")
async def post_revision(flow_id: str, node_key: str, slot: str, request: Request):
    raw = await request.body()
    return await run_in_threadpool(legacy.run, request, lambda: chat_adapter.chat_edit(
        chat_authz.CLI, token_raw=_bearer(request), request_body=raw,
        coords={"flow_id": flow_id, "node_key": node_key, "slot": slot}, locale=legacy.locale_of(request)))


# 0127 CH0028: doc_id routes. The server resolves card bindings; creation is a separate route.
@router.get("/documents/{doc_id}")
def get_document_by_id(doc_id: str, request: Request):
    return legacy.run(request, lambda: chat_documents.chat_read_document(
        chat_authz.CLI, token_raw=_bearer(request), doc_id=doc_id))


@router.post("/documents/{doc_id}/revisions")
async def post_document_revision(doc_id: str, request: Request):
    raw = await request.body()
    return await run_in_threadpool(legacy.run, request, lambda: chat_documents.chat_revise_document(
        chat_authz.CLI, token_raw=_bearer(request), doc_id=doc_id, request_body=raw,
        locale=legacy.locale_of(request)))


@router.post("/documents")
async def post_document(request: Request):
    raw = await request.body()
    return await run_in_threadpool(legacy.run, request, lambda: chat_documents.chat_create_document(
        chat_authz.CLI, token_raw=_bearer(request), request_body=raw, locale=legacy.locale_of(request)))


# flowgate.v02gui.0010: card questions (Q) of the chat's Flow — list, and answer on the user's behalf.
@router.get("/questions")
def get_questions(request: Request):
    return legacy.run(request, lambda: chat_questions.chat_list_questions(
        chat_authz.CLI, token_raw=_bearer(request), query=dict(request.query_params)))


@router.post("/questions/{doc_id}/items/{item_id}/answers")
async def post_question_answer(doc_id: str, item_id: str, request: Request):
    raw = await request.body()
    return await run_in_threadpool(legacy.run, request, lambda: chat_questions.chat_answer_question(
        chat_authz.CLI, token_raw=_bearer(request), doc_id=doc_id, item_id=item_id, request_body=raw,
        locale=legacy.locale_of(request)))
