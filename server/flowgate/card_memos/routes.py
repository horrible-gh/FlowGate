"""HTTP adapter of the card memos (flowgate.v02.0056 T#3). Mounted under the ``/flowgate`` context like the Graph
routes; same envelope (P0008 §1.2 / §1.3), user JWT only, the document permissions (read / create / update /
delete).

    GET    /api/v1/flows/{flow_id}/nodes/{node_key}/memos              the card's memos, oldest first
    POST   /api/v1/flows/{flow_id}/nodes/{node_key}/memos              {"title", "body"?, "ai_ref"?} -> 201
    PATCH  /api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}    any of {"title", "body", "ai_ref"}
    DELETE /api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}

A write never calls an AI and touches no document. The writes run inside the Flow guard (they order with draft
saves and run steps); a disposed Flow refuses them (409 ``flow_disposed``).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Request

from flowgate.card_memos import legacy_adapter as legacy
from flowgate.card_memos import memos, repository
from flowgate.card_settings import repository as card_settings

router = APIRouter(prefix="/api/v1", tags=["CardMemos"])


def _card(flow_id: str, node_key: str) -> dict:
    node = card_settings.card_node(flow_id, node_key)
    if node is None:
        raise legacy.GraphApiError(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key},
                                   message="The card does not exist in this flow.")
    return node


def _memo(flow_id: str, node_key: str, memo_id: str) -> dict:
    found = repository.memo(flow_id, node_key, memo_id)
    if found is None:
        raise legacy.GraphApiError(404, "memo_not_found", {"flow_id": flow_id, "node_key": node_key,
                                                           "memo_id": memo_id}, message="The memo does not exist.")
    return found


def _writable(flow_id: str) -> None:
    legacy.require_writable(flow_id)
    if not repository.available():
        raise legacy.GraphApiError(409, "card_memos_unavailable", {"flow_id": flow_id},
                                   message="Card memos are not available on this server yet.")


def _payload(flow_id: str, node_key: str, **extra: Any) -> dict:
    return {"flow_id": flow_id, "node_key": node_key, **extra}


@router.get("/flows/{flow_id}/nodes/{node_key}/memos")
def list_card_memos(flow_id: str, node_key: str, request: Request):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "read")
        _card(flow_id, node_key)
        return _payload(flow_id, node_key, memos=[memos.view(m) for m in repository.memos(flow_id, [node_key])])

    return legacy.run(request, handle)


@router.post("/flows/{flow_id}/nodes/{node_key}/memos")
def create_card_memo(flow_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        fields, errors = memos.check_create(body)
        if errors:
            raise legacy.invalid(request, errors)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "create")
        _writable(flow_id)

        def write() -> dict:
            _card(flow_id, node_key)
            memo_id = repository.insert(flow_id, node_key, fields["title"], fields["body"], fields["ai_ref"], user_id)
            return _payload(flow_id, node_key, memo=memos.view(_memo(flow_id, node_key, memo_id)))

        return legacy.with_flow_guard(flow_id, write)

    return legacy.run(request, handle, 201)


@router.patch("/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}")
def change_card_memo(flow_id: str, node_key: str, memo_id: str, request: Request,
                     body: Optional[Any] = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        fields, errors = memos.check_change(body)
        if errors:
            raise legacy.invalid(request, errors)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "update")
        _writable(flow_id)

        def write() -> dict:
            _memo(flow_id, node_key, memo_id)
            repository.update(flow_id, node_key, memo_id, fields, user_id)
            return _payload(flow_id, node_key, memo=memos.view(_memo(flow_id, node_key, memo_id)))

        return legacy.with_flow_guard(flow_id, write)

    return legacy.run(request, handle)


@router.delete("/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}")
def delete_card_memo(flow_id: str, node_key: str, memo_id: str, request: Request):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "delete")
        _writable(flow_id)

        def write() -> dict:
            _memo(flow_id, node_key, memo_id)
            repository.delete(flow_id, node_key, memo_id)
            return _payload(flow_id, node_key, memo_id=memo_id, deleted=True)

        return legacy.with_flow_guard(flow_id, write)

    return legacy.run(request, handle)
