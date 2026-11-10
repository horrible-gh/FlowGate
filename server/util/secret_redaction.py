"""Credential redaction shared by Git execution and gate evidence (no truncation)."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote, quote_plus

MASK = "***"

# scheme://user:pass@host or scheme://token@host -> scheme://***@host
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@'\"]+@")
# Authorization / Proxy-Authorization / X-*-Token header values, to the end of the line
_AUTH_HEADER = re.compile(r"(?im)\b((?:proxy-)?authorization|x-[a-z0-9-]*token|private-token|cookie)\s*[:=]\s*[^\r\n]+")
# Bearer / Basic / token schemes
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic|token)\s+[a-z0-9._~+/=-]{6,}")
# key=value / key: value secrets (query strings, config dumps)
_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|access_token|refresh_token|api[_-]?key|private_token|client_secret)"
    r"(\s*[=:]\s*)[^\s&'\";,]+")
# well-known token shapes (GitHub, GitLab, Slack, generic long hex/base64 after a known prefix)
_KNOWN_TOKENS = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{16,}"
                           r"|xox[abprs]-[A-Za-z0-9-]{10,})\b")


def scrub_credentials(text: Optional[str], *secrets: Optional[str]) -> str:
    """Mask whole output before callers select lines or impose their own length limit."""
    out = str(text or "")
    # Mask structured credentials intact, before a configured substring can
    # split a token into unrecognizable fragments.
    out = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{MASK}@", out)
    out = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}: {MASK}", out)
    out = _AUTH_SCHEME.sub(lambda m: f"{m.group(1)} {MASK}", out)
    out = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    out = _KNOWN_TOKENS.sub(MASK, out)
    values = {variant for secret in secrets if secret
              for variant in (secret, quote(secret, safe=""), quote_plus(secret, safe=""))}
    for value in sorted(values, key=len, reverse=True):
        out = out.replace(value, MASK)
        if "%" in value:
            # Percent hex digits are case insensitive on the wire.
            out = re.sub(re.escape(value), MASK, out, flags=re.IGNORECASE)
    return out
