"""Request id for every HTTP request + token redaction in the access log (flowgate.v02gui.0017).

NR 0003 B-R8 found that a failed ``/auth/refresh`` left no application log line and no request
identifier, and that the default uvicorn access log prints the query string verbatim -- which for
the SSE stream (``/api/v1/events/stream?token=<access JWT>``) means the access token itself.

* ``RequestIdMiddleware`` gives each HTTP request an id: a well-formed incoming ``X-Request-ID``
  is kept (so a proxy id can be followed end to end), anything else is replaced by a fresh one.
  The id is answered in the ``X-Request-ID`` response header and is readable from application
  code through ``current_request_id()`` (a contextvar; pure ASGI so it stays in the same task,
  see request_scope_middleware.py for why). An exception that escapes the request is tagged with the id
  (``request_id_of_exception``) so the server's later "Exception in ASGI application" record can be tied to it.
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


# The id of the request an exception escaped from. The contextvar is reset when the request ends, but the server
# (uvicorn's "Exception in ASGI application") logs the escaped exception only after that; the tag lets the
# server_logs row of that very record point at its request (flowgate.v02test.0050 T0005 criterion (c)).
_EXC_REQUEST_ID_ATTR = "__flowgate_request_id__"


def _tag_exception(exc: BaseException, rid: str) -> None:
    if getattr(exc, _EXC_REQUEST_ID_ATTR, None):  # the innermost request keeps its id
        return
    try:
        setattr(exc, _EXC_REQUEST_ID_ATTR, rid)
    except Exception:  # noqa: BLE001 -- an exception type that refuses attributes just stays untagged
        pass


def request_id_of_exception(exc: BaseException | None) -> str | None:
    """The request id tagged on ``exc`` (or on an exception it wraps, chained or grouped), else None."""
    pending = [exc]
    seen: set[int] = set()
    while pending and len(seen) < 32:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        rid = getattr(current, _EXC_REQUEST_ID_ATTR, None)
        if isinstance(rid, str) and rid:
            return rid
        pending.append(current.__cause__)
        pending.append(current.__context__)
        pending.extend(getattr(current, "exceptions", None) or ())
    return None


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


async def unhandled_error_response(request, exc):
    """ServerErrorMiddleware sits outside user middleware; echo the tagged id here.

    Starlette still re-raises the exception after sending this response, so uvicorn
    records the original traceback exactly once. Do not swallow or log it here.
    """
    from starlette.responses import PlainTextResponse

    rid = request_id_of_exception(exc) or request.scope.get("flowgate.request_id")
    headers = {REQUEST_ID_HEADER: rid} if rid else {}
    return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)


class RequestIdMiddleware:
    """Assign a request id, expose it to the handler and echo it in the response header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _pick_request_id(scope)
        scope["flowgate.request_id"] = rid
        token = _request_id.set(rid)
        header = (REQUEST_ID_HEADER.encode("latin-1"), rid.encode("latin-1"))

        async def send_with_id(message):
            if message.get("type") == "http.response.start":
                headers = [h for h in (message.get("headers") or []) if h[0].lower() != header[0]]
                headers.append(header)
                message = {**message, "headers": headers}
            await send(message)

        try:
            # Reject decoded URL/header text before policy middleware can query the DB.
            from starlette.datastructures import Headers, QueryParams
            from starlette.responses import JSONResponse
            from modules.flow_gate.api.input_validation import has_nul

            if has_nul([scope.get("path", ""), list(QueryParams(scope.get("query_string", b"")).multi_items()),
                        list(Headers(scope=scope).items())]):
                await JSONResponse({"detail": "NUL characters are not allowed in request text"},
                                   status_code=422)(scope, receive, send_with_id)
                return
            await self.app(scope, receive, send_with_id)
        except BaseException as exc:
            _tag_exception(exc, rid)
            raise
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
