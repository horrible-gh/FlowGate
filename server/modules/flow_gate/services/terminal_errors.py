"""Installer errors adapt to the terminal API error contract."""
from .terminal_sessions import TerminalError


class BundleError(TerminalError):
    def __init__(self, code, message, status=500):
        super().__init__(status, code, message)
