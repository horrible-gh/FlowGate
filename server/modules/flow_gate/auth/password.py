"""Password hashing, verification, and policy (bcrypt-based).

Also supports verification of legacy pbkdf2_sha256 hashes (automatically falls back with deprecated scheme).
"""
from __future__ import annotations
import re
from passlib.context import CryptContext

# bcrypt primary + pbkdf2_sha256 deprecated (backward compatibility with existing passwords)
_ctx = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated=["pbkdf2_sha256"])


def hash_password(plain: str) -> str:
    """Hash a password using bcrypt."""
    return _ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Compare a plain password to a hash. Also supports pbkdf2_sha256 hashes."""
    try:
        return _ctx.verify(plain, hashed)
    except Exception:
        return False


def _min_length() -> int:
    try:
        from modules.flow_gate.settings.security_policy_service import password_min_length
        return password_min_length()
    except Exception:  # noqa: BLE001 — no readable policy keeps the original 12
        return 12


def validate_password(plain: str) -> list[str]:
    """Validate password against policy. Returns: list of violations (empty list = pass).

    Policy:
    - At least N characters — N is 시스템 › 보안 › 비밀번호 길이 (8 / 10 / 12, default 12)
    - At least 3 of the following: uppercase, lowercase, digits, special characters
    """
    errors: list[str] = []
    min_length = _min_length()
    if len(plain) < min_length:
        errors.append(f"Password must be at least {min_length} characters long.")

    type_count = sum([
        bool(re.search(r"[A-Z]", plain)),
        bool(re.search(r"[a-z]", plain)),
        bool(re.search(r"[0-9]", plain)),
        bool(re.search(r"[^A-Za-z0-9]", plain)),
    ])
    if type_count < 3:
        errors.append("Password must include at least 3 categories: uppercase letters, lowercase letters, digits, special characters.")

    return errors
