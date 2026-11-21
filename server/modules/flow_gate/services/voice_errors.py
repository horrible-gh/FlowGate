"""Stable voice error codes shared by HTTP handlers and the installer."""
from __future__ import annotations


class VoiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def response(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}
