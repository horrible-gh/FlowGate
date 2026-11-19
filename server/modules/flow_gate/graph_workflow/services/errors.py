"""Graph API errors raised by services and turned into the P0008 §1.3 envelope by the routes."""
from __future__ import annotations

from typing import Optional


class GraphApiError(Exception):
    def __init__(self, status: int, code: str, params: Optional[dict] = None, message: Optional[str] = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.params = params or {}
        self.message = message
        self.message_key: Optional[str] = None     # another message of the envelope table for this code


class FlowNotFound(GraphApiError):
    def __init__(self, flow_id: str):
        super().__init__(404, "flow_not_found", {"flow_id": flow_id})


class FlowLockTimeout(Exception):
    """The in-process Flow lock was not acquired in time (L0009 E-02) -> 500 internal_error.

    ``str(exc)`` / ``args[0]`` stay the key waited for (a Flow id, or a document id for a document lock).
    flowgate.v02test.0040 T0005: ``kind`` ("flow" / "document") and ``wait_sec`` (how long it waited) travel with it,
    so a caller that turns it into a refusal (module rename -> 409 ``module_rename_busy``) can say what was busy.
    """

    def __init__(self, key: str, *, kind: str = "flow", wait_sec: Optional[float] = None):
        super().__init__(key)
        self.key = key
        self.kind = kind
        self.wait_sec = wait_sec
