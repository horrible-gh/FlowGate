"""Reject NUL in API text before route dependencies and handlers use it."""
from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.requests import HTTPConnection


def has_nul(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return False


async def reject_nul_input(request: HTTPConnection):
    # FastAPI applies app-wide dependencies to WebSocket routes too (the terminal). A
    # `Request` annotation is left unfilled there and the handshake dies with a TypeError,
    # so take the common base: a WebSocket has no body, only path/query/headers.
    values = [request.path_params, list(request.query_params.multi_items()),
              list(request.headers.items())]
    if not isinstance(request, Request):
        if has_nul(values):
            raise HTTPException(422, "NUL characters are not allowed in request text")
        return
    # FastAPI caches parsed JSON/forms on this Request. Binary uploads stay binary.
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not content_type or content_type == "application/json" or content_type.endswith("+json"):
        try:
            values.append(await request.json())
        except (ValueError, UnicodeError):
            pass  # Keep the route's existing malformed-body handling.
    elif content_type in ("application/x-www-form-urlencoded", "multipart/form-data"):
        form = await request.form()
        for key, value in form.multi_items():
            values.extend([key, value.filename if isinstance(value, UploadFile) else value])
    if has_nul(values):
        # Never echo a credential-bearing input in the error or the log.
        raise HTTPException(422, "NUL characters are not allowed in request text")

