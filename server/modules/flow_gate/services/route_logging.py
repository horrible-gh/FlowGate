"""Structured request/outcome logging for the workflow hand-off endpoints (0449 T0004 item 6).

Why this exists
---------------
0449 NR0003 could not tell three cases apart for the reported incident — the client never
called ``/workflow/advance``; it called and was refused; it never got past ``/token/issue`` —
because neither endpoint left a trace. ``token_routes`` had no logger at all, and
``advance_workflow``'s own ``sequence_exhausted`` / ``head_in_progress`` raises are ordinary
control flow with no logging anywhere on the way out. A refusal is not an error, so it was
invisible.

Two things had to be true for a log line to be useful here:

1. **It must actually be written.** ``config.py`` hands ``logger.json`` to LogAssist, which
   attaches the timed file handler (``logs/default.log``) and the console handler to ITS OWN
   logger, not to the root. A plain ``getLogger(__name__)`` therefore propagates to a
   handler-less root and the record evaporates — the same trap already documented in
   ``documents/attachments/errors.py``. :func:`get_logger` borrows those handlers.
2. **It must not leak.** Every field is passed by keyword and the formatter accepts nothing
   else, so a raw token, a mention body, a document body or a user secret has no parameter to
   arrive through. What is recorded is the endpoint, the outcome, the HTTP status, the server's
   own refusal code, the document/group identifiers and a correlation id.

   A closed parameter list only holds if nothing writes *beside* it. 0449 TR0005 rev1 paired
   each ``failed`` line with a ``logger.exception(...)`` call for triage, and that second
   record expands ``exc_info`` into ``str(exc)`` plus every traceback frame — so a helper that
   interpolated the very token it was minting into its own error message had it written to
   ``logs/default.log`` anyway, around this list rather than through it. rev2 removes those
   calls and keeps the diagnostic through :func:`exception_signature`, which reports exception
   types and code coordinates and never a message.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

# Correlation ids are reused from the request when a proxy or the SPA already assigned one,
# so a browser network entry and a server line can be lined up without guessing by timestamp.
_CORRELATION_HEADERS = ("x-request-id", "x-correlation-id")
_CORRELATION_MAX_LEN = 64

ADVANCE_ENDPOINT = "POST /api/v1/workflow/{doc_id}/advance"
TOKEN_ISSUE_ENDPOINT = "POST /api/v1/token/issue"


def get_logger(name: str) -> logging.Logger:
    """A logger whose records reach ``logs/default.log`` instead of a handler-less root.

    ``propagate`` is deliberately left on so pytest's ``caplog`` still sees every record.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        for handler in logging.getLogger("LogAssist.log").handlers:
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def correlation_id(request: Any) -> str:
    """The caller's correlation id, or a fresh short one when the request carries none."""
    headers = getattr(request, "headers", None)
    if headers is not None:
        for name in _CORRELATION_HEADERS:
            try:
                value = (headers.get(name) or "").strip()
            except Exception:  # pragma: no cover - defensive: exotic header mappings
                value = ""
            if value:
                return value[:_CORRELATION_MAX_LEN]
    return uuid.uuid4().hex[:12]


_FAULT_MAX_LEN = 240


def exception_signature(exc: BaseException, *, max_frames: int = 3) -> str:
    """A leak-proof identifier for an unexpected exception: types and code coordinates only.

    Never the exception's message, and never a traceback record. ``logger.exception`` is the
    obvious way to keep a 500 diagnosable, but what it writes is ``str(exc)`` plus every frame's
    source line, and neither is under this module's control: an exception raised deep in a mint
    or render helper carries whatever that helper interpolated. What a triager actually needs to
    reach the code survives here — the exception class, its chained causes, and the innermost
    frames as ``file:line:function``. File names are basenames, so an absolute path (which
    carries the account name on Windows) does not travel either, and the result is whitespace-
    free so it stays one field of the space-joined line.
    """
    names: list[str] = []
    seen: set[int] = set()
    cause: Optional[BaseException] = exc
    while cause is not None and id(cause) not in seen and len(names) < 4:
        seen.add(id(cause))
        names.append(type(cause).__name__)
        cause = cause.__cause__ or cause.__context__

    stack = []
    tb = exc.__traceback__
    while tb is not None:
        stack.append(tb)
        tb = tb.tb_next
    frames = []
    for entry in reversed(stack[-max_frames:]):  # innermost first: it survives the length cap
        code = entry.tb_frame.f_code
        frames.append(f"{os.path.basename(code.co_filename)}:{entry.tb_lineno}:{code.co_name}")

    signature = "<".join(names)
    if frames:
        signature = f"{signature}@" + "<".join(frames)
    return "".join(signature.split())[:_FAULT_MAX_LEN]


def log_route_event(
    logger: logging.Logger,
    *,
    endpoint: str,
    event: str,
    status: Optional[int] = None,
    code: Optional[str] = None,
    doc_id: Optional[str] = None,
    group_id: Optional[str] = None,
    token_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    fault: Optional[str] = None,
    level: int = logging.INFO,
) -> None:
    """Write one searchable line for a request arriving at, or leaving, a hand-off endpoint.

    ``event`` is a closed vocabulary: ``received`` (the request reached the handler),
    ``issued`` / ``advanced`` (it succeeded), ``refused`` (the server declined on purpose —
    logged at INFO, because a 400/409 refusal is a decision, not a fault) and ``failed`` (an
    unexpected 500). ``token_id`` is the token's identifier, never its secret: the raw token
    value has no parameter here and must never gain one. ``fault`` is the one field a 500 adds,
    and it accepts only :func:`exception_signature`'s output — types and code coordinates, never
    an exception message.
    """
    fields = [f"endpoint={endpoint}", f"event={event}"]
    if status is not None:
        fields.append(f"status={status}")
    if code:
        fields.append(f"code={code}")
    if doc_id:
        fields.append(f"doc_id={doc_id}")
    if group_id:
        fields.append(f"group_id={group_id}")
    if token_id:
        fields.append(f"token_id={token_id}")
    if fault:
        fields.append(f"fault={fault}")
    if correlation_id:
        fields.append(f"cid={correlation_id}")
    logger.log(level, "[route] %s", " ".join(fields))
