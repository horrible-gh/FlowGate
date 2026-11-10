"""Remote credentials never leave a gate decision's Git work (flowgate.v02.0085 T#3, D0007 §7.3 last paragraph).

A remote URL may carry ``user:password@``, a token may sit in a query string or an ``Authorization`` header, and Git's
standard error repeats all of them. Before anything of a gate decision's Git work is stored (decision, effects,
recovery history, job error / evidence), logged, put in an event, an API answer or an SSE payload, it goes through
``scrub`` (text) or ``scrub_value`` (a JSON-like value). The configured secret of the project is removed by value too.

What a gate stores of a failure is a safe error code (``safe_code``), never Git's own text: remote identifiers (the
name ``origin``), ref names and SHAs only. ``scrub`` is the second line, for the few places a text is kept or logged.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from util.secret_redaction import MASK, scrub_credentials
_CODE = re.compile(r"[^a-z0-9_.:-]+")
CODE_MAX = 64
TEXT_MAX = 300

def scrub(text: Optional[str], *secrets: Optional[str]) -> str:
    """``text`` without any remote credential: URL user info, auth headers and schemes, key=value secrets, known token
    shapes and every given secret by value. Bounded to ``TEXT_MAX`` characters."""
    return scrub_credentials(text, *secrets)[:TEXT_MAX]


def scrub_value(value: Any, *secrets: Optional[str]) -> Any:
    """A JSON-like value with every string scrubbed (keys kept: they are the code's own names)."""
    if isinstance(value, str):
        return scrub(value, *secrets)
    if isinstance(value, dict):
        return {k: scrub_value(v, *secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(v, *secrets) for v in value]
    return value


def safe_code(code: Optional[str], default: str = "git_error") -> str:
    """An error code fit to be stored and shown: lower case ``[a-z0-9_.:-]``, at most 64 characters."""
    cleaned = _CODE.sub("_", str(code or "").strip().lower()).strip("_")
    return (cleaned or default)[:CODE_MAX]


def secrets_of(cfg: Optional[dict]) -> tuple[str, ...]:
    """The project's own credential values (the stored token / password, the user name of a URL) to remove by value.
    A secret that cannot be read is none to remove (``scrub``'s patterns still apply)."""
    if not cfg:
        return ()
    found: list[str] = []
    try:
        from modules.flow_gate.services.git.credentials import _load_secret_for
        secret = _load_secret_for(cfg)
        if secret:
            found.append(secret)
    except Exception:  # noqa: BLE001 — an unreadable secret is simply not one we can match by value
        pass
    for match in re.finditer(r"://([^/\s@]+)@", str(cfg.get("repo_url") or "")):
        found.extend(part for part in match.group(1).split(":") if len(part) >= 4)
    return tuple(found)


def contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(n and n in text for n in needles)
