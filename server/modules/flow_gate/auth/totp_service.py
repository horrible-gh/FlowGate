"""TOTP service: encrypts, decrypts, and verifies secrets using AES-256-GCM.

Environment variables:
- FLOWGATE_TOTP_ENCRYPT_KEY : base64-encoded 32-byte key (required)
- FLOWGATE_TOTP_ENCRYPT_KEY_PREV : previous key (optional, used as decryption fallback during key rotation)
"""
from __future__ import annotations
import base64
import os
from typing import Optional

import pyotp
from Crypto.Cipher import AES as _AES

_KEY_ENV = "FLOWGATE_TOTP_ENCRYPT_KEY"
_KEY_PREV_ENV = "FLOWGATE_TOTP_ENCRYPT_KEY_PREV"

TOTP_LOCK_MAX_ATTEMPTS = 5
TOTP_LOCK_MINUTES = 15


def _load_key(env_name: str) -> Optional[bytes]:
    val = os.environ.get(env_name)
    if not val:
        return None
    raw = base64.b64decode(val)
    if len(raw) != 32:
        raise ValueError(f"{env_name} must be 32 bytes (base64-encoded).")
    return raw


def _get_current_key() -> bytes:
    key = _load_key(_KEY_ENV)
    if key is None:
        raise RuntimeError(
            f"Environment variable {_KEY_ENV} is not set. "
            "Set a base64-encoded 32-byte key."
        )
    return key


def encrypt_totp_secret(plain_secret: str) -> str:
    """Encrypts a TOTP secret with AES-256-GCM.

    Returns: base64(12-byte nonce + ciphertext + 16-byte tag)
    """
    key = _get_current_key()
    nonce = os.urandom(12)
    cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plain_secret.encode("utf-8"))
    return base64.b64encode(nonce + ciphertext + tag).decode("ascii")


def decrypt_totp_secret(encrypted: str) -> str:
    """Decrypt AES-256-GCM. If decryption with the current key fails, retry with the previous key."""
    data = base64.b64decode(encrypted)
    # Structure: 12-byte nonce + ciphertext + 16-byte tag
    nonce = data[:12]
    tag = data[-16:]
    ciphertext = data[12:-16]

    for env_name in (_KEY_ENV, _KEY_PREV_ENV):
        key = _load_key(env_name)
        if key is None:
            continue
        try:
            cipher = _AES.new(key, _AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
        except Exception:
            continue

    raise ValueError("TOTP secret decryption failed: no valid key available.")


def generate_totp_secret() -> str:
    """Generate a new TOTP secret (base32)."""
    return pyotp.random_base32()


def get_totp_provisioning_uri(plain_secret: str, username: str) -> str:
    """Generate a provisioning URI for TOTP QR enrollment."""
    return pyotp.TOTP(plain_secret).provisioning_uri(
        name=username, issuer_name="FlowGate"
    )


def verify_totp_code(encrypted_secret: str, code: str) -> bool:
    """Verify a TOTP code (±1 window allowed).

    encrypted_secret: Encrypted secret stored in the DB.
    code: 6-digit code entered by the user.
    """
    try:
        plain = decrypt_totp_secret(encrypted_secret)
        totp = pyotp.TOTP(plain)
        return totp.verify(code, valid_window=1)
    except Exception:
        return False
