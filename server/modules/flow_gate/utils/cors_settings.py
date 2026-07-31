"""CORS origin defaults (flowgate.default.0371 NR0007 §2).

The deploy/setup defaults used to fall back to ALLOWED_ORIGIN=* whenever an
operator left the value unset, which combined with allow_credentials=True in
server/routers/main.py let any origin call the API with cookies/auth headers.
The installed client is always same-origin (VITE_API_BASE_URL=/flowgate), so
a wide-open default protects nothing for the common case and only weakens it
for everyone else. These helpers make the fail-closed behavior (blank ->
no cross-origin access) a single, testable unit instead of duplicated inline
logic in routers/main.py.
"""
from __future__ import annotations


def parse_allowed_origins(raw: str) -> list[str]:
    """Split a comma-separated ALLOWED_ORIGIN value into a clean origin list.

    Blank entries (including a wholly blank/whitespace value) are dropped, so
    an unset ALLOWED_ORIGIN parses to an empty list -- CORSMiddleware then
    allows no cross-origin request, which is the fail-closed default this
    module exists to enforce.
    """
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def resolve_allow_credentials(origins: list[str]) -> bool:
    """Disallow credentialed CORS when the origin list is the wildcard.

    Browsers already reject `Access-Control-Allow-Origin: *` combined with
    credentials, but nothing stopped ALLOWED_ORIGIN=* from also getting
    allow_credentials=True from FastAPI's CORSMiddleware. Forcing it off here
    keeps the *intended* behavior of a "*" origin (open, cookie-less API)
    instead of silently depending on the browser to enforce it.
    """
    return "*" not in origins
