"""Per-request DB scope: read memoization + query accounting (0291 NR0003 P3-1 / 4-8).

``db/request_cache.py`` sets the policy; this file sets the boundary. The boundary is one HTTP request.

**Why pure ASGI middleware.** Starlette's ``BaseHTTPMiddleware`` (including
``@app.middleware("http")``) runs the downstream app in a separate anyio task. A contextvar is
copied when the task is created, so propagation depends on spawn timing — a detail that can
change between Starlette versions. Pure ASGI middleware awaits ``self.app(...)`` **in the same
task**, so scope visibility is structurally guaranteed. A cache going quietly dark is the kind
of failure with no symptom at all, only lost benefit, so the guarantee was chosen here.

It propagates into sync ``def`` endpoints too: FastAPI copies the current context when handing
off to the AnyIO thread pool, and what the scope holds is a **mutable object**, so changes made
inside the thread are visible. As NR0003 finding 3 noted, most endpoints in this codebase are
sync ``def``, so without this property the cache would run nowhere.

**SSE is excluded.** The ``/api/v1/events`` stream's lifetime is a session, not a request.
Opening a scope would keep the cache alive until the connection drops, reviving exactly the
staleness risk this module removes. A streaming response is the one place that breaks the "one request = one boundary" premise.
"""
from __future__ import annotations

import logging
import os

from ..db import request_cache

_log = logging.getLogger(__name__)

# Paths that open no scope. A substring test, so it still matches with a CONTEXT prefix.
_EXCLUDED_PATH_MARKERS = ("/api/v1/events",)


def _query_log_enabled() -> bool:
    """Per-request query-count logging (NR0003 4-8).

    Off by default. One line accumulates per request, so leaving it on permanently would be as
    noisy as the SQL log it set out to reduce. Turn it on with ``FLOWGATE_QUERY_LOG=1`` only while measuring.
    """
    return os.environ.get("FLOWGATE_QUERY_LOG", "").strip() not in ("", "0", "false", "False")


class RequestScopeMiddleware:
    """One request = one cache."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(marker in path for marker in _EXCLUDED_PATH_MARKERS):
            await self.app(scope, receive, send)
            return

        label = f"{scope.get('method', '?')} {path}"
        with request_cache.request_scope(label) as rs:
            try:
                await self.app(scope, receive, send)
            finally:
                # Requests ending in an exception are counted too: the path that returns a 500 is
                # sometimes the heaviest querier, and losing the numbers there hides that fact.
                if _query_log_enabled() and rs.reads:
                    _log.info("[query-scope] %s", rs.summary())
