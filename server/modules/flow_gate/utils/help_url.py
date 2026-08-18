"""Single source of truth for the outbound API help URL (group 0238 T0004).

Every outbound error envelope used to hard-code
``https://example.com/api/v1/help`` as its ``help_url`` hint — a placeholder
domain that does not exist, so the one field meant to guide a stuck worker sent
it nowhere (0238 NR0003, prevention proposal #2).

The outbound routers are mounted under ``{CONTEXT}/api/v1`` (server/routers/main.py),
so derive the URL from the configured CONTEXT rather than hard-coding it.

The result is host-relative on purpose: T269 fixed worker URL exposure by keeping
advertised paths free of a scheme/host the server cannot know behind a proxy. The
worker already holds the base it called and resolves the rest itself.
"""
from __future__ import annotations


def _context() -> str:
    """Return the configured context path (e.g. "/flowgate"), or "" if unavailable."""
    try:
        from config import settings
        return (settings.CONTEXT or "").strip().rstrip("/")
    except Exception:
        # Config is unimportable in bare unit tests; a context-less path still
        # beats pointing the worker at example.com.
        return ""


def outbound_api_base() -> str:
    """Return the base path of the worker-facing outbound API."""
    return f"{_context()}/api/v1"


def help_url() -> str:
    """Return the URL of GET /api/v1/help, used as the `help_url` error hint."""
    return f"{outbound_api_base()}/help"
