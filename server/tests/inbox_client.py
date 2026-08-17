"""Post to POST /inbox the way a caller does (flowgate.default.0394 T0004, NR0003 §13-5).

`inbox()` used to be a plain `async def` that awaited `_handle_new` / `_handle_review` /
`_handle_edit` / `_handle_test_run` directly, so tests reached for the inner function and
drove it with `asyncio.run(...)`. Then the event-loop-blocking work turned those four into
ordinary `def`s handed to `anyio.to_thread.run_sync`. Nothing about the HTTP contract moved
— same body in, same status and JSON out — but every test that had bound itself to the
inner function's sync/async shape broke at once (NR0003 §6.2-가: 8 of the 28 server
failures, none of them a product defect).

So go in through the door. `post_inbox()` builds a one-route app and posts, which is what
the product's own callers do; the handlers stay free to be sync, async, threaded or split
in two without a single test noticing.

The response is an httpx Response: read `.status_code` and `.json()` (the old direct calls
returned a starlette JSONResponse, whose payload had to be read as `json.loads(resp.body)`).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient


def inbox_app() -> FastAPI:
    """A FastAPI app carrying only the inbox router."""
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return app


def post_inbox(body: dict[str, Any], raw_token: str = "raw", headers: dict[str, str] | None = None) -> Any:
    """POST `body` to /api/v1/inbox with a bearer token, returning the httpx Response.

    The token is whatever the test's `token_service.verify` patch expects to see; the
    default matches the "raw" placeholder those tests already used. `headers` (e.g.
    {"x-locale": "en"}) are merged in alongside Authorization (T0004 locale regression
    tests).
    """
    with TestClient(inbox_app()) as client:
        return client.post(
            "/api/v1/inbox",
            json=body,
            headers={"Authorization": f"Bearer {raw_token}", **(headers or {})},
        )
