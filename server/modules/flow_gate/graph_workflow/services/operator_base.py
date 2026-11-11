"""The operator-facing API base of an AI start that has no HTTP request (flowgate.v02test.0017 CH0002).

A chat or AI invoke run takes its base from the request (``token_routes._build_api_base``: the origin the
person's browser reached). A Graph AI start (``ai_node_starter``) and the card follow-ups that reuse its base
(``card_settings.legacy_adapter``: Q answers, review loops) are post-commit or recovery jobs with no request,
so they used this server's loopback. The local CLI rewrites any base to loopback itself
(``provider_api._resolve_agent_api_base``), but a remote CLI hands the base to an Agent on another PC and
refuses loopback before any Job (``provider_remote.api_base_failure`` -> ``remote_api_base_unreachable:
api_base_loopback``). So a node whose AI was a remote Agent never started, while chat on the same Agent did.

``resolve(user_id)`` answers, in order:

1. ``settings.FLOWGATE_OPERATOR_API_BASE`` -- an explicit origin (``scheme://host[:port]``) an operator set for
   request-less starts; ``CONTEXT`` + ``/api/v1`` are appended. Invalid: ignored with a warning.
2. The base of the run user's own latest Graph API request that did not come over loopback (``remember``,
   called by ``graph_workflow.api.envelope.authenticate``) -- the same origin chat would use for that person.
   Keyed by the user: a remote Agent runs only for its owner (``provider_remote`` ``requester_not_owner``), so
   only that person's own requests decide where their own Agent connects, as for chat.
3. This server's loopback, as before. A local CLI behaves as it did; a remote CLI still refuses it clearly.

The memory is per process and not persisted: a recovery job right after a restart, before the person has made
a request, falls back to 1 or 3.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
from typing import Optional
from urllib.parse import urlsplit

_log = logging.getLogger(__name__)

_lock = threading.Lock()
_by_user: dict[str, str] = {}


def _settings():
    from config import settings                                    # noqa: PLC0415
    return settings


def _suffix() -> str:
    return f"{_settings().CONTEXT.rstrip('/')}/api/v1"


def is_loopback(base: Optional[str]) -> bool:
    """True when ``base`` names no host, ``localhost`` or a loopback IP (``provider_remote.api_base_failure``)."""
    try:
        host = urlsplit(base or "").hostname
    except ValueError:
        return True
    if not host:
        return True
    host = host.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def loopback_base() -> str:
    return f"http://127.0.0.1:{_settings().FLOWGATE_PORT}{_suffix()}"


def configured_base() -> Optional[str]:
    """``FLOWGATE_OPERATOR_API_BASE`` as an API base, or None when unset or unusable."""
    raw = getattr(_settings(), "FLOWGATE_OPERATOR_API_BASE", None)
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip().rstrip("/")
    try:
        parts = urlsplit(value)
        parts.port                                                  # noqa: B018 - raises on a bad port
    except ValueError:
        parts = None
    if (parts is None or parts.scheme not in ("http", "https") or not parts.hostname or parts.username
            or parts.password or parts.path or parts.query or parts.fragment):
        _log.warning("FLOWGATE_OPERATOR_API_BASE must be an http(s) origin (scheme://host[:port]); ignored")
        return None
    return f"{value}{_suffix()}"


def base_of_request(request) -> Optional[str]:
    """The operator base of ``request`` exactly as ``token_routes._build_api_base`` builds it."""
    try:
        base = str(request.base_url).rstrip("/")
    except Exception:                                               # noqa: BLE001 - no usable request
        return None
    return f"{base}{_suffix()}" if base else None


def remember(user_id: Optional[str], request) -> None:
    """Keep ``user_id``'s latest non-loopback operator base. Never raises."""
    if not user_id:
        return
    try:
        base = base_of_request(request)
        if base is None or is_loopback(base):
            return
        with _lock:
            _by_user[str(user_id)] = base
    except Exception:                                               # noqa: BLE001 - a request is never refused for this
        _log.debug("operator base not remembered", exc_info=True)


def remembered(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    with _lock:
        return _by_user.get(str(user_id))


def resolve(user_id: Optional[str] = None) -> str:
    """The API base a request-less AI start of ``user_id`` hands to its run (see the module docstring)."""
    return configured_base() or remembered(user_id) or loopback_base()


def _reset_for_tests() -> None:
    with _lock:
        _by_user.clear()
