"""Request id for every HTTP request + token redaction in the access log (flowgate.v02gui.0017).

NR 0003 B-R8 found that a failed ``/auth/refresh`` left no application log line and no request
identifier, and that the default uvicorn access log prints the query string verbatim -- which for
the SSE stream (``/api/v1/events/stream?token=<access JWT>``) means the access token itself.

* ``RequestIdMiddleware`` gives each HTTP request an id: a well-formed incoming ``X-Request-ID``
  is kept (so a proxy id can be followed end to end), anything else is replaced by a fresh one.
  The id is answered in the ``X-Request-ID`` response header and is readable from application
  code through ``current_request_id()`` (a contextvar; pure ASGI so it stays in the same task,
  see request_scope_middleware.py for why).
* ``install_access_log_redaction()`` attaches a filter to the ``uvicorn.access`` logger that
  replaces the VALUE of ``token`` / ``access_token`` / ``refresh_token`` query parameters with
  ``[redacted]`` before the line is formatted. It is installed at app import, after uvicorn has
  configured logging, so it covers ``stg.py`` and the plain ``uvicorn`` CLI alike.
"""
from __future__ import annotations

import contextvars
import logging
import re
import uuid

REQUEST_ID_HEADER = "x-request-id"
_INCOMING_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("flowgate_request_id", default=None)


def current_request_id() -> str | None:
    """The id of the HTTP request being handled, or None outside a request."""
    return _request_id.get()


def _pick_request_id(scope) -> str:
    for name, value in scope.get("headers") or ():
        if name.lower() == REQUEST_ID_HEADER.encode("latin-1"):
            try:
                candidate = value.decode("latin-1").strip()
            except Exception:
                break
            if _INCOMING_ID.match(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class RequestIdMiddleware:
    """Assign a request id, expose it to the handler and echo it in the response header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _pick_request_id(scope)
        token = _request_id.set(rid)
        header = (REQUEST_ID_HEADER.encode("latin-1"), rid.encode("latin-1"))

        async def send_with_id(message):
            if message.get("type") == "http.response.start":
                headers = [h for h in (message.get("headers") or []) if h[0].lower() != header[0]]
                headers.append(header)
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)


# ``token=`` (SSE), ``access_token=`` / ``refresh_token=`` (defensive): keep the name, drop the value.
_SECRET_QUERY = re.compile(r"(?i)([?&](?:access_token|refresh_token|token)=)[^&#\s\"]*")
REDACTED = "[redacted]"


def redact_secrets(text: str) -> str:
    return _SECRET_QUERY.sub(lambda m: m.group(1) + REDACTED, text)


class AccessLogTokenRedactionFilter(logging.Filter):
    """Rewrite secret query values in a log record (args and message) before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and args:
            record.args = tuple(redact_secrets(a) if isinstance(a, str) else a for a in args)
        elif isinstance(args, dict) and args:
            record.args = {k: redact_secrets(v) if isinstance(v, str) else v for k, v in args.items()}
        if isinstance(record.msg, str) and "=" in record.msg:
            record.msg = redact_secrets(record.msg)
        return True


def install_access_log_redaction(logger_name: str = "uvicorn.access") -> AccessLogTokenRedactionFilter:
    """Attach the redaction filter once (idempotent) and return it."""
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        # By name, not isinstance: a test harness that re-imports this module must not stack a
        # second filter of a "different" class object on the process-wide logger.
        if type(existing).__name__ == AccessLogTokenRedactionFilter.__name__:
            return existing
    flt = AccessLogTokenRedactionFilter()
    logger.addFilter(flt)
    return flt
