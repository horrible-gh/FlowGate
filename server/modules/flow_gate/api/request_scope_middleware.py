"""Per-request DB scope: read memoization + query accounting (0291 NR0003 P3-1 / 4-8).

``db/request_cache.py`` 가 정책을, 이 파일이 경계를 정한다. 경계는 HTTP 요청 하나다.

**순수 ASGI 미들웨어인 이유.** Starlette 의 ``BaseHTTPMiddleware`` (``@app.middleware("http")``
포함)는 다운스트림 앱을 별도 anyio 태스크에서 돌린다. contextvar 는 태스크가 생길 때
복사되므로 전파 여부가 스폰 시점에 달려 있고, 그건 Starlette 버전에 따라 바뀔 수 있는
세부다. 순수 ASGI 미들웨어는 **같은 태스크 안에서** ``await self.app(...)`` 하므로 스코프가
보이는 것이 구조적으로 보장된다. 캐시가 조용히 꺼지면 아무 증상 없이 효과만 사라지는
종류의 실패라, 여기서는 보장 쪽을 택했다.

sync ``def`` 엔드포인트로도 전파된다 — FastAPI 가 AnyIO 스레드풀로 넘길 때 현재 컨텍스트를
복사하고, 스코프에 담긴 것은 **가변 객체**라 스레드 안에서의 변경이 그대로 보인다.
NR0003 발견 3 이 짚은 대로 이 코드베이스의 엔드포인트는 대부분 sync ``def`` 이므로 이
성질이 없으면 캐시가 도는 곳이 없다.

**SSE 는 제외한다.** ``/api/v1/events`` 스트림은 수명이 요청이 아니라 세션이다. 스코프를
열면 캐시가 접속이 끊길 때까지 살아 있게 되어, 이 모듈이 없애려던 stale 위험을 정확히
되살린다. 스트리밍 응답 하나가 요청 경계라는 전제를 깨는 유일한 자리다.
"""
from __future__ import annotations

import logging
import os

from ..db import request_cache

_log = logging.getLogger(__name__)

# 스코프를 열지 않는 경로. 부분 문자열 검사다 — CONTEXT prefix 가 붙어도 걸린다.
_EXCLUDED_PATH_MARKERS = ("/api/v1/events",)


def _query_log_enabled() -> bool:
    """요청당 쿼리 수 로깅 (NR0003 4-8).

    기본 OFF 다. 이 줄은 요청마다 하나씩 쌓이므로 상시 켜 두면 줄이려던 SQL 로그만큼
    시끄러워진다. 계측이 필요한 구간에만 ``FLOWGATE_QUERY_LOG=1`` 로 켠다.
    """
    return os.environ.get("FLOWGATE_QUERY_LOG", "").strip() not in ("", "0", "false", "False")


class RequestScopeMiddleware:
    """요청 하나 = 캐시 하나."""

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
                # 예외로 끝난 요청도 센다 — 500 을 내는 경로가 쿼리를 가장 많이 쓰는
                # 경우가 있고, 그때 수치가 사라지면 그 사실을 알 수 없다.
                if _query_log_enabled() and rs.reads:
                    _log.info("[query-scope] %s", rs.summary())
