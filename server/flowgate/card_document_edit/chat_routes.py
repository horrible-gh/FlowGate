"""CLI chat HTTP transport. Manual body parsing keeps authentication ahead of 422."""
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from flowgate.card_documents import legacy_adapter as legacy
from . import chat_adapter, chat_authz

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
