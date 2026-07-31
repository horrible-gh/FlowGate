"""AI provider API-key encryption at rest (flowgate.default.0371 — NR0007 §3).

`ai_providers.api_key` used to hold the operator's provider secret verbatim, so a
DB dump, a replica or a nightly backup carried a directly usable Anthropic/OpenAI
key. This module is the single boundary that keeps that column ciphertext.

Storage format::

    enc:v1:<base64(12-byte nonce + ciphertext + 16-byte GCM tag)>

The explicit version prefix — rather than "does this look like base64?" — is what
tells a stored value apart from a legacy plaintext key: real API keys are often
base64-shaped themselves, and guessing would corrupt one (NR0007 §3 권고 2).

Key material:

    FLOWGATE_AI_ENCRYPT_KEY        base64-encoded 32 bytes (current)
    FLOWGATE_AI_ENCRYPT_KEY_PREV   optional; tried on decrypt during rotation

A dedicated key rather than the git/TOTP one (NR0007 §3 권고 1): the three secret
stores stay independently rotatable, and one leaked key does not open the others.
When neither the environment nor .env carries a key, one is generated once into
``<storage root>/.flowgate-ai-key`` (chmod 600) — the same boot-time provisioning
git_service uses, so an install that predates this change keeps working without
any operator action. Silently storing plaintext when no key is available is never
an option (NR0007 §3 권고 5), and a value that carries the enc:v1: prefix but does
not decrypt raises instead of being read back as plaintext — hiding a lost master
key behind a "plausible" secret is how a chain fails much later, much less
legibly (NR0007 §3 권고 4).
"""
from __future__ import annotations

import base64
import os
import stat
from pathlib import Path
from typing import Optional

from Crypto.Cipher import AES as _AES

KEY_ENV = "FLOWGATE_AI_ENCRYPT_KEY"
KEY_PREV_ENV = "FLOWGATE_AI_ENCRYPT_KEY_PREV"
KEY_FILE_NAME = ".flowgate-ai-key"

ENC_PREFIX = "enc:v1:"
_NONCE_LEN = 12
_TAG_LEN = 16
_KEY_LEN = 32


class ApiKeyCryptoError(RuntimeError):
    """Encryption/decryption could not be performed — never a reason to fall back
    to plaintext."""


def _key_file_path() -> Path:
    from modules.flow_gate.storage.paths import get_storage_root  # lazy — import cycle

    return get_storage_root(create=True) / KEY_FILE_NAME


def _load_key_material(env_name: str) -> Optional[bytes]:
    """env var → pydantic settings (.env), mirroring git_service's resolution."""
    val = os.environ.get(env_name)
    if not val:
        try:
            from config import settings as _settings  # lazy — import cycle safety

            val = getattr(_settings, env_name, None)
        except Exception:
            val = None
    if not val:
        return None
    try:
        raw = base64.b64decode(val)
    except Exception as exc:
        raise ApiKeyCryptoError(f"{env_name} is not valid base64.") from exc
    if len(raw) != _KEY_LEN:
        raise ApiKeyCryptoError(f"{env_name} must be a base64-encoded 32-byte key.")
    return raw


def _get_current_key(create: bool = False) -> bytes:
    """Resolve the master key: env/.env → persisted storage key file (→ generate)."""
    key = _load_key_material(KEY_ENV)
    if key is not None:
        return key
    try:
        kf = _key_file_path()
        if kf.is_file():
            raw = base64.b64decode(kf.read_text(encoding="ascii").strip())
            if len(raw) == _KEY_LEN:
                return raw
        if create:
            raw = os.urandom(_KEY_LEN)
            kf.write_text(base64.b64encode(raw).decode("ascii"), encoding="ascii")
            try:
                os.chmod(kf, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            return raw
    except ApiKeyCryptoError:
        raise
    except Exception:
        pass
    raise ApiKeyCryptoError(
        f"{KEY_ENV} is not configured and no persisted key is available."
    )


def is_encrypted(value: Optional[str]) -> bool:
    """True for a value this module produced. Legacy plaintext rows read False."""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_api_key(plain: Optional[str]) -> Optional[str]:
    """Plaintext → `enc:v1:...`. None/"" pass through (absent key), and a value that
    is already encrypted is returned untouched so re-saving never double-wraps."""
    if plain is None or plain == "":
        return plain
    if is_encrypted(plain):
        return plain
    key = _get_current_key(create=True)
    nonce = os.urandom(_NONCE_LEN)
    cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plain.encode("utf-8"))
    return ENC_PREFIX + base64.b64encode(nonce + ciphertext + tag).decode("ascii")


def decrypt_api_key(stored: Optional[str]) -> Optional[str]:
    """`enc:v1:...` → plaintext; a value without the prefix is a legacy plaintext
    row and is returned as it is (that is what makes the backfill resumable).

    Raises ApiKeyCryptoError when a prefixed value cannot be decrypted with the
    current or the previous key.
    """
    if stored is None or stored == "":
        return stored
    if not is_encrypted(stored):
        return stored
    try:
        data = base64.b64decode(stored[len(ENC_PREFIX):])
    except Exception as exc:
        raise ApiKeyCryptoError("Stored API key is not readable ciphertext.") from exc
    if len(data) < _NONCE_LEN + _TAG_LEN:
        raise ApiKeyCryptoError("Stored API key is truncated ciphertext.")
    nonce, tag, ciphertext = data[:_NONCE_LEN], data[-_TAG_LEN:], data[_NONCE_LEN:-_TAG_LEN]

    candidates: list[bytes] = []
    try:
        candidates.append(_get_current_key())
    except ApiKeyCryptoError:
        pass
    prev = _load_key_material(KEY_PREV_ENV)
    if prev is not None:
        candidates.append(prev)

    for key in candidates:
        try:
            cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
        except Exception:
            continue
    raise ApiKeyCryptoError(
        f"Stored AI provider API key cannot be decrypted ({KEY_ENV} changed or lost). "
        "Set the previous key in "
        f"{KEY_PREV_ENV}, or re-enter the key in the AI provider settings."
    )
