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


class FlowNotFound(GraphApiError):
    def __init__(self, flow_id: str):
        super().__init__(404, "flow_not_found", {"flow_id": flow_id})


class FlowLockTimeout(Exception):
    """The in-process Flow lock was not acquired in time (L0009 E-02) -> 500 internal_error."""
