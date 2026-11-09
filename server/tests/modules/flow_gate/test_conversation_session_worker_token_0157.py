"""A worker token sent to the session conversation routes names the worker route (flowgate.v02.0157).

The plural ``/documents/{doc_id}/conversation/turns`` and ``.../turn`` routes are session-only.
A chat worker token sent there used to get the JWT decoder's generic 401
``Invalid authentication credentials``, which reads as a bad token. The boundary is unchanged
(still 401, the service is never reached); only the 401 now says which route to use.
"""
from __future__ import annotations

from unittest.mock import patch

import jwt
from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.documents.routers import conversation_turns as routes
from modules.flow_gate.services import conversation_query_service as query
from modules.flow_gate.services import conversation_turn_service as append_service
from modules.flow_gate.services import token_service

DOC_ID = "flowgate.v02.0157.0002-CH"
WORKER_TOKEN = "fake-worker-token-0157-not-a-real-secret"
TOKEN_ROW = {"token_id": "tok_1", "action_scope": "chat", "doc_ref": DOC_ID}
GENERIC = "Invalid authentication credentials"


def _client(prefix: str = "", *, session_user: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix=prefix)
    if session_user:
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "username": "sjm"}
    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_worker_token_on_session_turns_read_names_the_worker_route():
    # No get_current_user override: the real JWT check stays mounted, so this also proves the
    # worker-token check runs before it (otherwise the generic 401 would come back).
    with (
        patch.object(token_service, "_find_token_by_raw", return_value=TOKEN_ROW) as found,
        patch.object(query, "list_turns") as listed,
    ):
        response = _client().get(
            f"/documents/{DOC_ID}/conversation/turns?after_seq=13&include_head=1",
            headers=_auth(WORKER_TOKEN),
        )
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail != GENERIC
    assert f"GET /conversation/{DOC_ID}/turns" in detail
    assert response.headers.get("www-authenticate") == "Bearer"
    found.assert_called_once_with(WORKER_TOKEN)
    listed.assert_not_called()


def test_worker_route_in_the_hint_keeps_the_mount_prefix():
    with patch.object(token_service, "_find_token_by_raw", return_value=TOKEN_ROW):
        response = _client("/flowgate/api/v1").get(
            f"/flowgate/api/v1/documents/{DOC_ID}/conversation/turns",
            headers=_auth(WORKER_TOKEN),
        )
    assert response.status_code == 401
    assert f"GET /flowgate/api/v1/conversation/{DOC_ID}/turns" in response.json()["detail"]


def test_worker_token_on_session_turn_append_names_the_worker_append_route():
    with (
        patch.object(token_service, "_find_token_by_raw", return_value=TOKEN_ROW),
        patch.object(append_service, "append_turn") as appended,
    ):
        response = _client("/flowgate/api/v1").post(
            f"/flowgate/api/v1/documents/{DOC_ID}/conversation/turn",
            json={"body": "hi", "idempotency_key": "tok_1"},
            headers=_auth(WORKER_TOKEN),
        )
    assert response.status_code == 401
    assert f"POST /flowgate/api/v1/conversation/{DOC_ID}/turn" in response.json()["detail"]
    appended.assert_not_called()


def test_unknown_non_jwt_bearer_keeps_the_generic_401():
    with (
        patch.object(token_service, "_find_token_by_raw", return_value=None),
        patch("modules.flow_gate.auth.middleware.decode_token", side_effect=jwt.InvalidTokenError()),
    ):
        response = _client().get(
            f"/documents/{DOC_ID}/conversation/turns", headers=_auth("not-a-token")
        )
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC


def test_a_failing_token_lookup_falls_back_to_the_session_answer():
    with (
        patch.object(token_service, "_find_token_by_raw", side_effect=RuntimeError("db down")),
        patch("modules.flow_gate.auth.middleware.decode_token", side_effect=jwt.InvalidTokenError()),
    ):
        response = _client().get(
            f"/documents/{DOC_ID}/conversation/turns", headers=_auth(WORKER_TOKEN)
        )
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC


def test_session_jwt_requests_never_touch_the_worker_token_table():
    page = {"ok": True, "doc_id": DOC_ID, "turns": [], "head_seq": 0, "me": None}
    with (
        patch.object(token_service, "_find_token_by_raw") as found,
        patch.object(query, "list_turns", return_value=page) as listed,
    ):
        response = _client(session_user=True).get(
            f"/documents/{DOC_ID}/conversation/turns",
            headers=_auth("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.c2ln"),
        )
    assert response.status_code == 200
    assert response.json() == page
    found.assert_not_called()
    assert listed.call_args.kwargs["actor"]["kind"] == "session"


def test_session_request_without_a_bearer_is_unchanged():
    page = {"ok": True, "doc_id": DOC_ID, "turns": [], "head_seq": 0, "me": None}
    with (
        patch.object(token_service, "_find_token_by_raw") as found,
        patch.object(query, "list_turns", return_value=page),
    ):
        response = _client(session_user=True).get(f"/documents/{DOC_ID}/conversation/turns")
    assert response.status_code == 200
    found.assert_not_called()
