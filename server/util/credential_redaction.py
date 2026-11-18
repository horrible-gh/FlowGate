"""The shared credential redaction rule (flowgate.v02test.0050 T0005 decision 6).

Moved here unchanged from ``flowgate/conversation/chat_errors.py`` (flowgate.v02test.0024) so the server log table
(``modules.flow_gate.services.server_logs``) masks exactly what a chat error masks. ``chat_errors.redact`` keeps its
own name and behaviour on top of this: same patterns, same ``***`` mask, same 4000-character cut there.

The rule stays identical to the client's ``redactChatError`` (client_gui/src/features/conversation/chatErrors.ts).
Applying it twice changes nothing.
"""
from __future__ import annotations

import re
from typing import Optional

SECRET_PATTERNS = (
    re.compile(r"(?i)(\bbearer\s+)[^\s\"']+"),
    re.compile(r"(?i)\b((?:FLOWGATE_TOKEN|[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|SECRET_KEY|PASSWORD))\s*[:=]\s*)[^\s\"']+"),
    re.compile(r"(?i)(\b(?:authorization|token|access_token|api[_-]?key|password|secret)\b\s*[=:]\s*)(?!bearer\b)[^\s,;&\"']+"),
)
MASK = "***"


def redact_text(text: Optional[str], max_chars: Optional[int] = None) -> str:
    """``text`` without anything that looks like a credential, cut to ``max_chars`` when given."""
    value = str(text or "")
    for pattern in SECRET_PATTERNS:
        value = pattern.sub(lambda m: m.group(1) + MASK, value)
    return value if max_chars is None else value[:max_chars]


# A mapping key whose VALUE is a credential (structured data such as ``server_logs.record(extra=...)``). ``redact_text``
# only sees ``name=value`` inside one string; a dict keeps the name and the value apart, so the key decides.
# ``-`` / ``.`` / spaces and camelCase are folded to ``_`` first (``X-Api-Key``, ``apiKey``, ``refreshToken``).
SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:authorization|proxy_authorization|cookie|set_cookie|password|passwd|pwd|passphrase|secret|"
    r"client_secret|secret_key|private_key|token|access_token|refresh_token|id_token|auth_token|api_key|apikey|"
    r"credential|credentials|flowgate_token)$"
)


def is_secret_key(name: object) -> bool:
    """Whether a mapping key names a credential, so its value must be masked whole."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name or "")).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return bool(text) and SECRET_KEY_PATTERN.search(text) is not None
