"""P#2 error compatibility at both inbox and API worker boundaries."""
import json
import logging
from fastapi.responses import JSONResponse
from modules.flow_gate.db.connection import now_iso
from .errors import GraphApiError, internal_error

_log = logging.getLogger(__name__)
HELP = "/flowgate/api/v1/help"


def failure(exc):
    return normalize(exc.status, {"error": {"code": exc.code, "message": exc.message or exc.code,
                                               "params": exc.params}})[1]


def normalize(status, value, *, source=None):
    if isinstance(value, JSONResponse):
        status, value = value.status_code, json.loads(value.body)
    if isinstance(value, dict) and status >= 400 and "error" not in value and value.get("code") and value.get("message"):
        # Existing TR2 failures carry code/message/details at the top level.
        value = {**value, "error_message": value["message"], "error": {
            "code": value["code"], "message": value["message"], "params": value.get("details") or {}}}
    if not isinstance(value, dict) or not status or not isinstance(value.get("error", {}), dict) or (status >= 400 and not value.get("error_message") and not value.get("error", {}).get("code")):
        exc = internal_error()
        _log.error("agent inbox transport failed trace=%s status=%s", exc.params["trace_id"], status)
        status, value = exc.status, {"error": {"code": exc.code, "message": exc.message or exc.code, "params": exc.params}}
    if status < 400:
        return status, value
    value = dict(value)
    error = dict(value.get("error") or {})
    message = value.get("error_message") or error.get("message") or error.get("code") or "internal_error"
    code = error.get("code")
    if status == 401:
        code = "unauthorized"
    elif status == 403 and code in {None, "forbidden"}:
        code = "forbidden"
    elif status == 422 and not code and source == "action_scope":
        code = "invalid_request"
        error["params"] = {"errors": [{"field": "action", "reason": "invalid_format",
                             "validation": {"reason": "action_scope_not_registerable"}}]}
    elif not code:
        # P#2 §8: certain legacy failures keep their status/error_message contract.
        return status, value
    error.update(code=code, message=message, params=error.get("params", {}))
    if code in {"unauthorized", "forbidden", "graph_attempt_inactive"}:
        error["params"] = {}
    value.update(ok=False, server_ts=value.get("server_ts") or now_iso(), error=error,
                 http_status=status, error_message=message, help_url=HELP)
    return status, value


def response(exc):
    value = failure(exc)
    return JSONResponse(status_code=exc.status, content=value)
