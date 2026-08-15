"""The P0011 §1-4 error envelope as one exception type.

Every attachment path fails through ``AttachmentError``. The route layer turns it into the
exact JSON P0011 promised::

    {"error": {"code": "...", "message": "...", "details": {...}}}

FastAPI's ``HTTPException`` cannot produce that shape — it always wraps the body in
``{"detail": ...}`` — so the routes return ``JSONResponse`` directly, the same thing
``tree_routes._err`` does for the source-download contract.

``details`` never carries an absolute path or an internal exception string (P0011 §1-4).
That is also why an unexpected crash goes through ``unexpected`` below: the caller gets a
fixed ``reason`` word, and the traceback goes to the server log instead of the wire.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi.responses import JSONResponse

logger = logging.getLogger("flow_gate.attachments")


def _sink() -> logging.Logger:
    """This package's logger, wired to wherever the app already writes.

    ``config.py`` hands ``logger.json`` to LogAssist, which attaches the timed file handler
    (``logs/default.log``) and the console handler to ITS OWN logger — not to the root. A
    plain ``getLogger(__name__)`` therefore propagates to a root with no handlers and the
    line is lost, which is precisely why the rejected 500 left no trace on the server. Borrow
    those handlers once so an attachment failure lands in the same file as everything else;
    ``propagate`` stays on so pytest's ``caplog`` still sees the record.
    """
    if not logger.handlers:
        for handler in logging.getLogger("LogAssist.log").handlers:
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


class AttachmentError(Exception):
    """One failed attachment operation, already shaped as the P0011 envelope."""

    def __init__(self, status_code: int, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        # Drop None values so an omitted detail never shows up as an explicit null.
        # `group_id: null` in a copy response IS meaningful, so callers that need a
        # literal null pass the string sentinel handling themselves.
        self.details = {k: v for k, v in details.items() if v is not None}

    def body(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status_code, content=self.body())


def error_response(exc: AttachmentError) -> JSONResponse:
    return exc.response()


# ── unexpected failures ─────────────────────────────────────────────────────────
#
# 0060 TR0017 rev2. The rework reason was a bare `500 (Internal Server Error)` seen while
# uploading, and the forensics dead-ended for one reason: this package logged nothing, so
# the server log held no trace of the request at all. Every 5xx now goes through here.
#
# `reason` is a closed vocabulary — 'storage' (filesystem), 'registry' (metadata store),
# 'unexpected' (anything else). It is a category, not an exception string, so P0011 §1-4
# still holds while the user-visible error stops being indistinguishable from a crash.

UNEXPECTED_REASONS = ("storage", "registry", "unexpected")


def unexpected(
    exc: Optional[BaseException],
    *,
    operation: str,
    code: str = "ATTACHMENT_OPERATION_FAILED",
    message: str = "The attachment operation could not be completed.",
    reason: Optional[str] = "unexpected",
    **details: Any,
) -> AttachmentError:
    """Log *exc* with its traceback and return the 500 envelope to hand back.

    The exception itself never reaches the client; the log line carries the operation, the
    document and the full traceback so the next report of "500 while uploading" can be read
    off ``server/logs/default.log`` instead of being re-derived from disk forensics.

    ``reason=None`` leaves the envelope exactly as the caller shaped it — delete and copy
    already answer with their own codes and details and come through here only for the log.
    ``exc=None`` is for the one 5xx that is a decision rather than a crash (copy's
    source-changed check), where there is no traceback to carry.
    """
    _sink().error(
        "[attachments] %s failed: %s (%s) details=%s",
        operation,
        type(exc).__name__ if exc is not None else "no-exception",
        code,
        details,
        exc_info=exc,
    )
    if reason is not None:
        details["reason"] = reason
    err = AttachmentError(500, code, message, **details)
    if exc is not None:
        err.__cause__ = exc
    return err
