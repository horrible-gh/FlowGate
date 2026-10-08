"""Result binder entry point (D0004 §2.2 결과 연결기, L0009 §2.6.8).

From S4 the v0.1 contacts hand every document event of a Graph-owned Flow here (inbox
registration, review decision). Binding a document to a node run needs runs, which arrive with
S5 (DB0010 T15~T20); until then no node run exists to bind to, so the event is only logged. The
document itself stays an unbound document node on the Canvas (D0004 §3.2).
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

EVENT_KINDS = ("inbox_registered", "review_decided")


def on_document_event(kind: str, flow_id: str, payload: dict) -> None:
    if kind not in EVENT_KINDS:
        return
    _log.debug("graph result event (no node runs before S5) kind=%s flow=%s payload=%s", kind, flow_id, payload)
