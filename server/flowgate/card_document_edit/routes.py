"""P#1 user-JWT transport, mounted under /flowgate by the composition root."""
import json
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from flowgate.card_documents import legacy_adapter as legacy
from .contracts import Actor, Origin, ReviseRequest, PROJECTION_FAILED
from .revise import revise
from .read import read_card
from .validation import parse_body
from .errors import invalid, internal_error
from . import contacts

router = APIRouter(prefix="/api/v2", tags=["CardDocumentEdit"])
BASE = "/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document"


@router.get(BASE)
def get_document(flow_id: str, node_key: str, slot: str, request: Request):
    def handle():
        actor = Actor(legacy.authenticate(request))
        return read_card(flow_id, node_key, slot, actor, Origin())
    return legacy.run(request, handle)


@router.post(BASE + "/revisions")
async def post_revision(flow_id: str, node_key: str, slot: str, request: Request):
    raw = await request.body()
    def handle():
        actor = Actor(legacy.authenticate(request))  # authenticate before JSON/object validation
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise invalid("invalid_request", "body", "invalid_format")
        body = parse_body(body)
        req = ReviseRequest(flow_id, node_key, slot, body.get("expected_doc_id"),
                            body.get("expected_revision"), body.get("content"), actor,
                            idempotency_key=body.get("idempotency_key"), locale=legacy.locale_of(request))
        outcome = revise(req)
        if outcome.contacts == "NORMAL":
            contacts.run(outcome.plan)
        elif outcome.contacts == "REPLAY":
            contacts.run_replay(outcome.plan)
        else:
            raise internal_error()
        if outcome.line is PROJECTION_FAILED:
            raise internal_error()
        return {**outcome.result, "line": outcome.line}
    return await run_in_threadpool(legacy.run, request, handle)
